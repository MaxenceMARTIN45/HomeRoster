"""File-based reverse proxies: nginx, SWAG, Caddy and HAProxy.

These proxies have no API to manage access, so HomeRoster writes one small allow-list file per site into a
directory the proxy reads (mounted on ``/proxy`` in the container), and you include it once in each site:

========  ==========================================  ====================================================
provider  file written                                what you add to the site
========  ==========================================  ====================================================
nginx     ``homeroster-jellyfin.conf``                ``include /etc/nginx/homeroster/homeroster-jellyfin.conf;``
swag      same, in SWAG's ``/config/nginx/homeroster``  same include, in the ``server`` block of the proxy conf
caddy     ``homeroster-jellyfin.caddy``               ``import /etc/caddy/homeroster/homeroster-jellyfin.caddy``
haproxy   ``homeroster-jellyfin.lst`` (one IP a line)  ``acl hr_jellyfin src -f /etc/haproxy/homeroster/homeroster-jellyfin.lst``
                                                      ``http-request deny if { hdr(host) -i jellyfin.example.com } !hr_jellyfin``
========  ==========================================  ====================================================

The sites are the configured apps plus ``admins`` (admins only): include ``homeroster-admins`` in every other
site (dashboards, *arr apps, HomeRoster itself). A file is only rewritten when its content changes; the
proxy is then reloaded by the host watcher (``bash scripts/host-watcher.sh proxy``). HAProxy can instead be updated
live through its runtime API (``runtime_api: 127.0.0.1:9999``).

Options (``proxy:`` section): dir (default ``/proxy``), prefix (``homeroster-``); haproxy: runtime_api.
"""

from __future__ import annotations

import asyncio
import os
import tempfile
from pathlib import Path
from typing import Any, ClassVar

from ..base import ServiceError
from ..registry import register_proxy
from .base import AccessPolicy, AccessProxy, SyncResult

ADMINS_SITE = "admins"
HEADER = "Written by HomeRoster on every access sync: do not edit, your changes would be overwritten."


class FileProxy(AccessProxy):
    extension: ClassVar[str] = ".conf"

    def __init__(self, options: dict[str, Any]):
        super().__init__(options)
        self.dir = Path(str(options.get("dir") or "/proxy"))
        self.prefix = str(options.get("prefix") or "homeroster-")

    def render(self, ips: list[str]) -> str:
        raise NotImplementedError

    def path(self, site: str) -> Path:
        return self.dir / f"{self.prefix}{site}{self.extension}"

    async def version(self) -> str:
        if not self.dir.is_dir() or not os.access(self.dir, os.W_OK):
            raise ServiceError(self.label, "errors.file_write", path=str(self.dir), error="not a writable directory")
        return f"{len(list(self.dir.glob(self.prefix + '*' + self.extension)))} files"

    def _write(self, path: Path, text: str) -> None:
        try:
            fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".homeroster-", suffix=".tmp")
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(text)
            os.chmod(tmp, 0o644)
            os.replace(tmp, path)  # the proxy never reads a half-written file
        except OSError as e:
            raise ServiceError(self.label, "errors.file_write", path=str(path), error=e.strerror or str(e)) from e

    async def changed(self, site: str, ips: list[str]) -> None:
        """Hook run after a site's file changed (HAProxy pushes it to the running process)."""

    async def apply(self, policy: AccessPolicy, *, dry_run: bool = False) -> SyncResult:
        sites = sorted({*self.managed_sites, ADMINS_SITE})  # unmanaged sites include "admins"
        result = SyncResult(sites=sites)
        for site in sites:
            ips = policy.ips_for("" if site == ADMINS_SITE else site)  # "": nobody but the admins
            text = self.render(ips)
            path = self.path(site)
            try:
                current = path.read_text(encoding="utf-8")
            except OSError:
                current = None
            if current == text:
                result.unchanged.append(site)
                continue
            result.changed.append(site)
            if not dry_run:
                self._write(path, text)
                await self.changed(site, ips)
        return result


@register_proxy("nginx")
class Nginx(FileProxy):
    label = "nginx"

    def render(self, ips: list[str]) -> str:
        return f"# {HEADER}\n" + "".join(f"allow {ip};\n" for ip in ips) + "deny all;\n"


@register_proxy("swag")
class Swag(Nginx):
    """SWAG (LinuxServer's nginx): mount ``<swag config>/nginx/homeroster`` on ``/proxy``. With the
    ``linuxserver/mods:swag-auto-reload`` Docker mod, SWAG reloads by itself when the files change."""

    label = "SWAG"


@register_proxy("caddy")
class Caddy(FileProxy):
    label = "Caddy"
    extension = ".caddy"

    def render(self, ips: list[str]) -> str:
        return (f"# {HEADER}\n@homeroster_denied not remote_ip {' '.join(ips) or '127.0.0.1/32'}\n"
                "respond @homeroster_denied 403\n")


@register_proxy("haproxy")
class HAProxy(FileProxy):
    label = "HAProxy"
    extension = ".lst"

    def render(self, ips: list[str]) -> str:
        return f"# {HEADER}\n" + "".join(f"{ip}\n" for ip in ips)

    def _reference(self, site: str) -> str:
        """The ACL file path as HAProxy knows it (the host path, set with ``haproxy_dir``)."""
        base = str(self.options.get("haproxy_dir") or "/etc/haproxy/homeroster").rstrip("/")
        return f"{base}/{self.path(site).name}"

    async def _runtime(self, commands: list[str]) -> str:
        host, _, port = str(self.options.get("runtime_api")).rpartition(":")
        try:
            reader, writer = await asyncio.wait_for(asyncio.open_connection(host or "127.0.0.1", int(port)), 5)
        except (OSError, ValueError, TimeoutError) as e:
            raise ServiceError(self.label, "errors.unreachable") from e
        writer.write(("; ".join(commands) + "\n").encode())
        await writer.drain()
        answer = (await asyncio.wait_for(reader.read(), 5)).decode(errors="replace")
        writer.close()
        return answer

    async def version(self) -> str:
        files = await super().version()
        if self.options.get("runtime_api"):
            info = await self._runtime(["show info"])
            version = next((line.split(":", 1)[1].strip() for line in info.splitlines() if line.startswith("Version:")), "")
            return version or files
        return files

    async def changed(self, site: str, ips: list[str]) -> None:
        if not self.options.get("runtime_api"):
            return  # the host watcher reloads HAProxy
        reference = self._reference(site)
        answer = await self._runtime([f"clear acl {reference}", *(f"add acl {reference} {ip}" for ip in ips)])
        if "unknown" in answer.lower() or "not found" in answer.lower():
            raise ServiceError(self.label, "errors.raw", text=answer.strip()[:160])
