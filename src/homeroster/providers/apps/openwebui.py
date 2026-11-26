"""Open WebUI. Options: url, api_key (an admin's ``sk-...`` key).

Enable API keys first: Admin Panel > Settings > General > "Enable API Key" (without endpoint restrictions,
or allow ``/api/v1/auths`` and ``/api/v1/users``).
  POST /api/v1/auths/add              {name, email, password, role: "user"}   -> {id, ...}
  POST /api/v1/users/{id}/update      {password} (or {role})
  DELETE /api/v1/users/{id} · GET /api/v1/users/all · GET /api/version
People sign in with their e-mail address. "Disabled" means role ``pending``.
"""

from __future__ import annotations

from typing import Any, ClassVar

from ..base import HttpService, ServiceError
from ..registry import register_app
from .base import Account, AppConnector, Person


@register_app("openwebui")
class OpenWebUI(AppConnector):
    default_label = "Open WebUI"
    default_description: ClassVar[dict[str, str]] = {"en": "AI assistant", "fr": "Assistant IA"}
    default_color = "emerald"
    default_icon = "chat"
    login_kind = "email"
    requires_email = True
    supports_disable = True

    def __init__(self, app_id: str, options: dict[str, Any], *, domain: str):
        super().__init__(app_id, options, domain=domain)
        self.http = HttpService(self.label, str(options.get("url") or ""),
                                headers={"Authorization": f"Bearer {options.get('api_key') or ''}"})

    async def version(self) -> str:
        self.require("url", "api_key")
        v = await self.http.request("GET", "/api/version")
        await self.http.request("GET", "/api/v1/users/all")  # make sure the key has admin rights
        return str((v or {}).get("version", ""))

    async def find(self, person: Person, parent: Account | None = None) -> Account | None:
        if not person.email:
            return None
        self.require("url", "api_key")
        body = await self.http.request("GET", "/api/v1/users/all") or {}
        users = body.get("users", []) if isinstance(body, dict) else body
        for user in users:
            if user.get("email", "").lower() == person.email.lower():
                return Account(user["id"], user["email"])
        return None

    async def create(self, person: Person, password: str | None, parent: Account | None = None) -> Account:
        if await self.find(person):
            raise ServiceError(self.label, "errors.account_exists", login=person.email)
        user = await self.http.request("POST", "/api/v1/auths/add", json={
            "name": person.name, "email": person.email, "password": password, "role": "user"})
        if not isinstance(user, dict) or not user.get("id"):
            raise ServiceError(self.label, "errors.missing_id")
        return Account(user["id"], person.email)

    async def set_password(self, account: Account, password: str) -> None:
        await self.http.request("POST", f"/api/v1/users/{account.external_id}/update", json={"password": password})

    async def set_enabled(self, account: Account, enabled: bool) -> None:
        await self.http.request("POST", f"/api/v1/users/{account.external_id}/update",
                                json={"role": "user" if enabled else "pending"})

    async def delete(self, account: Account) -> None:
        await self.http.request("DELETE", f"/api/v1/users/{account.external_id}", expect=(200, 204, 404))
