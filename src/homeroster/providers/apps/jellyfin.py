"""Jellyfin (10.10+). Options: url, api_key (Dashboard > API Keys).

  POST /Users/New                    {Name, Password}
  POST /Users/Password?userId=ID     {NewPw, ResetPassword: false}   (an admin key needs no old password)
  POST /Users/{id}/Policy            FULL policy (read it, flip IsDisabled, send it back)
  DELETE /Users/{id} · GET /Users · GET /System/Info/Public
Jellyfin stores no e-mail: people sign in with their user name.
"""

from __future__ import annotations

from typing import Any, ClassVar

from ..base import HttpService, ServiceError
from ..registry import register_app
from .base import Account, AppConnector, Person


@register_app("jellyfin")
class Jellyfin(AppConnector):
    default_label = "Jellyfin"
    default_description: ClassVar[dict[str, str]] = {"en": "Movies and TV shows", "fr": "Films et séries"}
    default_color = "violet"
    default_icon = "movie"
    login_kind = "username"
    supports_disable = True

    def __init__(self, app_id: str, options: dict[str, Any], *, domain: str):
        super().__init__(app_id, options, domain=domain)
        key = str(options.get("api_key") or "")
        self.http = HttpService(self.label, str(options.get("url") or ""),
                                headers={"Authorization": f'MediaBrowser Token="{key}"'})

    async def version(self) -> str:
        self.require("url", "api_key")
        info = await self.http.request("GET", "/System/Info/Public")
        await self.http.request("GET", "/Users")  # make sure the key is accepted
        return str((info or {}).get("Version", ""))

    async def _find_user(self, *names: str) -> dict | None:
        self.require("url", "api_key")
        wanted = {n.lower() for n in names if n}
        return next((u for u in await self.http.request("GET", "/Users") or [] if u.get("Name", "").lower() in wanted), None)

    async def find(self, person: Person, parent: Account | None = None) -> Account | None:
        user = await self._find_user(person.name, person.slug)
        return Account(user["Id"], user["Name"]) if user else None

    async def create(self, person: Person, password: str | None, parent: Account | None = None) -> Account:
        if await self._find_user(person.name):
            raise ServiceError(self.label, "errors.account_exists", login=person.name)
        user = await self.http.request("POST", "/Users/New", json={"Name": person.name, "Password": password})
        if not isinstance(user, dict) or not user.get("Id"):
            raise ServiceError(self.label, "errors.missing_id")
        return Account(user["Id"], user.get("Name") or person.name)

    async def set_password(self, account: Account, password: str) -> None:
        await self.http.request("POST", "/Users/Password", params={"userId": account.external_id},
                                json={"NewPw": password, "ResetPassword": False}, expect=(200, 204))

    async def set_enabled(self, account: Account, enabled: bool) -> None:
        user = await self.http.request("GET", f"/Users/{account.external_id}")
        policy = (user or {}).get("Policy")
        if not isinstance(policy, dict):
            raise ServiceError(self.label, "errors.unexpected_response")
        if policy.get("IsAdministrator") and not enabled:
            raise ServiceError(self.label, "errors.cannot_disable_admin")
        policy["IsDisabled"] = not enabled
        await self.http.request("POST", f"/Users/{account.external_id}/Policy", json=policy, expect=(200, 204))

    async def delete(self, account: Account) -> None:
        await self.http.request("DELETE", f"/Users/{account.external_id}", expect=(200, 204, 404))
