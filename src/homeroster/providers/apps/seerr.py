"""Seerr (Jellyseerr / Overseerr successor). Options: url, api_key (Settings > General), uses (jellyfin).

Accounts are imported from Jellyfin, so people sign in with their Jellyfin user name and password:
  POST /api/v1/user/import-from-jellyfin   {jellyfinUserIds: [id]}  -> [User]
  GET  /api/v1/user/jellyfin/{jellyfinId}                         -> User (when already imported)
  POST /api/v1/user/{id}/settings/main      {username, email}
  DELETE /api/v1/user/{id} · GET /api/v1/status
"""

from __future__ import annotations

from typing import Any, ClassVar

from ..base import HttpService, ServiceError
from ..registry import register_app
from .base import Account, AppConnector, Person


@register_app("seerr")
class Seerr(AppConnector):
    default_label = "Seerr"
    default_description: ClassVar[dict[str, str]] = {"en": "Request a movie or a show", "fr": "Demander un film ou une série"}
    default_color = "indigo"
    default_icon = "ticket"
    login_kind = "username"
    manages_password = False
    default_parent = "jellyfin"

    def __init__(self, app_id: str, options: dict[str, Any], *, domain: str):
        super().__init__(app_id, options, domain=domain)
        self.http = HttpService(self.label, str(options.get("url") or ""),
                                headers={"X-Api-Key": str(options.get("api_key") or "")})

    async def version(self) -> str:
        self.require("url", "api_key")
        status = await self.http.request("GET", "/api/v1/status")
        await self.http.request("GET", "/api/v1/user", params={"take": 1})  # make sure the key works
        return str((status or {}).get("version", ""))

    async def _by_parent(self, parent_id: str) -> dict | None:
        try:
            user = await self.http.request("GET", f"/api/v1/user/jellyfin/{parent_id}")
        except ServiceError:
            return None
        return user if isinstance(user, dict) and user.get("id") else None

    async def find(self, person: Person, parent: Account | None = None) -> Account | None:
        if not parent:
            return None
        self.require("url", "api_key")
        user = await self._by_parent(parent.external_id)
        return Account(str(user["id"]), parent.login) if user else None

    async def create(self, person: Person, password: str | None, parent: Account | None = None) -> Account:
        if not parent:
            raise ServiceError(self.label, "errors.parent_required")
        self.require("url", "api_key")
        user = await self._by_parent(parent.external_id)
        if not user:
            created = await self.http.request("POST", "/api/v1/user/import-from-jellyfin",
                                              json={"jellyfinUserIds": [parent.external_id]}, expect=(200, 201))
            user = created[0] if isinstance(created, list) and created else await self._by_parent(parent.external_id)
        if not user:
            raise ServiceError(self.label, "errors.import_empty")
        if person.email:
            try:
                await self.http.request("POST", f"/api/v1/user/{user['id']}/settings/main",
                                        json={"username": parent.login, "email": person.email}, expect=(200, 204))
            except ServiceError:
                pass  # the account works without an e-mail; Seerr only uses it for notifications
        return Account(str(user["id"]), parent.login)

    async def delete(self, account: Account) -> None:
        await self.http.request("DELETE", f"/api/v1/user/{account.external_id}", expect=(200, 204, 404))
