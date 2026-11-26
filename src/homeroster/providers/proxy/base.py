"""The reverse proxy contract: turn "who may open which app" into IP allow-lists.

HomeRoster computes an :class:`AccessPolicy` from its database (the source of truth) and the VPN device
list. The proxy provider then makes every site it serves match that policy:

* a site that belongs to a managed app allows the VPN IPs of the people granted that app, plus the admins;
* any other site (dashboards, *arr apps, HomeRoster itself...) allows the admins only;
* everything else is denied.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, ClassVar

from ..base import Health, ServiceError


def ip_sort_key(ip: str) -> tuple:
    try:
        return (0, *(int(part) for part in ip.split(".")))
    except ValueError:
        return (1, ip)


@dataclass
class AccessPolicy:
    admin_ips: set[str]
    #: app id -> VPN IPs of the people granted that app (admins excluded).
    allowed: dict[str, set[str]]
    #: Maps a public hostname to a site key: the app id for a managed app, otherwise the first DNS label.
    site_for_host: Callable[[str], str]

    def ips_for(self, site: str) -> list[str]:
        return sorted(self.admin_ips | self.allowed.get(site, set()), key=ip_sort_key)


@dataclass
class SyncResult:
    changed: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)
    sites: list[str] = field(default_factory=list)
    #: Translated messages shown on the Status page (e.g. a site the proxy serves without protection).
    warnings: list[str] = field(default_factory=list)


class AccessProxy(ABC):
    provider_name: ClassVar[str] = ""
    label: ClassVar[str] = "Reverse proxy"

    def __init__(self, options: dict[str, Any]):
        self.options = options
        #: Site keys of the configured apps, set by HomeRoster. Proxies that cannot list the sites they serve
        #: (the file-based ones) write one allow-list per managed site, plus "admins" for everything else.
        self.managed_sites: list[str] = []

    @abstractmethod
    async def apply(self, policy: AccessPolicy, *, dry_run: bool = False) -> SyncResult:
        """Make the proxy match ``policy``. Must be idempotent: a second call changes nothing."""

    async def version(self) -> str:
        return ""

    async def health(self) -> Health:
        try:
            return Health(ok=True, message="", version=await self.version())
        except ServiceError as e:
            return Health.from_error(e)
