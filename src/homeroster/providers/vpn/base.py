"""The VPN contract: HomeRoster only needs to list, create and delete devices, and hand a client its access.

A device's VPN IP is what the reverse proxy allows, so it must be stable for the lifetime of the device.

Two families of VPN fit this contract:

* **Config-file VPNs** (WGDashboard, wg-easy, plain WireGuard, PiVPN): ``create_device`` creates the peer at
  once, and ``client_config`` returns the WireGuard file shown as a QR code.
* **Invitation VPNs** (Tailscale, Headscale, NetBird, ZeroTier): the device only exists once the person signs
  in on it. ``create_device`` returns a *pending* device carrying the invitation (a link, or a server address
  and a one-time key) in ``extra["invite"]``; HomeRoster stores it, shows it as a QR code and drops it once
  the real device appears. Such providers set ``invite_only = True`` and fill ``Device.owner`` (the person's
  slug or e-mail address), since the devices are named by the phones themselves.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, ClassVar

from ..apps.base import Person
from ..base import Health, ServiceError

INVITE_PREFIX = "invite:"


@dataclass
class Device:
    id: str
    name: str
    ip: str
    online: bool = False
    #: Slug or e-mail address of the owner, for VPNs that know it. Otherwise the name says it ("slug - device").
    owner: str = ""
    #: An invitation not used yet (invitation VPNs only).
    pending: bool = False
    extra: dict[str, Any] = field(default_factory=dict)


class VpnProvider(ABC):
    provider_name: ClassVar[str] = ""
    label: ClassVar[str] = "VPN"
    #: Name of the client app people install, shown in the welcome e-mail and the QR code dialog.
    client_app: ClassVar[str] = "WireGuard"
    #: True when people join with an invitation instead of a configuration file (see the module docstring).
    invite_only: ClassVar[bool] = False

    def __init__(self, options: dict[str, Any]):
        self.options = options

    @abstractmethod
    async def devices(self) -> list[Device]:
        """Every device known to the VPN, whatever its name. Pending invitations are not listed."""

    @abstractmethod
    async def create_device(self, name: str, owner: Person) -> Device:
        """Create a device (the VPN picks its IP) or, for invitation VPNs, an invitation for ``owner``."""

    @abstractmethod
    async def delete_devices(self, device_ids: list[str]) -> None:
        """Delete devices; ids starting with ``invite:`` are invitations to revoke (best effort)."""

    async def client_config(self, device_id: str) -> str:
        """The client configuration file, rendered as a QR code for the mobile app (config-file VPNs)."""
        raise ServiceError(self.label, "errors.config_missing")

    async def claim(self, invitations: list[Device]) -> dict[str, str]:
        """Invitation VPNs whose new devices cannot say who they belong to (ZeroTier) attach them here.

        Returns ``{invitation id: device id}`` for the invitations now used. Most providers need nothing.
        """
        return {}

    async def version(self) -> str:
        return ""

    async def health(self) -> Health:
        try:
            return Health(ok=True, message="", version=await self.version())
        except ServiceError as e:
            return Health.from_error(e)
