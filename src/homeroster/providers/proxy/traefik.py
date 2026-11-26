"""Traefik (v2 or v3): one IP allow-list middleware per site, written to a file Traefik watches.

Traefik's API is read-only, so HomeRoster works with its file provider:

* it reads the routers from the API (``GET /api/http/routers``) to learn which hostnames Traefik serves;
* it writes ``<middleware_prefix><site>`` middlewares (``ipAllowList``, ``ipWhiteList`` on v2) to ``file``,
  a YAML file inside a directory Traefik loads with ``--providers.file.directory`` (watched by default);
* you attach ``<middleware_prefix><site>@file`` to each router once (a Docker label, or the router's YAML).
  Until HomeRoster has written the middleware, Traefik keeps that router disabled: it fails closed.

Routers that serve a site without its middleware are reported as warnings on the Status page.

Options (``proxy:`` section): url (Traefik API, e.g. http://192.168.1.10:8080), username and password (if
the API sits behind basic auth), file (default /traefik/homeroster.yml), middleware_prefix (``homeroster-``).
"""

from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path
from typing import Any

import yaml

from ...i18n import t
from ..base import HttpService, ServiceError
from ..registry import register_proxy
from .base import AccessPolicy, AccessProxy, SyncResult

HOST_RULE = re.compile(r"\bHost\(([^)]*)\)")
QUOTED = re.compile(r"[`\"']([^`\"']+)[`\"']")
HEADER = "# Written by HomeRoster on every access sync. Do not edit: your changes would be overwritten.\n"


def hosts_of(rule: str) -> list[str]:
    """Hostnames matched by a router rule: Host(`a`), Host(`a`, `b`) (v2) or Host(`a`) || Host(`b`)."""
    return [h.lower() for group in HOST_RULE.findall(rule or "") for h in QUOTED.findall(group)]


@register_proxy("traefik")
class Traefik(AccessProxy):
    label = "Traefik"

    def __init__(self, options: dict[str, Any]):
        super().__init__(options)
        self.prefix = str(options.get("middleware_prefix") or "homeroster-")
        self.file = Path(str(options.get("file") or "/traefik/homeroster.yml"))
        username, password = str(options.get("username") or ""), str(options.get("password") or "")
        self.http = HttpService(self.label, str(options.get("url") or ""),
                                auth=(username, password) if username else None)

    async def version(self) -> str:
        body = await self.http.request("GET", "/api/version")
        if not self.file.parent.is_dir():
            raise ServiceError(self.label, "errors.file_write", path=str(self.file.parent), error="missing directory")
        return str(body.get("Version") or "") if isinstance(body, dict) else ""

    async def _routers(self) -> list[dict]:
        routers: list[dict] = []
        page = 1
        while page:
            body = await self.http.request("GET", "/api/http/routers", params={"page": page, "per_page": 100})
            if not isinstance(body, list):
                raise ServiceError(self.label, "errors.unexpected_response")
            routers += body
            page = page + 1 if len(body) == 100 else 0
        return [r for r in routers if r.get("provider") != "internal"]

    async def _middleware_kind(self) -> str:
        explicit = self.options.get("middleware_type")
        if explicit:
            return str(explicit)
        body = await self.http.request("GET", "/api/version")
        version = str(body.get("Version") or "") if isinstance(body, dict) else ""
        return "ipWhiteList" if version.startswith("2.") else "ipAllowList"

    def _current(self) -> dict[str, list[str]]:
        try:
            data = yaml.safe_load(self.file.read_text(encoding="utf-8")) or {}
        except FileNotFoundError:
            return {}
        except (OSError, yaml.YAMLError) as e:
            raise ServiceError(self.label, "errors.file_write", path=str(self.file), error=str(e)) from e
        out = {}
        for name, middleware in ((data.get("http") or {}).get("middlewares") or {}).items():
            rule = next(iter((middleware or {}).values()), None) or {}
            out[name] = list(rule.get("sourceRange") or [])
        return out

    def _write(self, kind: str, wanted: dict[str, list[str]]) -> None:
        document = {"http": {"middlewares": {name: {kind: {"sourceRange": ips}} for name, ips in sorted(wanted.items())}}}
        text = HEADER + yaml.safe_dump(document, sort_keys=False)
        try:
            # Write then rename, so Traefik never loads a half-written file.
            fd, tmp = tempfile.mkstemp(dir=self.file.parent, prefix=".homeroster-", suffix=".tmp")
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(text)
            os.chmod(tmp, 0o644)
            os.replace(tmp, self.file)
        except OSError as e:
            raise ServiceError(self.label, "errors.file_write", path=str(self.file), error=e.strerror or str(e)) from e

    async def apply(self, policy: AccessPolicy, *, dry_run: bool = False) -> SyncResult:
        routers = await self._routers()
        by_site: dict[str, list[tuple[dict, str]]] = {}
        for router in routers:
            for host in hosts_of(str(router.get("rule") or "")):
                by_site.setdefault(policy.site_for_host(host), []).append((router, host))

        result = SyncResult(sites=sorted(by_site))
        current = self._current()
        wanted = {self.prefix + site: policy.ips_for(site) for site in by_site}
        for site in sorted(by_site):
            name = self.prefix + site
            (result.unchanged if current.get(name) == wanted[name] else result.changed).append(site)
            for router, host in by_site[site]:
                if f"{name}@file" not in (router.get("middlewares") or []):
                    result.warnings.append(t("sync.warn_unprotected", host=host, router=str(router.get("name", "?")),
                                             middleware=f"{name}@file"))
        if not dry_run and (result.changed or set(current) != set(wanted)):
            self._write(await self._middleware_kind(), wanted)
        return result
