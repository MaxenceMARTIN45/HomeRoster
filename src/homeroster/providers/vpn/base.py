"""The VPN contract: HomeRoster only needs to list, create and delete devices, and export a client config.

A device's VPN IP is what the reverse proxy allows, so it must be stable for the lifetime of the device.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, ClassVar

from ..base import Health, ServiceError


@dataclass
class Device:
    id: str
    name: str
    ip: str
    online: bool = False
    extra: dict[str, Any] = field(default_factory=dict)


class VpnProvider(ABC):
    provider_name: ClassVar[str] = ""
    label: ClassVar[str] = "VPN"
    #: Name of the client app people install, shown in the welcome e-mail and the QR code dialog.
    client_app: ClassVar[str] = "WireGuard"

    def __init__(self, options: dict[str, Any]):
        self.options = options

    @abstractmethod
    async def devices(self) -> list[Device]:
        """Every device known to the VPN, whatever its name."""

    @abstractmethod
    async def create_device(self, name: str) -> Device:
        """Create a device (the VPN picks its IP) and return it."""

    @abstractmethod
    async def delete_devices(self, device_ids: list[str]) -> None: ...

    @abstractmethod
    async def client_config(self, device_id: str) -> str:
        """The client configuration file, rendered as a QR code for the mobile app."""

    async def version(self) -> str:
        return ""

    async def health(self) -> Health:
        try:
            return Health(ok=True, message="", version=await self.version())
        except ServiceError as e:
            return Health.from_error(e)
