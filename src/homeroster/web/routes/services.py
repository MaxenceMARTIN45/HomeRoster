"""Settings > Services: change the VPN, the reverse proxy and the apps without editing homeroster.yaml.

Every change is tested first (a connection to the changed service) and saved to ``<data>/services.yaml``;
HomeRoster then switches to the new configuration at once, without a restart. Secrets are never sent back
to the browser: an empty secret field keeps the current value. Saving requires a fresh 2FA code.
"""

from __future__ import annotations

import copy
import re
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from ... import catalog, db
from ... import security as sec
from ...config import Config, ConfigError, save_services
from ...core.roster import Roster
from ...i18n import t
from ...providers.base import error_message
from ..deps import back, check_csrf, page, require_admin, roster

router = APIRouter()
PAGE = "/settings/services"


def _sections(r: Roster) -> dict[str, Any]:
    raw = copy.deepcopy(r.config.raw)
    return {"vpn": raw.get("vpn") or {}, "proxy": raw.get("proxy") or {}, "apps": raw.get("apps") or {}}


def _view(section: dict[str, Any], names: list[str]) -> list[dict[str, Any]]:
    out = []
    for f in catalog.fields(names):
        value = section.get(f["name"], "")
        if isinstance(value, list):
            value = ", ".join(str(v) for v in value)
        out.append({**f, "value": "" if f["secret"] else ("" if value is None else str(value)),
                    "is_set": value not in ("", None)})
    return out


@router.get(PAGE, response_class=HTMLResponse)
async def services_page(request: Request):
    s = require_admin(request)
    r = roster(request)
    sections = _sections(r)
    vpn, proxy = sections["vpn"], sections["proxy"]
    apps = []
    for app_id, options in sections["apps"].items():
        connector = str(options.get("connector") or "")
        known = connector in catalog.APPS
        apps.append({"id": app_id, "connector": connector, "label": r.apps[app_id].label if app_id in r.apps else app_id,
                     "editable": known,
                     "fields": _view(options, catalog.APPS[connector] + catalog.APP_COMMON) if known else []})
    return page(request, "services.html", s,
                vpn_provider=vpn.get("provider", ""), proxy_provider=proxy.get("provider", ""),
                vpn_forms={p: _view(vpn if p == vpn.get("provider") else {}, f) for p, f in catalog.VPN.items()},
                proxy_forms={p: _view(proxy if p == proxy.get("provider") else {}, f) for p, f in catalog.PROXY.items()},
                new_app_forms={c: _view({}, f + catalog.APP_COMMON) for c, f in catalog.APPS.items()},
                service_apps=apps)


def _merge(current: dict[str, Any], names: list[str], form: Any, prefix: str) -> dict[str, Any]:
    """The submitted options over the current ones; an empty secret keeps its value, an empty option is removed."""
    out = dict(current)
    for f in catalog.fields(names):
        value = str(form.get(f"{prefix}{f['name']}", "")).strip()
        if f["secret"] and not value:
            continue
        if not value:
            out.pop(f["name"], None)
        elif f["name"] == "libraries":
            out[f["name"]] = [v.strip() for v in value.split(",") if v.strip()]
        else:
            out[f["name"]] = value
    return out


async def _apply(request: Request, sections: dict[str, Any], kind: str, name: str, force: bool):
    """Build the new configuration, test what changed, then save and switch to it."""
    current = roster(request)
    raw = {"homeroster": {}, **sections}
    try:
        candidate = Config.from_dict(raw)
    except ConfigError as e:
        return back(PAGE, str(e), "error")
    fresh = Config(**{**current.config.__dict__, "vpn": candidate.vpn, "proxy": candidate.proxy,
                      "apps": candidate.apps, "raw": candidate.raw})
    try:
        new = Roster(fresh)
    except Exception as e:  # an unknown provider or an invalid option
        return back(PAGE, t("services.test_failed", error=str(e)), "error")
    target = {"vpn": new.vpn, "proxy": new.proxy}.get(kind) or new.apps.get(name)
    if target is not None:
        health = await target.health()
        if not health.ok and not force:
            return back(PAGE, t("services.test_failed", error=health.message), "error")
    save_services(sections)
    request.app.state.roster = new
    db.audit("services_changed", f"{kind} {name}".strip(), "forced" if force else "")
    try:
        await new.sync_access(reason="services changed")
    except Exception as e:  # reported on the Status page as well
        return back(PAGE, t("services.saved_sync_failed", error=error_message(e)), "error")
    return back(PAGE, t("services.saved"))


async def _checked_form(request: Request) -> tuple[Any, bool] | None:
    s = require_admin(request)
    form = await request.form()
    check_csrf(s, str(form.get("csrf", "")))
    if not sec.verify_totp(str(form.get("code", ""))):
        return None
    return form, bool(form.get("force"))


@router.post(PAGE + "/{kind}")
async def services_save(request: Request, kind: str):
    checked = await _checked_form(request)
    if checked is None:
        return back(PAGE, t("auth.code_wrong_nothing_changed"), "error")
    form, force = checked
    r = roster(request)
    sections = _sections(r)
    if kind in ("vpn", "proxy"):
        options = catalog.VPN if kind == "vpn" else catalog.PROXY
        provider = str(form.get("provider", ""))
        if provider not in options:
            return back(PAGE, t("services.unknown"), "error")
        current = sections[kind] if sections[kind].get("provider") == provider else {}
        sections[kind] = {"provider": provider, **_merge(current, options[provider], form, f"{provider}__")}
        sections[kind]["provider"] = provider
        return await _apply(request, sections, kind, provider, force)
    if kind == "app":
        app_id = str(form.get("app_id", "")).strip().lower()
        if not re.fullmatch(r"[a-z][a-z0-9_-]{0,31}", app_id):
            return back(PAGE, t("services.app_id_invalid"), "error")
        current = sections["apps"].get(app_id) or {}
        connector = str(form.get("connector") or current.get("connector") or "")
        if connector not in catalog.APPS:
            return back(PAGE, t("services.unknown"), "error")
        sections["apps"][app_id] = {"connector": connector,
                                    **_merge(current, catalog.APPS[connector] + catalog.APP_COMMON, form, f"{connector}__")}
        return await _apply(request, sections, "app", app_id, force)
    return back(PAGE, t("services.unknown"), "error")


@router.post(PAGE + "/app/{app_id}/delete")
async def services_delete_app(request: Request, app_id: str):
    checked = await _checked_form(request)
    if checked is None:
        return back(PAGE, t("auth.code_wrong_nothing_deleted"), "error")
    sections = _sections(roster(request))
    if app_id not in sections["apps"]:
        return back(PAGE, t("errors.unknown_app"), "error")
    del sections["apps"][app_id]
    for options in sections["apps"].values():  # an app reusing it ("uses") falls back to its own default
        if options.get("uses") == app_id:
            options.pop("uses")
    return await _apply(request, sections, "removed", app_id, force=True)
