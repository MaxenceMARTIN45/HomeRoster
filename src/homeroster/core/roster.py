"""The heart of HomeRoster: create, update and remove a person across the whole homelab.

Rules that matter:
- Access rights (reverse proxy allow-lists) are rebuilt from HomeRoster's database: it is the source of truth.
- A sync is NEVER applied when no admin device is found, so the admin can never lock themselves out.
- Every step is independent: if one app fails, the VPN, the other apps and the e-mail are still done.
- User passwords are neither stored nor logged.
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
import time
import tomllib
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from .. import db, logos
from ..config import Config
from ..i18n import localized, t
from ..naming import device_name, parse_device_name
from ..providers.apps.base import Account, AppConnector, Person
from ..providers.base import Health, ServiceError, error_message
from ..providers.mail.base import InlineImage, Mailer
from ..providers.proxy.base import AccessPolicy, AccessProxy
from ..providers.registry import (
    APP_CONNECTORS,
    MAIL_PROVIDERS,
    PROXY_PROVIDERS,
    VPN_PROVIDERS,
    load_plugins,
    lookup,
)
from ..providers.vpn.base import Device, VpnProvider
from ..templating import render

log = logging.getLogger("homeroster")

SYNC_ALERT_AFTER = 3  # consecutive failed syncs before e-mailing the admin
INVITATION_TTL = 7 * 24 * 3600  # an unused VPN invitation is forgotten after a week


def _order_by_dependency(apps: dict[str, AppConnector]) -> dict[str, AppConnector]:
    """Parents first (Jellyfin before Seerr), otherwise keep the configuration order."""
    for app in apps.values():
        if app.parent and app.parent not in apps:
            raise ValueError(f"app '{app.id}' uses '{app.parent}', which is not configured")
        if app.parent == app.id:
            raise ValueError(f"app '{app.id}' cannot use itself")

    def depth(app: AppConnector, seen: tuple[str, ...] = ()) -> int:
        if not app.parent:
            return 0
        if app.id in seen:
            raise ValueError(f"circular 'uses' between apps: {' -> '.join((*seen, app.id))}")
        return 1 + depth(apps[app.parent], (*seen, app.id))

    return dict(sorted(apps.items(), key=lambda item: depth(item[1])))


class Roster:
    def __init__(self, config: Config):
        load_plugins(config.plugins)
        self.config = config
        self.vpn: VpnProvider = lookup(VPN_PROVIDERS, "VPN provider", config.vpn.provider)(config.vpn.options)
        self.proxy: AccessProxy = lookup(PROXY_PROVIDERS, "proxy provider", config.proxy.provider)(config.proxy.options)
        self.mail: Mailer | None = (
            lookup(MAIL_PROVIDERS, "mail provider", config.mail.provider)(config.mail.options) if config.mail else None)
        apps = {app_id: lookup(APP_CONNECTORS, "app connector", options["connector"])(app_id, options, domain=config.domain)
                for app_id, options in config.apps.items()}
        self.apps: dict[str, AppConnector] = _order_by_dependency(apps)
        self._hosts = {app.hostname: app.id for app in self.apps.values()}
        self.proxy.managed_sites = list(self.apps)
        self._sync_lock = asyncio.Lock()

    # ================================================================== helpers

    def person(self, pid: int) -> Person:
        row = db.person_by_id(pid)
        if row is None:
            raise ServiceError("HomeRoster", "errors.person_missing")
        return self._person(row)

    @staticmethod
    def _person(row: sqlite3.Row) -> Person:
        return Person(id=row["id"], slug=row["slug"], name=row["display_name"], email=row["email"],
                      language=row["language"])

    def app(self, app_id: str) -> AppConnector:
        try:
            return self.apps[app_id]
        except KeyError:
            raise ServiceError(app_id, "errors.unknown_app") from None

    def children_of(self, app_id: str) -> list[str]:
        return [a.id for a in self.apps.values() if a.parent == app_id]

    def site_for_host(self, hostname: str) -> str:
        hostname = hostname.lower()
        return self._hosts.get(hostname) or hostname.split(".")[0]

    @staticmethod
    def _account(row: sqlite3.Row | None) -> Account | None:
        return Account(row["external_id"], row["login"]) if row else None

    # ================================================================== devices and access rights

    # ---- invitations (invitation VPNs: Tailscale, Headscale, NetBird, ZeroTier)

    @staticmethod
    def _invitations() -> dict[str, dict[str, Any]]:
        """Pending invitations, ``{id: {name, slug, invite, created}}``, kept until used, revoked or expired."""
        now = time.time()
        invites = db.kv_get("vpn_invitations", {}) or {}
        return {k: v for k, v in invites.items() if now - float(v.get("created", 0)) < INVITATION_TTL}

    @staticmethod
    def _save_invitations(invites: dict[str, dict[str, Any]]) -> None:
        db.kv_set("vpn_invitations", invites)

    def _remember(self, device: Device, slug: str) -> None:
        invites = self._invitations()
        invites[device.id] = {"name": device.name, "slug": slug, "invite": str(device.extra.get("invite", "")),
                              "created": time.time()}
        self._save_invitations(invites)

    async def devices_by_person(self) -> tuple[dict[str, list[Device]], list[Device]]:
        """``{slug: [devices]}`` (pending invitations included) plus the devices nobody owns."""
        devices = await self.vpn.devices()
        invites = self._invitations()
        if invites:
            pending = [Device(id=k, name=v["name"], ip="", owner=v["slug"], pending=True,
                              extra={"created": v["created"]}) for k, v in invites.items()]
            for invite_id in await self.vpn.claim(pending):
                invites.pop(invite_id, None)
            devices = await self.vpn.devices() if len(invites) != len(pending) else devices
        emails = {p["email"].lower(): p["slug"] for p in db.people() if p["email"]}
        by_person: dict[str, list[Device]] = {}
        unmatched: list[Device] = []
        for device in devices:
            owner = emails.get(device.owner.lower(), device.owner) if device.owner else ""
            parsed = parse_device_name(device.name)
            if owner:
                device.extra["device"] = parsed[1] if parsed and parsed[0] == owner else device.name
                by_person.setdefault(owner, []).append(device)
            elif parsed:
                device.extra["device"] = parsed[1]
                by_person.setdefault(parsed[0], []).append(device)
            else:
                unmatched.append(device)
        # An invitation is used once a device of that person shows up after it was sent.
        for invite_id, invite in list(invites.items()):
            used = any(float(d.extra.get("created") or 0) >= invite["created"] for d in by_person.get(invite["slug"], []))
            if used:
                invites.pop(invite_id)
                continue
            by_person.setdefault(invite["slug"], []).append(Device(
                id=invite_id, name=invite["name"], ip="", owner=invite["slug"], pending=True,
                extra={"device": parse_device_name(invite["name"])[1] if parse_device_name(invite["name"]) else invite["name"]}))
        if invites != (db.kv_get("vpn_invitations", {}) or {}):
            self._save_invitations(invites)
        return by_person, unmatched

    async def sync_access(self, *, dry_run: bool = False, reason: str = "auto") -> dict[str, Any]:
        """Rebuild the proxy allow-lists from the database. Returns a report (also stored for the Status page)."""
        async with self._sync_lock:
            report: dict[str, Any] = {"ts": time.time(), "ok": False, "reason": reason, "changed": [], "warnings": []}
            people = db.people()
            if not people:
                # Nobody configured yet: touch nothing and do not alert.
                report.update(error=t("sync.no_people"), skipped=True)
                if not dry_run:
                    db.kv_set("last_sync", report)
                return report
            try:
                by_person, unmatched = await self.devices_by_person()
                known = {p["slug"] for p in people}
                admin_ips: set[str] = set()
                allowed: dict[str, set[str]] = {}
                for person in people:
                    ips = {d.ip for d in by_person.get(person["slug"], []) if d.ip}
                    if person["is_admin"]:
                        admin_ips |= ips
                        continue
                    for app_id in db.grants_of(person["id"]):
                        allowed.setdefault(app_id, set()).update(ips)
                report["warnings"] += [t("sync.warn_unmatched", name=d.name) for d in unmatched]
                report["warnings"] += [t("sync.warn_unknown", slug=s) for s in sorted(set(by_person) - known)]
                if not admin_ips:
                    raise ServiceError(t("sync.safety"), "sync.no_admin_device")
                result = await self.proxy.apply(AccessPolicy(admin_ips, allowed, self.site_for_host), dry_run=dry_run)
                report.update(ok=True, changed=result.changed, sites=result.sites)
                report["warnings"] += list(dict.fromkeys(result.warnings))
            except Exception as e:
                report["error"] = error_message(e)
                log.warning("access sync failed: %s", report["error"])
            if not dry_run:
                await self._record_sync(report)
            return report

    async def _record_sync(self, report: dict[str, Any]) -> None:
        db.kv_set("last_sync", report)
        if report["ok"]:
            db.kv_set("sync_failures", 0)
            if report["changed"]:
                db.audit("sync", ", ".join(report["changed"]), report["reason"])
            return
        failures = db.kv_get("sync_failures", 0) + 1
        db.kv_set("sync_failures", failures)
        if failures == SYNC_ALERT_AFTER:
            await self.alert_admin(t("alerts.sync_failing"), report.get("error", ""))

    async def alert_admin(self, subject: str, detail: str) -> None:
        if not self.mail or not self.mail.configured():
            return
        try:
            html = render("emails/alert.html", subject=subject, detail=detail, owner=self.config.owner)
            await self.mail.send(self.mail.admin_address, f"[HomeRoster] {subject}", html,
                                 f"{subject}\n\n{detail}\n\n{t('alerts.footer')}")
        except ServiceError as e:
            log.warning("could not send the alert e-mail: %s", e)

    # ================================================================== app accounts

    async def ensure_account(self, pid: int, app_id: str, password: str | None) -> Account:
        """Return the person's account in ``app_id``, creating it (and its parent's account) when needed."""
        existing = self._account(db.accounts_of(pid).get(app_id))
        if existing:
            return existing
        app = self.app(app_id)
        person = self.person(pid)
        parent = await self.ensure_account(pid, app.parent, password) if app.parent else None
        app.check_new(person, password)
        account = await app.create(person, password if app.manages_password else None, parent)
        db.link_account(pid, app_id, account.external_id, account.login)
        await self._set_language(app, account, person.language)
        return account

    @staticmethod
    async def _set_language(app: AppConnector, account: Account, language: str) -> bool:
        """Best effort: a language the app refuses never fails the account it belongs to."""
        if not app.supports_language:
            return False
        try:
            await app.set_language(account, language)
            return True
        except Exception as e:
            log.info("%s: language not applied (%s)", app.label, error_message(e))
            return False

    async def apply_language(self, pid: int) -> list[str]:
        """Push the person's language to every linked account that stores one. Returns the apps updated."""
        person = self.person(pid)
        done = []
        for app_id, row in db.accounts_of(pid).items():
            app = self.apps.get(app_id)
            if app and await self._set_language(app, self._account(row), person.language):  # type: ignore[arg-type]
                done.append(app.label)
        return done

    async def create_account(self, pid: int, app_id: str, password: str | None) -> str:
        """Create (or re-enable) the person's account in one app. Returns a short message."""
        row = db.accounts_of(pid).get(app_id)
        if row:
            if row["status"] == "disabled":
                return await self.set_account_enabled(pid, app_id, True)
            return t("accounts.already_linked")
        await self.ensure_account(pid, app_id, password)
        app = self.app(app_id)
        return t("accounts.created_via", app=self.app(app.parent).label) if app.parent else t("accounts.created")

    async def set_account_enabled(self, pid: int, app_id: str, enabled: bool) -> str:
        row = db.accounts_of(pid).get(app_id)
        if not row:
            return t("accounts.none")
        app = self.app(app_id)
        if app.supports_disable:
            await app.set_enabled(self._account(row), enabled)  # type: ignore[arg-type]
        # Apps without a "disabled" state are blocked by the reverse proxy only.
        db.set_account_status(pid, app_id, "active" if enabled else "disabled")
        return t("accounts.enabled") if enabled else t("accounts.disabled")

    def _needed(self, app_id: str, grants: set[str]) -> bool:
        """An account stays enabled while its app, or an app signing in through it, is granted."""
        return app_id in grants or any(self._needed(child, grants) for child in self.children_of(app_id))

    async def grant(self, pid: int, app_id: str, password: str | None) -> dict[str, Any]:
        """Give an app: proxy access plus an account (created or re-enabled)."""
        app = self.app(app_id)
        out: dict[str, Any] = {"app": app_id, "ok": True, "message": ""}
        db.set_grants(pid, db.grants_of(pid) | {app_id})
        try:
            out["message"] = await self.create_account(pid, app_id, password)
            if app.parent:
                parent_row = db.accounts_of(pid).get(app.parent)
                if parent_row and parent_row["status"] == "disabled":
                    await self.set_account_enabled(pid, app.parent, True)
        except Exception as e:
            out.update(ok=False, message=error_message(e))
        sync = await self.sync_access(reason=f"grant {app_id}")
        db.audit("grant_added", f"{self.person(pid).slug} → {app_id}", out["message"], out["ok"] and sync["ok"])
        return {**out, "sync": sync}

    async def revoke(self, pid: int, app_id: str) -> dict[str, Any]:
        """Take an app away: proxy access is cut at once; the account is disabled, not deleted."""
        app = self.app(app_id)
        grants = db.grants_of(pid) - {app_id}
        db.set_grants(pid, grants)
        sync = await self.sync_access(reason=f"revoke {app_id}")
        notes = [t("accounts.access_cut")]
        accounts = db.accounts_of(pid)
        for target in filter(None, (app_id, app.parent)):
            row = accounts.get(target)
            if not row or row["status"] == "disabled" or self._needed(target, grants):
                continue
            try:
                await self.set_account_enabled(pid, target, False)
                if self.app(target).supports_disable:
                    notes.append(t("accounts.account_disabled", app=self.app(target).label))
            except Exception as e:
                notes.append(t("accounts.disable_failed", error=error_message(e)))
        message = ", ".join(notes)
        db.audit("grant_removed", f"{self.person(pid).slug} → {app_id}", message, sync["ok"])
        return {"app": app_id, "ok": sync["ok"], "message": message if sync["ok"] else sync.get("error", ""), "sync": sync}

    async def reset_password(self, pid: int, app_ids: list[str], password: str) -> list[dict[str, Any]]:
        """Overwrite the password in the selected apps. Apps signing in through a parent follow it."""
        accounts = db.accounts_of(pid)
        results = []
        for app_id in app_ids:
            app = self.app(app_id)
            row = accounts.get(app_id)
            if not app.manages_password:
                results.append({"app": app_id, "label": app.label, "ok": True,
                                "message": t("accounts.follows", app=self.app(app.parent).label if app.parent else "—")})
                continue
            if not row:
                results.append({"app": app_id, "label": app.label, "ok": False, "message": t("accounts.none")})
                continue
            try:
                await app.set_password(self._account(row), password)  # type: ignore[arg-type]
                results.append({"app": app_id, "label": app.label, "ok": True, "message": t("accounts.password_replaced")})
            except Exception as e:
                results.append({"app": app_id, "label": app.label, "ok": False, "message": error_message(e)})
        db.audit("password_reset", self.person(pid).slug,
                 ", ".join(f"{r['app']}:{'ok' if r['ok'] else 'error'}" for r in results), all(r["ok"] for r in results))
        return results

    async def link_existing(self, pid: int) -> list[str]:
        """Link the accounts that already exist in the apps (people created before HomeRoster)."""
        person = self.person(pid)
        found: list[str] = []
        for app in self.apps.values():  # parents first
            accounts = db.accounts_of(pid)
            if app.id in accounts:
                continue
            parent = self._account(accounts.get(app.parent)) if app.parent else None
            if app.parent and not parent:
                continue
            try:
                account = await app.find(person, parent)
            except ServiceError as e:
                found.append(f"{app.label} ({e.message})")
                continue
            if account:
                db.link_account(pid, app.id, account.external_id, account.login)
                found.append(app.label)
        db.audit("accounts_linked", person.slug, ", ".join(found) or "—")
        return found

    # ================================================================== devices

    async def add_device(self, pid: int, device: str) -> Device:
        person = self.person(pid)
        created = await self.vpn.create_device(device_name(person.slug, device), person)
        if created.pending:
            self._remember(created, person.slug)
        await self.sync_access(reason=f"device {device} added")
        db.audit("device_added", created.name, created.ip)
        return created

    async def person_device(self, pid: int, device_id: str) -> Device:
        person = self.person(pid)
        by_person, _ = await self.devices_by_person()
        device = next((d for d in by_person.get(person.slug, []) if d.id == device_id), None)
        if not device:
            raise ServiceError(self.vpn.label, "errors.device_not_owned")
        return device

    async def device_access(self, pid: int, device_id: str) -> tuple[str, str]:
        """Name and what the QR code holds: the WireGuard file, or the invitation of a pending device."""
        device = await self.person_device(pid, device_id)
        if device.pending:
            return device.name, self._invitations()[device_id]["invite"]
        return device.name, await self.vpn.client_config(device_id)

    async def delete_device(self, pid: int, device_id: str) -> str:
        device = await self.person_device(pid, device_id)
        await self._delete_devices([device])
        await self.sync_access(reason="device removed")
        db.audit("device_removed", device.name)
        return device.name

    async def _delete_devices(self, devices: list[Device]) -> None:
        invites = self._invitations()
        for device in devices:
            invites.pop(device.id, None)
        self._save_invitations(invites)
        await self.vpn.delete_devices([d.id for d in devices])

    # ================================================================== full creation (live job)

    def plan_steps(self, app_ids: set[str], send_email: bool) -> list[dict[str, Any]]:
        steps = [{"key": "vpn", "label": t("steps.vpn")}, {"key": "access", "label": t("steps.access")}]
        steps += [{"key": a.id, "label": t("steps.account", app=a.label)} for a in self.apps.values() if a.id in app_ids]
        if send_email:
            steps.append({"key": "email", "label": t("steps.email")})
        return [{**s, "status": "pending", "message": ""} for s in steps]

    async def run_creation(self, job_id: str, pid: int, password: str, app_ids: set[str], device: str,
                           send_email: bool) -> None:
        job = db.job_get(job_id)
        steps: list[dict[str, Any]] = job["steps"] if job else []
        result: dict[str, Any] = {"person_id": pid}
        person = self.person(pid)

        def mark(key: str, status: str, message: str = "") -> None:
            for s in steps:
                if s["key"] == key:
                    s.update(status=status, message=message)
            db.job_update(job_id, steps=steps)

        async def step(key: str, action: Callable[[], Awaitable[str]]) -> None:
            mark(key, "running")
            try:
                mark(key, "ok", await action() or "")
            except Exception as e:
                mark(key, "error", error_message(e))

        async def do_vpn() -> str:
            created = await self.vpn.create_device(device_name(person.slug, device), person)
            if created.pending:
                self._remember(created, person.slug)
            result.update(device_id=created.id, device_name=created.name, device_ip=created.ip)
            return f"{created.name} · {created.ip}" if created.ip else t("person.invitation_ready", name=created.name)

        async def do_access() -> str:
            report = await self.sync_access(reason=f"creation of {person.slug}")
            if not report["ok"]:
                raise ServiceError(t("steps.access"), "errors.raw", text=report.get("error", ""))
            labels = [a.label for a in self.apps.values() if a.id in app_ids]
            return t("steps.access_ok", apps=", ".join(labels) or "—")

        await step("vpn", do_vpn)
        await step("access", do_access)
        for app_id in [a for a in self.apps if a in app_ids]:
            await step(app_id, lambda app_id=app_id: self.create_account(pid, app_id, password))
        if send_email:
            await step("email", lambda: self.send_welcome(pid))
        ok = all(s["status"] == "ok" for s in steps)
        db.job_update(job_id, steps=steps, status="done" if ok else "partial", result=result)
        db.audit("person_created", person.slug, ", ".join(f"{s['key']}:{s['status']}" for s in steps), ok)

    # ================================================================== welcome e-mail

    async def logo(self, app_id: str | None = None) -> InlineImage | None:
        """The homelab's logo (``app_id`` None) or an app's, ready to embed; None when there is none."""
        if app_id is None:
            setting, fallback, cid = self.config.logo, None, "homelab"
            if not setting:
                return None
        else:
            app = self.app(app_id)
            setting, fallback, cid = app.logo, app.fallback_logo, f"app-{app_id}"
        data = await logos.load(setting, cache_dir=Path(self.config.data_dir) / "logos", fallback=fallback)
        return InlineImage(cid, data, logos.image_type(data) or "png") if data else None

    async def welcome_message(self, pid: int) -> tuple[str, str, str, list[InlineImage]]:
        """Subject, HTML, text and inline images of the welcome e-mail, in the person's language."""
        row = db.person_by_id(pid)
        if row is None:
            raise ServiceError("HomeRoster", "errors.person_missing")
        person, lang = self._person(row), row["language"]
        grants, accounts = db.grants_of(pid), db.accounts_of(pid)
        apps, images = [], []
        homelab_logo = await self.logo()
        if homelab_logo:
            images.append(homelab_logo)
        for app in self.apps.values():
            if app.id not in grants and not row["is_admin"]:
                continue
            account = accounts.get(app.id) or (accounts.get(app.parent) if app.parent else None)
            image = await self.logo(app.id)
            if image:
                images.append(image)
            apps.append({
                "id": app.id, "label": app.label, "description": localized(app.description, lang),
                "url": app.public_url, "login": account["login"] if account else app.default_login(person),
                "via": self.app(app.parent).label if app.parent else "", "color": app.color,
                "logo_cid": image.cid if image else "",
            })
        ctx = {"person": person, "apps": apps, "owner": self.config.owner, "domain": self.config.domain,
               "vpn_app": self.vpn.client_app, "vpn_invite": self.vpn.invite_only,
               "homelab_logo_cid": homelab_logo.cid if homelab_logo else "",
               "rtl": t("_meta.direction", lang) == "rtl"}
        subject = t("email.welcome.subject", lang, owner=self.config.owner)
        html = render("emails/welcome.html", lang=lang, **ctx)
        text = render("emails/welcome.txt", lang=lang, **ctx)
        return subject, html, text, images

    async def send_welcome(self, pid: int) -> str:
        person = self.person(pid)
        if not person.email:
            raise ServiceError(t("steps.email"), "errors.no_email")
        if not self.mail:
            raise ServiceError(t("steps.email"), "errors.not_configured")
        subject, html, text, images = await self.welcome_message(pid)
        await self.mail.send(person.email, subject, html, text, images)
        db.audit("welcome_sent", person.slug, person.email)
        return t("email.sent_to", email=person.email)

    # ================================================================== removal

    async def delete_person(self, pid: int, *, delete_accounts: bool) -> list[dict[str, Any]]:
        person = self.person(pid)
        results = []
        try:
            by_person, _ = await self.devices_by_person()
            own = by_person.get(person.slug, [])
            await self._delete_devices(own)
            results.append({"label": t("people.devices"), "ok": True, "message": t("people.devices_deleted", n=len(own))})
        except Exception as e:
            results.append({"label": t("people.devices"), "ok": False, "message": error_message(e)})
        if delete_accounts:
            accounts = db.accounts_of(pid)
            for app in reversed(list(self.apps.values())):  # children before parents
                row = accounts.get(app.id)
                if not row:
                    continue
                try:
                    await app.delete(self._account(row))  # type: ignore[arg-type]
                    results.append({"label": app.label, "ok": True, "message": t("accounts.deleted")})
                except Exception as e:
                    results.append({"label": app.label, "ok": False, "message": error_message(e)})
        db.run("DELETE FROM people WHERE id = ?", (pid,))
        sync = await self.sync_access(reason=f"removal of {person.slug}")
        results.append({"label": t("steps.access"), "ok": sync["ok"],
                        "message": t("sync.rebuilt") if sync["ok"] else sync.get("error", "")})
        db.audit("person_deleted", person.slug,
                 ", ".join(f"{r['label']}:{'ok' if r['ok'] else 'error'}" for r in results), all(r["ok"] for r in results))
        return results

    # ================================================================== bulk import

    def import_toml(self, text: str) -> dict[str, list[str]]:
        """Import people and grants: ``admins = ["alex"]`` and a ``[users]`` table ``bob = ["jellyfin"]``."""
        data = tomllib.loads(text)
        created: list[str] = []
        updated: list[str] = []
        ignored: list[str] = []
        admins = {str(a).strip().lower() for a in data.get("admins", [])}
        users = {str(k).strip().lower(): {str(a) for a in v} for k, v in (data.get("users") or {}).items()}
        for slug in sorted(admins | set(users)):
            apps = {a for a in users.get(slug, set()) if a in self.apps}
            ignored += [f"{slug}:{a}" for a in sorted(users.get(slug, set()) - apps)]
            existing = db.person_by_slug(slug)
            if existing:
                pid = existing["id"]
                db.run("UPDATE people SET is_admin = ?, updated_at = ? WHERE id = ?",
                       (1 if slug in admins else 0, time.time(), pid))
                updated.append(slug)
            else:
                pid = db.create_person(slug, slug.replace("_", " ").title(), language=self.config.language,
                                       is_admin=slug in admins)
                created.append(slug)
            db.set_grants(pid, apps)
        db.audit("import", "", f"created: {created} · updated: {updated} · ignored: {ignored}")
        return {"created": created, "updated": updated, "ignored": ignored}

    # ================================================================== status

    async def health_all(self) -> list[dict[str, Any]]:
        async def check(kind: str, label: str, probe: Callable[[], Awaitable[Health]]) -> dict[str, Any]:
            try:
                health = await probe()
            except Exception as e:
                health = Health(ok=False, message=error_message(e))
            return {"kind": kind, "label": label, "ok": health.ok, "version": health.version,
                    "message": health.message or (t("status.connected") if health.ok else "")}

        probes = [check("vpn", self.vpn.label, self.vpn.health), check("proxy", self.proxy.label, self.proxy.health)]
        probes += [check("app", app.label, app.health) for app in self.apps.values()]
        if self.mail:
            probes.append(check("mail", self.mail.label, self.mail.health))
        return list(await asyncio.gather(*probes))
