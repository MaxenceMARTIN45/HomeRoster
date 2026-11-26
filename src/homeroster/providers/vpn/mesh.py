"""Invitation VPNs (mesh networks): each person's devices join with an invitation, then get a stable IP.

All four are *invitation* providers (see ``vpn/base.py``): ``create_device`` returns a pending device whose
``extra["invite"]`` is what the QR code shows, and every device reports its ``owner``.

* ``headscale``: one Headscale user per person (named after their HomeRoster id), a single-use pre-auth key
  per invitation. In the Tailscale app: custom coordination server, then the key.
* ``tailscale``: the person is invited to your tailnet by e-mail (``user-invites``) and signs in with their own
  account; devices belong to them through that e-mail address, so it must match their HomeRoster e-mail.
* ``netbird``: one group ``homeroster-<id>`` per person, a one-off setup key per invitation.
* ``zerotier``: people join your network ID; HomeRoster authorizes and names the first new member that shows
  up after the invitation (within an hour), so invite one device at a time.

The reverse proxy must see each device's own VPN IP: run it on the mesh itself (not behind a subnet router
that masquerades traffic).
"""

from __future__ import annotations

import ipaddress
import time
from datetime import UTC, datetime, timedelta
from typing import Any

from ...i18n import t
from ..apps.base import Person
from ..base import HttpService, NotConfigured, ServiceError
from ..registry import register_vpn
from .base import INVITE_PREFIX, Device, VpnProvider

ONLINE_SECONDS = 300


def _timestamp(value: Any) -> float:
    """RFC 3339 string, or milliseconds since 1970 (ZeroTier), to a Unix time; 0 when unknown."""
    if isinstance(value, int | float):
        return float(value) / 1000 if value > 10**11 else float(value)
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def _ipv4(addresses: list[str] | None) -> str:
    for address in addresses or []:
        try:
            if ipaddress.ip_address(address.split("/")[0]).version == 4:
                return address.split("/")[0]
        except ValueError:
            continue
    return ""


def _invite_id(owner: Person, ref: str) -> str:
    return f"{INVITE_PREFIX}{owner.slug}:{ref}"


def _ref(invite_id: str) -> str:
    return invite_id.rsplit(":", 1)[-1]


class MeshVpn(VpnProvider):
    invite_only = True

    def __init__(self, options: dict[str, Any], http: HttpService, *secrets: str):
        super().__init__(options)
        self.http = http
        self._secrets = secrets

    async def _call(self, method: str, path: str, **kwargs: Any) -> Any:
        if not all(self._secrets):
            raise NotConfigured(self.label)
        return await self.http.request(method, path, expect=(200, 201, 204), **kwargs)


# =========================================================================================== Headscale


@register_vpn("headscale")
class Headscale(MeshVpn):
    """Options: url (API), api_key (``headscale apikeys create``), server_url (what clients use, default url)."""

    label = "Headscale"
    client_app = "Tailscale"

    def __init__(self, options: dict[str, Any]):
        key = str(options.get("api_key") or "")
        super().__init__(options, HttpService("Headscale", str(options.get("url") or ""),
                                              headers={"Authorization": f"Bearer {key}"}), key)
        self.server_url = str(options.get("server_url") or options.get("url") or "").rstrip("/")

    async def version(self) -> str:
        await self._call("GET", "/api/v1/user")
        return "v1 API"

    async def devices(self) -> list[Device]:
        body = await self._call("GET", "/api/v1/node")
        return [Device(id=str(n.get("id")), name=str(n.get("givenName") or n.get("name") or ""),
                       ip=_ipv4(n.get("ipAddresses")), online=bool(n.get("online")),
                       owner=str((n.get("user") or {}).get("name") or ""),
                       extra={"created": _timestamp(n.get("createdAt"))})
                for n in (body or {}).get("nodes") or []]

    async def _user_id(self, owner: Person) -> str:
        users = (await self._call("GET", "/api/v1/user", params={"name": owner.slug}) or {}).get("users") or []
        user = next((u for u in users if u.get("name") == owner.slug), None)
        if not user:
            user = (await self._call("POST", "/api/v1/user", json={"name": owner.slug, "displayName": owner.name,
                                                                   "email": owner.email}) or {}).get("user") or {}
        if not user.get("id"):
            raise ServiceError(self.label, "errors.unexpected_response")
        return str(user["id"])

    async def create_device(self, name: str, owner: Person) -> Device:
        expiry = datetime.now(UTC) + timedelta(hours=int(self.options.get("invite_hours") or 48))
        body = await self._call("POST", "/api/v1/preauthkey", json={
            "user": await self._user_id(owner), "reusable": False, "ephemeral": False,
            "expiration": expiry.isoformat().replace("+00:00", "Z")})
        key = (body or {}).get("preAuthKey") or {}
        if not key.get("key"):
            raise ServiceError(self.label, "errors.unexpected_response")
        invite = t("vpn.invite_server_key", server=self.server_url, secret=key["key"])
        return Device(id=_invite_id(owner, str(key.get("id"))), name=name, ip="", owner=owner.slug, pending=True,
                      extra={"invite": invite})

    async def delete_devices(self, device_ids: list[str]) -> None:
        for device_id in device_ids:
            if device_id.startswith(INVITE_PREFIX):
                try:
                    await self._call("POST", "/api/v1/preauthkey/expire", json={"id": _ref(device_id)})
                except ServiceError:
                    pass  # an expired or used key is harmless
            else:
                await self._call("DELETE", f"/api/v1/node/{device_id}")


