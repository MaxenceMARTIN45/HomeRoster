"""Nginx Proxy Manager: one access list per site, rebuilt from the policy.

For every proxy host, HomeRoster maintains an access list named ``<acl_prefix><site>`` with one
``allow <ip>`` line per authorised device and a final ``deny all``, and attaches it to the host.

Options (``proxy:`` section): url (admin API, port 81), email, password, acl_prefix (``homeroster-``).
Use a dedicated NPM user with "Proxy Hosts" and "Access Lists" set to *Manage*, everything else *Hidden*.
"""

from __future__ import annotations

import time
from typing import Any

from ..base import HttpService, NotConfigured, ServiceError
from ..registry import register_proxy
from .base import AccessPolicy, AccessProxy, SyncResult

TOKEN_TTL = 3000  # NPM tokens last one hour; renew a bit earlier


@register_proxy("npm")
class NginxProxyManager(AccessProxy):
    label = "Nginx Proxy Manager"

    def __init__(self, options: dict[str, Any]):
        super().__init__(options)
        self.email = str(options.get("email") or "")
        self.password = str(options.get("password") or "")
        self.prefix = str(options.get("acl_prefix") or "homeroster-")
        self.http = HttpService(self.label, str(options.get("url") or ""))
        self._token: str | None = None
        self._token_ts = 0.0

    async def _login(self) -> None:
        if not (self.email and self.password):
            raise NotConfigured(self.label)
        if self._token and time.time() - self._token_ts < TOKEN_TTL:
            return
        self._token = None
        body = await self.http.request("POST", "/api/tokens", json={"identity": self.email, "secret": self.password})
        if not isinstance(body, dict) or not body.get("token"):
            raise ServiceError(self.label, "errors.login_refused")
        self._token, self._token_ts = body["token"], time.time()

    async def api(self, method: str, path: str, **kwargs: Any) -> Any:
        await self._login()
        return await self.http.request(method, "/api" + path, expect=(200, 201),
                                       headers={"Authorization": f"Bearer {self._token}"}, **kwargs)

    async def version(self) -> str:
        if not (self.email and self.password):
            raise NotConfigured(self.label)
        body = await self.http.request("GET", "/api/")
        await self.api("GET", "/nginx/proxy-hosts")  # make sure the credentials work
        if isinstance(body, dict) and isinstance(body.get("version"), dict):
            v = body["version"]
            return f"{v.get('major')}.{v.get('minor')}.{v.get('revision')}"
        return ""

    async def apply(self, policy: AccessPolicy, *, dry_run: bool = False) -> SyncResult:
        hosts = await self.api("GET", "/nginx/proxy-hosts") or []
        acls = {a["name"]: a for a in await self.api("GET", "/nginx/access-lists", params={"expand": "clients"}) or []}
        by_site: dict[str, list[dict]] = {}
        for host in hosts:
            if host.get("domain_names"):
                by_site.setdefault(policy.site_for_host(host["domain_names"][0]), []).append(host)

        result = SyncResult(sites=sorted(by_site))
        for site, site_hosts in sorted(by_site.items()):
            clients = [{"address": ip, "directive": "allow"} for ip in policy.ips_for(site)]
            clients.append({"address": "all", "directive": "deny"})
            wanted = [(c["address"], c["directive"]) for c in clients]
            name = self.prefix + site
            acl = acls.get(name)
            current = [(c["address"], c["directive"]) for c in (acl or {}).get("clients", [])]
            attached = acl is not None and all(h.get("access_list_id") == acl["id"] for h in site_hosts)
            if acl is not None and current == wanted and attached:
                result.unchanged.append(site)
                continue
            result.changed.append(site)
            if dry_run:
                continue
            body = {"name": name, "satisfy_any": False, "pass_auth": False, "items": [], "clients": clients}
            if acl is None:
                acl = await self.api("POST", "/nginx/access-lists", json=body)
            elif current != wanted:
                acl = await self.api("PUT", f"/nginx/access-lists/{acl['id']}", json=body)
            for host in site_hosts:
                if host.get("access_list_id") != acl["id"]:
                    await self.api("PUT", f"/nginx/proxy-hosts/{host['id']}", json={"access_list_id": acl["id"]})
        return result
