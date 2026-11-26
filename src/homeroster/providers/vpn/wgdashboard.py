"""WGDashboard (v4) for WireGuard: peers are the people's devices.

API (header ``wg-dashboard-apikey``):
  GET  /api/getWireguardConfigurationInfo?configurationName=wg0   -> data.configurationPeers
  POST /api/addPeers/wg0      {name, DNS, endpoint_allowed_ip, mtu, keepalive}   (IP picked automatically)
  GET  /api/downloadPeer/wg0?id=<public key>                        -> data.fileName / data.file
  POST /api/deletePeers/wg0   {peers: [public key, ...]}

Options (``vpn:`` section): url, api_key, interface (wg0), dns, allowed_ips, mtu (1420), keepalive (21).
"""

from __future__ import annotations

from typing import Any

from ...i18n import t
from ..base import HttpService, NotConfigured, ServiceError
from ..registry import register_vpn
from .base import Device, VpnProvider


@register_vpn("wgdashboard")
class WGDashboard(VpnProvider):
    label = "WGDashboard"
    client_app = "WireGuard"

    def __init__(self, options: dict[str, Any]):
        super().__init__(options)
        self.interface = str(options.get("interface") or "wg0")
        self.api_key = str(options.get("api_key") or "")
        self.http = HttpService(self.label, str(options.get("url") or ""), headers={"wg-dashboard-apikey": self.api_key})

    async def _call(self, method: str, path: str, **kwargs: Any) -> Any:
        if not self.api_key:
            raise NotConfigured(self.label)
        body = await self.http.request(method, path, expect=(200, 201, 400, 404, 409), **kwargs)
        if not isinstance(body, dict) or "status" not in body:
            raise ServiceError(self.label, "errors.unexpected_response")
        if not body.get("status"):
            raise ServiceError(self.label, "errors.raw", text=body.get("message") or t("errors.refused"))
        return body.get("data")

    async def version(self) -> str:
        try:
            version = await self._call("GET", "/api/getDashboardVersion")
            if version:
                await self.devices()
                return str(version)
        except ServiceError:
            pass
        await self.devices()  # at least make sure the key works
        return "v4"

    async def devices(self) -> list[Device]:
        data = await self._call("GET", "/api/getWireguardConfigurationInfo",
                                params={"configurationName": self.interface})
        out = []
        for peer in (data or {}).get("configurationPeers") or []:
            allowed = str(peer.get("allowed_ip") or "")
            out.append(Device(
                id=str(peer.get("id", "")),
                name=str(peer.get("name") or ""),
                ip=allowed.split(",")[0].split("/")[0].strip(),
                online=str(peer.get("status", "")).lower() == "running",
                extra={"latest_handshake": peer.get("latest_handshake", ""),
                       "rx": peer.get("total_receive", 0) or 0, "tx": peer.get("total_sent", 0) or 0},
            ))
        return out

    async def _by_name(self, name: str) -> Device | None:
        return next((d for d in await self.devices() if d.name == name), None)

    async def create_device(self, name: str) -> Device:
        if await self._by_name(name):
            raise ServiceError(self.label, "errors.device_exists", name=name)
        await self._call("POST", f"/api/addPeers/{self.interface}", json={
            "name": name,
            "DNS": self.options.get("dns") or "",
            "endpoint_allowed_ip": self.options.get("allowed_ips") or "0.0.0.0/0",
            "mtu": int(self.options.get("mtu") or 1420),
            "keepalive": int(self.options.get("keepalive") or 21),
            "allowed_ips_validation": True,
        })
        device = await self._by_name(name)
        if not device:
            raise ServiceError(self.label, "errors.device_missing")
        return device

    async def client_config(self, device_id: str) -> str:
        data = await self._call("GET", f"/api/downloadPeer/{self.interface}", params={"id": device_id})
        if not isinstance(data, dict) or not data.get("file"):
            raise ServiceError(self.label, "errors.config_missing")
        return str(data["file"])

    async def delete_devices(self, device_ids: list[str]) -> None:
        if device_ids:
            await self._call("POST", f"/api/deletePeers/{self.interface}", json={"peers": device_ids})