# =========================================================================================== Tailscale


@register_vpn("tailscale")
class Tailscale(MeshVpn):
    """Options: api_key (an API access token, ``tskey-api-...``), tailnet (default ``-``: the key's tailnet)."""

    label = "Tailscale"
    client_app = "Tailscale"

    def __init__(self, options: dict[str, Any]):
        key = str(options.get("api_key") or "")
        super().__init__(options, HttpService("Tailscale", str(options.get("url") or "https://api.tailscale.com"),
                                              headers={"Authorization": f"Bearer {key}"}), key)
        self.tailnet = str(options.get("tailnet") or "-")

    async def version(self) -> str:
        await self.devices()
        return "API v2"

    async def devices(self) -> list[Device]:
        body = await self._call("GET", f"/api/v2/tailnet/{self.tailnet}/devices", params={"fields": "all"})
        out = []
        for d in (body or {}).get("devices") or []:
            seen = _timestamp(d.get("lastSeen"))
            out.append(Device(id=str(d.get("id")), name=str(d.get("hostname") or d.get("name") or "").split(".")[0],
                              ip=_ipv4(d.get("addresses")), owner=str(d.get("user") or ""),
                              online=bool(d.get("connectedToControl")) or time.time() - seen < ONLINE_SECONDS,
                              extra={"created": _timestamp(d.get("created"))}))
        return out

    async def create_device(self, name: str, owner: Person) -> Device:
        if not owner.email:
            raise ServiceError(self.label, "errors.invite_needs_email")
        body = await self._call("POST", f"/api/v2/tailnet/{self.tailnet}/user-invites",
                                json=[{"role": "member", "email": owner.email}])
        invite = (body or [{}])[0] if isinstance(body, list) else {}
        url = str(invite.get("inviteUrl") or "")
        if not url:
            raise ServiceError(self.label, "errors.unexpected_response")
        return Device(id=_invite_id(owner, str(invite.get("id"))), name=name, ip="", owner=owner.slug, pending=True,
                      extra={"invite": url})

    async def delete_devices(self, device_ids: list[str]) -> None:
        for device_id in device_ids:
            if device_id.startswith(INVITE_PREFIX):
                try:
                    await self._call("DELETE", f"/api/v2/user-invites/{_ref(device_id)}")
                except ServiceError:
                    pass  # already accepted or expired
            else:
                await self._call("DELETE", f"/api/v2/device/{device_id}")


# ============================================================================================= NetBird


