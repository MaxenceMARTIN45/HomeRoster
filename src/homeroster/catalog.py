"""What the Services page offers: every built-in provider and connector, with the options it asks for.

An option ending in ``*`` is required. Options whose name contains key, token, password or secret are
secrets: the page never shows their value, and an empty field keeps the current one.
"""

from __future__ import annotations

import re

VPN: dict[str, list[str]] = {
    "wgdashboard": ["url*", "api_key*", "interface", "dns", "allowed_ips", "mtu", "keepalive"],
    "wg-easy": ["url*", "username*", "password*", "public_url"],
    "wireguard": ["config_file*", "endpoint*", "dns", "allowed_ips"],
    "pivpn": ["config_file*", "endpoint*", "dns", "allowed_ips"],
    "tailscale": ["api_key*", "tailnet"],
    "headscale": ["url*", "api_key*", "server_url"],
    "netbird": ["url*", "token*", "management_url"],
    "zerotier": ["token*", "network_id*", "url"],
}

PROXY: dict[str, list[str]] = {
    "npm": ["url*", "email*", "password*", "acl_prefix"],
    "traefik": ["url*", "file*", "username", "password", "middleware_prefix"],
    "caddy": ["dir*"],
    "nginx": ["dir*"],
    "swag": ["dir*"],
    "haproxy": ["dir*", "runtime_api", "haproxy_dir"],
    "zoraxy": ["url*", "username*", "password*"],
}

APPS: dict[str, list[str]] = {
    "jellyfin": ["url*", "api_key*"],
    "plex": ["url*", "token*", "libraries"],
    "emby": ["url*", "api_key*"],
    "seerr": ["url*", "api_key*", "uses"],
    "immich": ["url*", "api_key*"],
    "openwebui": ["url*", "api_key*"],
    "audiobookshelf": ["url*", "api_key*"],
    "navidrome": ["url*", "username*", "password*"],
    "vaultwarden": ["url*", "admin_token*"],
    "homeassistant": ["url*", "token*", "local_only"],
}
#: Asked for every app, after its own options.
APP_COMMON = ["hostname", "label"]

SECRET = re.compile(r"key|token|password|secret", re.IGNORECASE)


def fields(names: list[str]) -> list[dict[str, object]]:
    return [{"name": n.rstrip("*"), "required": n.endswith("*"), "secret": bool(SECRET.search(n))} for n in names]
