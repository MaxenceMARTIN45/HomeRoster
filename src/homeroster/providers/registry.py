"""Plugin registry: every provider registers itself under the name used in ``homeroster.yaml``.

    vpn:   {provider: wgdashboard}   -> class decorated with @register_vpn("wgdashboard")   (or wg-easy)
    proxy: {provider: npm}           -> class decorated with @register_proxy("npm")         (or traefik)
    apps:  {jellyfin: {connector: jellyfin}} -> class decorated with @register_app("jellyfin")

Third-party modules listed under ``homeroster.plugins`` are imported at startup, so a fork or an external
package can add providers without touching HomeRoster itself.
"""

from __future__ import annotations

import importlib
from collections.abc import Callable
from typing import TypeVar

T = TypeVar("T")

VPN_PROVIDERS: dict[str, type] = {}
PROXY_PROVIDERS: dict[str, type] = {}
MAIL_PROVIDERS: dict[str, type] = {}
APP_CONNECTORS: dict[str, type] = {}

BUILTIN_MODULES = (
    "homeroster.providers.vpn.wgdashboard",
    "homeroster.providers.vpn.wg_easy",
    "homeroster.providers.vpn.wireguard",
    "homeroster.providers.vpn.mesh",
    "homeroster.providers.proxy.npm",
    "homeroster.providers.proxy.traefik",
    "homeroster.providers.proxy.files",
    "homeroster.providers.proxy.zoraxy",
    "homeroster.providers.mail.smtp",
    "homeroster.providers.apps.jellyfin",
    "homeroster.providers.apps.seerr",
    "homeroster.providers.apps.immich",
    "homeroster.providers.apps.openwebui",
    "homeroster.providers.apps.media",
    "homeroster.providers.apps.home",
    "homeroster.providers.apps.rest",
)


def _register(table: dict[str, type], name: str) -> Callable[[type[T]], type[T]]:
    def decorator(cls: type[T]) -> type[T]:
        table[name] = cls
        cls.provider_name = name  # type: ignore[attr-defined]
        return cls
    return decorator


def register_vpn(name: str) -> Callable[[type[T]], type[T]]:
    return _register(VPN_PROVIDERS, name)


def register_proxy(name: str) -> Callable[[type[T]], type[T]]:
    return _register(PROXY_PROVIDERS, name)


def register_mail(name: str) -> Callable[[type[T]], type[T]]:
    return _register(MAIL_PROVIDERS, name)


def register_app(name: str) -> Callable[[type[T]], type[T]]:
    return _register(APP_CONNECTORS, name)


def load_plugins(extra: list[str] | tuple[str, ...] = ()) -> None:
    for module in (*BUILTIN_MODULES, *extra):
        importlib.import_module(module)


def lookup(table: dict[str, type], kind: str, name: str) -> type:
    try:
        return table[name]
    except KeyError:
        known = ", ".join(sorted(table)) or "none"
        raise LookupError(f"unknown {kind} '{name}' (available: {known})") from None
