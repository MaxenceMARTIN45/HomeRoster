"""Declarative REST connector: add an app with YAML only, no Python.

Describe the app's user-management API and HomeRoster does the rest. Example (Nextcloud, OCS API)::

    apps:
      nextcloud:
        connector: rest
        label: Nextcloud
        description: {en: Files, calendar and contacts, fr: Fichiers, agenda et contacts}
        icon: cloud
        color: sky
        url: http://nextcloud.lan
        login: username               # what people type to sign in: username | email
        username: "{slug}"            # user name sent to the API (default "{slug}")
        auth: {type: basic, username: "${NEXTCLOUD_ADMIN_USER}", password: "${NEXTCLOUD_ADMIN_PASSWORD}"}
        headers: {OCS-APIRequest: "true"}
        success: {field: ocs.meta.statuscode, equals: [100, 200]}
        endpoints:
          health:       {method: GET, path: "/ocs/v1.php/cloud/capabilities?format=json", version: ocs.data.version.string}
          find:         {method: GET, path: "/ocs/v1.php/cloud/users/{username}?format=json", id: ocs.data.id}
          create:       {method: POST, path: "/ocs/v1.php/cloud/users?format=json", id: "{username}",
                         form: {userid: "{username}", password: "{password}", email: "{email}", displayName: "{name}"}}
          set_password: {method: PUT, path: "/ocs/v1.php/cloud/users/{id}?format=json",
                         form: {key: password, value: "{password}"}}
          disable:      {method: PUT, path: "/ocs/v1.php/cloud/users/{id}/disable?format=json"}
          enable:       {method: PUT, path: "/ocs/v1.php/cloud/users/{id}/enable?format=json"}
          delete:       {method: DELETE, path: "/ocs/v1.php/cloud/users/{id}?format=json"}

Placeholders available in paths and bodies: ``{slug} {name} {email} {username} {password} {id} {login}``.
An endpoint takes ``method``, ``path``, an optional ``json`` or ``form`` body and ``expect`` (accepted HTTP
codes, default 200/201/204). ``id`` is either a template (``"{username}"``) or a dotted path into the JSON
response (``data.user.id``). ``find`` returns "not found" on HTTP 404, or searches a list with
``items: <path>`` and ``match: {field: email, equals: "{email}"}``.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

import httpx

from ..base import HttpService, ServiceError
from ..registry import register_app
from .base import Account, AppConnector, Person

DEFAULT_EXPECT = (200, 201, 204)


class _Missing(dict):
    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


def dig(data: Any, path: str) -> Any:
    """Follow a dotted path (``ocs.data.users.0.id``) into decoded JSON; None when it does not exist."""
    for part in path.split("."):
        if isinstance(data, dict):
            data = data.get(part)
        elif isinstance(data, list) and part.isdigit() and int(part) < len(data):
            data = data[int(part)]
        else:
            return None
    return data


def render(template: Any, context: dict[str, str], *, url: bool = False) -> Any:
    if isinstance(template, str):
        values = {k: quote(v, safe="") for k, v in context.items()} if url else context
        return template.format_map(_Missing(values))
    if isinstance(template, dict):
        return {k: render(v, context, url=url) for k, v in template.items()}
    if isinstance(template, list):
        return [render(v, context, url=url) for v in template]
    return template


@register_app("rest")
class RestConnector(AppConnector):
    default_icon = "app"

    def __init__(self, app_id: str, options: dict[str, Any], *, domain: str):
        super().__init__(app_id, options, domain=domain)
        self.endpoints: dict[str, dict[str, Any]] = options.get("endpoints") or {}
        for required in ("create", "delete"):
            if required not in self.endpoints:
                raise ValueError(f"app '{app_id}': the rest connector needs an '{required}' endpoint")
        self.login_kind = str(options.get("login") or "username")
        self.requires_email = bool(options.get("requires_email", self.login_kind == "email"))
        self.manages_password = bool(options.get("manages_password", True))
        self.supports_disable = "disable" in self.endpoints and "enable" in self.endpoints
        self.username_template = str(options.get("username") or "{slug}")
        self.success = options.get("success") or {}
        headers = {str(k): str(v) for k, v in (options.get("headers") or {}).items()}
        auth_options = options.get("auth") or {}
        auth_type = str(auth_options.get("type") or "none")
        auth: httpx.Auth | None = None
        if auth_type == "basic":
            auth = httpx.BasicAuth(str(auth_options.get("username") or ""), str(auth_options.get("password") or ""))
        elif auth_type == "bearer":
            headers["Authorization"] = f"Bearer {auth_options.get('token') or ''}"
        elif auth_type == "header":
            headers[str(auth_options.get("name") or "X-Api-Key")] = str(auth_options.get("value") or "")
        elif auth_type != "none":
            raise ValueError(f"app '{app_id}': unknown auth type '{auth_type}' (basic, bearer, header or none)")
        self.http = HttpService(self.label, str(options.get("url") or ""), headers=headers, auth=auth)

    def _context(self, person: Person | None = None, password: str | None = None,
                 account: Account | None = None) -> dict[str, str]:
        ctx: dict[str, str] = {}
        if person:
            ctx.update(slug=person.slug, name=person.name, email=person.email)
            ctx["username"] = self.username_template.format_map(_Missing(ctx))
        if password is not None:
            ctx["password"] = password
        if account:
            ctx.update(id=account.external_id, login=account.login)
        return ctx

    async def _call(self, name: str, context: dict[str, str], *, allow_404: bool = False) -> Any:
        spec = self.endpoints.get(name)
        if not spec:
            raise ServiceError(self.label, "errors.unsupported")
        expect = tuple(int(c) for c in spec.get("expect") or DEFAULT_EXPECT) + ((404,) if allow_404 else ())
        path = render(str(spec.get("path") or "/"), context, url=True)
        kwargs: dict[str, Any] = {"expect": expect}
        if "json" in spec:
            kwargs["json"] = render(spec["json"], context)
        if "form" in spec:
            kwargs["data"] = render(spec["form"], context)
        body = await self.http.request(str(spec.get("method") or "GET").upper(), path, **kwargs)
        self._check_success(body)
        return body

    def _check_success(self, body: Any) -> None:
        field = self.success.get("field")
        if not field or not isinstance(body, dict):
            return
        accepted = self.success.get("equals")
        accepted = accepted if isinstance(accepted, list) else [accepted]
        value = dig(body, str(field))
        if value not in accepted and str(value) not in {str(a) for a in accepted}:
            message = dig(body, str(self.success.get("message") or "")) if self.success.get("message") else None
            raise ServiceError(self.label, "errors.raw", text=str(message or f"{field} = {value}"))

    def _extract_id(self, spec: dict[str, Any], body: Any, context: dict[str, str]) -> str | None:
        id_spec = str(spec.get("id") or "id")
        value = render(id_spec, context) if "{" in id_spec else dig(body, id_spec)
        return None if value in (None, "") else str(value)

    async def version(self) -> str:
        self.require("url")
        if "health" not in self.endpoints:
            return ""
        body = await self._call("health", {})
        path = self.endpoints["health"].get("version")
        return str(dig(body, path) or "") if path else ""

    async def find(self, person: Person, parent: Account | None = None) -> Account | None:
        spec = self.endpoints.get("find")
        if not spec:
            return None
        self.require("url")
        context = self._context(person)
        try:
            body = await self._call("find", context, allow_404=True)
        except ServiceError:
            return None
        if spec.get("items"):
            match = spec.get("match") or {}
            wanted = str(render(str(match.get("equals") or "{username}"), context)).lower()
            items = dig(body, str(spec["items"])) or []
            body = next((i for i in items if str(dig(i, str(match.get("field") or "username")) or "").lower() == wanted), None)
            if body is None:
                return None
        external_id = self._extract_id(spec, body, context) if body is not None else None
        return Account(external_id, self.default_login(person)) if external_id else None

    def default_login(self, person: Person) -> str:
        return person.email if self.login_kind == "email" else self._context(person)["username"]

    async def create(self, person: Person, password: str | None, parent: Account | None = None) -> Account:
        self.require("url")
        context = self._context(person, password)
        body = await self._call("create", context)
        external_id = self._extract_id(self.endpoints["create"], body, context)
        if not external_id:
            raise ServiceError(self.label, "errors.missing_id")
        return Account(external_id, self.default_login(person))

    async def set_password(self, account: Account, password: str) -> None:
        await self._call("set_password", {**self._context(account=account), "password": password})

    async def set_enabled(self, account: Account, enabled: bool) -> None:
        await self._call("enable" if enabled else "disable", self._context(account=account))

    async def delete(self, account: Account) -> None:
        await self._call("delete", self._context(account=account), allow_404=True)
