"""Zoraxy: one access rule per site, with an IP whitelist, attached to the site's proxy rules.

Zoraxy's admin API uses a session cookie and a CSRF token (read from the login page), like its web UI:

  GET  /login.html                                   -> CSRF cookie + token (meta "zoraxy.csrf.Token")
  POST /api/auth/login        username, password
  POST /api/proxy/list        type=host              -> [{RootOrMatchingDomain, MatchingDomainAlias, AccessFilterUUID}]
  GET  /api/access/list                              -> [{ID, Name, WhitelistEnabled}]
  POST /api/access/create     name, desc, trustProxy
  GET  /api/whitelist/list    type=ip, id            -> [{IP}]
  POST /api/whitelist/ip/add  | /ip/remove  ip, id, comment
  POST /api/whitelist/enable  id, enable=true
  POST /api/access/attach     id, host

Options (``proxy:`` section): url (admin UI, port 8000 by default), username, password, rule_prefix
(``homeroster-``). Use a dedicated Zoraxy admin account.
"""

from __future__ import annotations

import re
from typing import Any

import httpx

from ..base import TIMEOUT, HttpService, NotConfigured, ServiceError
from ..registry import register_proxy
from .base import AccessPolicy, AccessProxy, SyncResult

TOKEN = re.compile(r'name="zoraxy\.csrf\.Token"\s+content="([^"]+)"|content="([^"]+)"\s+name="zoraxy\.csrf\.Token"')


class ZoraxySession:
    def __init__(self, proxy: Zoraxy):
        self.proxy = proxy
        self.client = httpx.AsyncClient(base_url=proxy.url, timeout=TIMEOUT, transport=_transport(),
                                        follow_redirects=True)
        self.token = ""

    async def __aenter__(self) -> ZoraxySession:
        try:
            page = await self.client.get("/login.html")
            match = TOKEN.search(page.text)
            self.token = (match.group(1) or match.group(2)) if match else ""
            await self.post("/api/auth/login", {"username": self.proxy.username, "password": self.proxy.password},
                            error_key="errors.login_refused")
        except BaseException:
            await self.client.aclose()
            raise
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.client.aclose()

    async def _send(self, method: str, path: str, **kwargs: Any) -> Any:
        try:
            response = await self.client.request(method, path, headers={"X-CSRF-Token": self.token}, **kwargs)
        except httpx.ConnectError as e:
            raise ServiceError(self.proxy.label, "errors.unreachable") from e
        except httpx.HTTPError as e:
            raise ServiceError(self.proxy.label, "errors.network", kind=e.__class__.__name__) from e
        if response.status_code != 200:
            raise ServiceError(self.proxy.label, "errors.http", detail=HttpService.explain(response))
        try:
            body = response.json()
        except ValueError:
            return response.text
        if isinstance(body, dict) and body.get("error"):
            raise ServiceError(self.proxy.label, "errors.raw", text=str(body["error"]))
        return body

    async def post(self, path: str, form: dict[str, Any], error_key: str = "") -> Any:
        try:
            return await self._send("POST", path, data=form)
        except ServiceError as e:
            if error_key:
                raise ServiceError(self.proxy.label, error_key) from e
            raise

    async def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        return await self._send("GET", path, params=params)


def _transport() -> httpx.AsyncBaseTransport | None:
    from .. import base  # read at call time: the tests swap it

    return base.TRANSPORT


@register_proxy("zoraxy")
class Zoraxy(AccessProxy):
    label = "Zoraxy"

    def __init__(self, options: dict[str, Any]):
        super().__init__(options)
        self.url = str(options.get("url") or "").rstrip("/")
        self.username = str(options.get("username") or "")
        self.password = str(options.get("password") or "")
        self.prefix = str(options.get("rule_prefix") or "homeroster-")

    def session(self) -> ZoraxySession:
        if not (self.url and self.username and self.password):
            raise NotConfigured(self.label)
        return ZoraxySession(self)

    async def version(self) -> str:
        async with self.session() as zx:
            await zx.post("/api/proxy/list", {"type": "host"})
        return ""

    async def apply(self, policy: AccessPolicy, *, dry_run: bool = False) -> SyncResult:
        async with self.session() as zx:
            hosts = await zx.post("/api/proxy/list", {"type": "host"}) or []
            by_site: dict[str, list[dict]] = {}
            for host in hosts:
                domain = str(host.get("RootOrMatchingDomain") or "")
                if domain:
                    by_site.setdefault(policy.site_for_host(domain.split(":")[0]), []).append(host)
            rules = {r.get("Name"): r for r in await zx.get("/api/access/list") or []}
            result = SyncResult(sites=sorted(by_site))
            for site, site_hosts in sorted(by_site.items()):
                name = self.prefix + site
                rule = rules.get(name)
                wanted = policy.ips_for(site)
                current = [] if rule is None else [str(e.get("IP")) for e in
                                                   await zx.get("/api/whitelist/list", {"type": "ip", "id": rule["ID"]}) or []]
                attached = rule is not None and all(h.get("AccessFilterUUID") == rule["ID"] for h in site_hosts)
                if rule is not None and sorted(current) == sorted(wanted) and rule.get("WhitelistEnabled") and attached:
                    result.unchanged.append(site)
                    continue
                result.changed.append(site)
                if dry_run:
                    continue
                if rule is None:
                    await zx.post("/api/access/create", {"name": name, "desc": "Managed by HomeRoster", "trustProxy": "false"})
                    rule = next((r for r in await zx.get("/api/access/list") or [] if r.get("Name") == name), None)
                    if rule is None:
                        raise ServiceError(self.label, "errors.unexpected_response")
                for ip in set(wanted) - set(current):
                    await zx.post("/api/whitelist/ip/add", {"ip": ip, "id": rule["ID"], "comment": "HomeRoster"})
                for ip in set(current) - set(wanted):
                    await zx.post("/api/whitelist/ip/remove", {"ip": ip, "id": rule["ID"]})
                if not rule.get("WhitelistEnabled"):
                    await zx.post("/api/whitelist/enable", {"id": rule["ID"], "enable": "true"})
                for host in site_hosts:
                    if host.get("AccessFilterUUID") != rule["ID"]:
                        await zx.post("/api/access/attach", {"id": rule["ID"], "host": host["RootOrMatchingDomain"]})
            return result
