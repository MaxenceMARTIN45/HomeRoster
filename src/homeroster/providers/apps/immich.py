"""Immich (v1.120+ / v2). Options: url, api_key.

The API key must belong to an admin and have the permissions
``adminUser.create``, ``adminUser.read``, ``adminUser.update`` and ``adminUser.delete``.
  POST /api/admin/users        {email, name, password, shouldChangePassword: false, notify: false}
  PUT  /api/admin/users/{id}   {password}
  DELETE /api/admin/users/{id} {force: false}     (Immich trash, restorable for a few days)
  GET  /api/admin/users · GET /api/server/version
People sign in to Immich with their e-mail address.
"""

from __future__ import annotations

from typing import Any, ClassVar

from ..base import HttpService, ServiceError
from ..registry import register_app
from .base import Account, AppConnector, Person


@register_app("immich")
class Immich(AppConnector):
    default_label = "Immich"
    default_description: ClassVar[dict[str, str]] = {"en": "Photos and videos", "fr": "Photos et vidéos"}
    default_color = "sky"
    default_icon = "photo"
    default_logo = "immich"
    login_kind = "email"
    requires_email = True

    def __init__(self, app_id: str, options: dict[str, Any], *, domain: str):
        super().__init__(app_id, options, domain=domain)
        self.http = HttpService(self.label, str(options.get("url") or ""),
                                headers={"x-api-key": str(options.get("api_key") or "")})

    async def version(self) -> str:
        self.require("url", "api_key")
        v = await self.http.request("GET", "/api/server/version") or {}
        await self.http.request("GET", "/api/admin/users")  # make sure the key has admin rights
        version = f"{v.get('major')}.{v.get('minor')}.{v.get('patch')}"
        return version + (f"-{v['prerelease']}" if v.get("prerelease") is not None else "")

    async def find(self, person: Person, parent: Account | None = None) -> Account | None:
        if not person.email:
            return None
        self.require("url", "api_key")
        for user in await self.http.request("GET", "/api/admin/users") or []:
            if user.get("email", "").lower() == person.email.lower():
                return Account(user["id"], user["email"])
        return None

    async def create(self, person: Person, password: str | None, parent: Account | None = None) -> Account:
        if await self.find(person):
            raise ServiceError(self.label, "errors.account_exists", login=person.email)
        user = await self.http.request("POST", "/api/admin/users", expect=(200, 201), json={
            "email": person.email, "name": person.name, "password": password,
            "shouldChangePassword": False, "notify": False})
        if not isinstance(user, dict) or not user.get("id"):
            raise ServiceError(self.label, "errors.missing_id")
        return Account(user["id"], person.email)

    async def set_password(self, account: Account, password: str) -> None:
        await self.http.request("PUT", f"/api/admin/users/{account.external_id}", json={"password": password})

    async def delete(self, account: Account) -> None:
        await self.http.request("DELETE", f"/api/admin/users/{account.external_id}", json={"force": False},
                                expect=(200, 204, 404))
