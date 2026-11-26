"""wg-easy (v15+) for WireGuard: clients are the people's devices.

API (HTTP Basic authentication with a wg-easy user without 2FA):
  GET    /api/client?page=N&pageSize=100     -> {total, clients: [{id, name, ipv4Address, latestHandshakeAt, ...}]}
  POST   /api/client               {name, expiresAt: null}        -> {success, clientId}  (IP picked automatically)
  GET    /api/client/<id>/configuration                           -> the client's .conf file
  DELETE /api/client/<id>
  GET    /api/information                                         -> {currentRelease, ...}

wg-easy checks the ``Origin`` header of writes when TRUSTED_PROXIES is set, so every request sends the origin
of ``public_url`` (default: ``url``).

Options (``vpn:`` section): url, username, password, public_url. The DNS, allowed IPs, MTU and keepalive pushed
to the devices are set in wg-easy itself (Admin panel > Config).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit

from ..apps.base import Person
from ..base import HttpService, NotConfigured, ServiceError
from ..registry import register_vpn
from .base import Device, VpnProvider

ONLINE_SECONDS = 180  # a WireGuard peer re-handshakes every two minutes while connected
PAGE_SIZE = 100


def _origin(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}" if parts.scheme and parts.netloc else ""


def _online(handshake: Any) -> bool:
    if not handshake:
        return False
    try:
        when = datetime.fromisoformat(str(handshake).replace("Z", "+00:00"))
    except ValueError:
        return False
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    return (datetime.now(UTC) - when).total_seconds() < ONLINE_SECONDS


@register_vpn("wg-easy")
class WgEasy(VpnProvider):
    label = "wg-easy"
    client_app = "WireGuard"

    def __init__(self, options: dict[str, Any]):
        super().__init__(options)
        self.username = str(options.get("username") or "")
        self.password = str(options.get("password") or "")
        url = str(options.get("url") or "")
        origin = _origin(str(options.get("public_url") or url))
        self.http = HttpService(self.label, url, auth=(self.username, self.password),
                                headers={"Origin": origin} if origin else None)

    async def _call(self, method: str, path: str, **kwargs: Any) -> Any:
        if not (self.username and self.password):
            raise NotConfigured(self.label)
        return await self.http.request(method, path, **kwargs)

    async def version(self) -> str:
        info = await self._call("GET", "/api/information")
        await self.devices()  # the information route is public: make sure the credentials work
        return str(info.get("currentRelease") or "") if isinstance(info, dict) else ""

    async def devices(self) -> list[Device]:
        clients: list[dict] = []
        page = 1
        while True:
            body = await self._call("GET", "/api/client", params={"page": page, "pageSize": PAGE_SIZE})
            if not isinstance(body, dict) or not isinstance(body.get("clients"), list):
                raise ServiceError(self.label, "errors.unexpected_response")
            clients += body["clients"]
            if not body["clients"] or len(clients) >= int(body.get("total") or 0):
                break
            page += 1
        return [Device(
            id=str(c.get("id", "")),
            name=str(c.get("name") or ""),
            ip=str(c.get("ipv4Address") or "").split("/")[0],
            online=bool(c.get("enabled", True)) and _online(c.get("latestHandshakeAt")),
            extra={"latest_handshake": c.get("latestHandshakeAt") or "",
                   "rx": c.get("transferRx") or 0, "tx": c.get("transferTx") or 0},
        ) for c in clients]

    async def create_device(self, name: str, owner: Person | None = None) -> Device:
        if any(d.name == name for d in await self.devices()):
            raise ServiceError(self.label, "errors.device_exists", name=name)
        body = await self._call("POST", "/api/client", json={"name": name, "expiresAt": None})
        client_id = str(body.get("clientId", "")) if isinstance(body, dict) else ""
        device = next((d for d in await self.devices() if d.id == client_id or (not client_id and d.name == name)), None)
        if not device:
            raise ServiceError(self.label, "errors.device_missing")
        return device

    async def client_config(self, device_id: str) -> str:
        text = await self._call("GET", f"/api/client/{device_id}/configuration")
        if not isinstance(text, str) or "[Interface]" not in text:
            raise ServiceError(self.label, "errors.config_missing")
        return text

    async def delete_devices(self, device_ids: list[str]) -> None:
        for device_id in device_ids:
            await self._call("DELETE", f"/api/client/{device_id}")