@register_vpn("netbird")
class NetBird(MeshVpn):
    """Options: url (``https://api.netbird.io`` or your management server), token (a personal access token),
    management_url (what clients use, default url), group_prefix (``homeroster-``)."""

    label = "NetBird"
    client_app = "NetBird"

    def __init__(self, options: dict[str, Any]):
        token = str(options.get("token") or "")
        url = str(options.get("url") or "https://api.netbird.io")
        super().__init__(options, HttpService("NetBird", url, headers={"Authorization": f"Token {token}"}), token)
        self.prefix = str(options.get("group_prefix") or "homeroster-")
        self.management_url = str(options.get("management_url") or url).rstrip("/")

    async def version(self) -> str:
        await self._call("GET", "/api/groups")
        return "REST API"

    async def devices(self) -> list[Device]:
        out = []
        for p in await self._call("GET", "/api/peers") or []:
            groups = [str(g.get("name") or "") for g in p.get("groups") or []]
            owner = next((g[len(self.prefix):] for g in groups if g.startswith(self.prefix)), "")
            out.append(Device(id=str(p.get("id")), name=str(p.get("name") or ""), ip=str(p.get("ip") or ""),
                              online=bool(p.get("connected")), owner=owner,
                              extra={"created": _timestamp(p.get("created_at"))}))
        return out

    async def _group_id(self, slug: str) -> str:
        name = self.prefix + slug
        group = next((g for g in await self._call("GET", "/api/groups") or [] if g.get("name") == name), None)
        if not group:
            group = await self._call("POST", "/api/groups", json={"name": name, "peers": []}) or {}
        if not group.get("id"):
            raise ServiceError(self.label, "errors.unexpected_response")
        return str(group["id"])

    async def create_device(self, name: str, owner: Person) -> Device:
        key = await self._call("POST", "/api/setup-keys", json={
            "name": name, "type": "one-off", "usage_limit": 1, "ephemeral": False,
            "expires_in": int(self.options.get("invite_hours") or 48) * 3600,
            "auto_groups": [await self._group_id(owner.slug)]}) or {}
        if not key.get("key"):
            raise ServiceError(self.label, "errors.unexpected_response")
        invite = t("vpn.invite_server_key", server=self.management_url, secret=key["key"])
        return Device(id=_invite_id(owner, str(key.get("id"))), name=name, ip="", owner=owner.slug, pending=True,
                      extra={"invite": invite})

    async def delete_devices(self, device_ids: list[str]) -> None:
        for device_id in device_ids:
            if device_id.startswith(INVITE_PREFIX):
                try:
                    await self._call("PUT", f"/api/setup-keys/{_ref(device_id)}", json={"revoked": True})
                except ServiceError:
                    pass
            else:
                await self._call("DELETE", f"/api/peers/{device_id}")


# ============================================================================================ ZeroTier


@register_vpn("zerotier")
class ZeroTier(MeshVpn):
    """Options: token (ZeroTier Central API token), network_id, url (default ``https://api.zerotier.com/api/v1``;
    a self-hosted controller with the same API, such as ztnet, also works)."""

    label = "ZeroTier"
    client_app = "ZeroTier One"
    CLAIM_WINDOW = 3600
    TAG = "homeroster:"

    def __init__(self, options: dict[str, Any]):
        token = str(options.get("token") or "")
        self.network = str(options.get("network_id") or "")
        super().__init__(options, HttpService("ZeroTier", str(options.get("url") or "https://api.zerotier.com/api/v1"),
                                              headers={"Authorization": f"token {token}"}), token, self.network)

    async def version(self) -> str:
        body = await self._call("GET", f"/network/{self.network}")
        return str((body or {}).get("config", {}).get("name") or self.network)

    async def _members(self) -> list[dict]:
        return await self._call("GET", f"/network/{self.network}/member") or []

    async def devices(self) -> list[Device]:
        out = []
        for m in await self._members():
            config = m.get("config") or {}
            if not config.get("authorized"):
                continue
            description = str(m.get("description") or "")
            out.append(Device(id=str(m.get("nodeId") or ""), name=str(m.get("name") or ""),
                              ip=_ipv4(config.get("ipAssignments")),
                              online=time.time() - _timestamp(m.get("lastSeen") or m.get("lastOnline") or 0) < ONLINE_SECONDS,
                              owner=description[len(self.TAG):] if description.startswith(self.TAG) else "",
                              extra={"created": _timestamp(m.get("creationTime") or config.get("creationTime") or 0)}))
        return out

    async def create_device(self, name: str, owner: Person) -> Device:
        if not self.network:
            raise NotConfigured(self.label)
        invite = t("vpn.invite_network", network=self.network)
        return Device(id=_invite_id(owner, str(int(time.time() * 1000))), name=name, ip="", owner=owner.slug,
                      pending=True, extra={"invite": invite})

    async def claim(self, invitations: list[Device]) -> dict[str, str]:
        """Authorize the new members, oldest first, for the invitations sent before they joined."""
        waiting = sorted((m for m in await self._members()
                          if not (m.get("config") or {}).get("authorized") and not m.get("description")),
                         key=lambda m: _timestamp(m.get("creationTime") or 0))
        claimed: dict[str, str] = {}
        for invitation in sorted(invitations, key=lambda d: float(d.extra.get("created") or 0)):
            sent = float(invitation.extra.get("created") or 0)
            member = next((m for m in waiting
                           if sent <= _timestamp(m.get("creationTime") or 0) <= sent + self.CLAIM_WINDOW), None)
            if not member:
                continue
            waiting.remove(member)
            node = str(member.get("nodeId"))
            await self._call("POST", f"/network/{self.network}/member/{node}", json={
                "name": invitation.name, "description": self.TAG + invitation.owner, "config": {"authorized": True}})
            claimed[invitation.id] = node
        return claimed

    async def delete_devices(self, device_ids: list[str]) -> None:
        for device_id in device_ids:
            if not device_id.startswith(INVITE_PREFIX):
                await self._call("DELETE", f"/network/{self.network}/member/{device_id}")
