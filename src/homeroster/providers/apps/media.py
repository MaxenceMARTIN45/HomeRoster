"""Media apps: Emby, Plex, Audiobookshelf and Navidrome.

* **Emby** (4.x): the Jellyfin API it was forked from. Options: url, api_key (Settings > API Keys).
* **Plex**: accounts live on plex.tv, so HomeRoster *shares your server* with the person's e-mail address
  (``plex.tv/api/servers/<id>/shared_servers``), like Plex's own "Manage library access". They accept the
  invitation and sign in with their own Plex account: no password here, and removing Plex removes the share.
  Options: url (your server), token (your X-Plex-Token), libraries (titles to share, default all).
* **Audiobookshelf** (2.x): ``/api/users``. Options: url, api_key (Settings > Users > your user > API token,
  or an API key with admin rights).
* **Navidrome** (0.50+): its web API (``/api/user``) with an admin account. Options: url, username, password.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from typing import Any, ClassVar

from ..base import HttpService, ServiceError
from ..registry import register_app
from .base import Account, AppConnector, Person
from .jellyfin import Jellyfin

# =============================================================================================== Emby


@register_app("emby")
class Emby(Jellyfin):
    default_label = "Emby"
    default_logo = "emby"
    default_color = "emerald"
    supports_language = False  # Emby keeps no per-user audio preference through this API

    def __init__(self, app_id: str, options: dict[str, Any], *, domain: str):
        super().__init__(app_id, options, domain=domain)
        self.http = HttpService(self.label, str(options.get("url") or ""),
                                headers={"X-Emby-Token": str(options.get("api_key") or "")})

    async def create(self, person: Person, password: str | None, parent: Account | None = None) -> Account:
        if await self._find_user(person.name):
            raise ServiceError(self.label, "errors.account_exists", login=person.name)
        user = await self.http.request("POST", "/Users/New", json={"Name": person.name})
        if not isinstance(user, dict) or not user.get("Id"):
            raise ServiceError(self.label, "errors.missing_id")
        account = Account(user["Id"], user.get("Name") or person.name)
        if password:
            await self.set_password(account, password)
        return account

    async def set_password(self, account: Account, password: str) -> None:
        await self.http.request("POST", f"/Users/{account.external_id}/Password",
                                json={"Id": account.external_id, "CurrentPw": "", "NewPw": password}, expect=(200, 204))


# =============================================================================================== Plex

PLEX_TV = "https://plex.tv"


@register_app("plex")
class Plex(AppConnector):
    default_label = "Plex"
    default_description: ClassVar[dict[str, str]] = {"en": "Movies and TV shows", "fr": "Films et séries"}
    default_color = "amber"
    default_icon = "movie"
    default_logo = "plex"
    login_kind = "email"
    requires_email = True
    manages_password = False

    def __init__(self, app_id: str, options: dict[str, Any], *, domain: str):
        super().__init__(app_id, options, domain=domain)
        headers = {"X-Plex-Token": str(options.get("token") or ""), "X-Plex-Client-Identifier": "homeroster",
                   "X-Plex-Product": "HomeRoster", "Accept": "application/json"}
        self.server = HttpService(self.label, str(options.get("url") or ""), headers=headers)
        self.tv = HttpService("plex.tv", str(options.get("plex_tv") or PLEX_TV), headers=headers)
        self._machine: str | None = None

    async def _machine_id(self) -> str:
        self.require("url", "token")
        if not self._machine:
            identity = await self.server.request("GET", "/identity")
            container = (identity or {}).get("MediaContainer") or {}
            self._machine = str(container.get("machineIdentifier") or "")
            if not self._machine:
                raise ServiceError(self.label, "errors.unexpected_response")
        return self._machine

    async def version(self) -> str:
        identity = await self.server.request("GET", "/identity")
        await self._shares()  # checks the token on plex.tv too
        return str(((identity or {}).get("MediaContainer") or {}).get("version") or "")

    async def _xml(self, path: str) -> ET.Element:
        text = await self.tv.request("GET", path, headers={"Accept": "application/xml"})
        try:
            return ET.fromstring(text if isinstance(text, str) else "")
        except ET.ParseError as e:
            raise ServiceError(self.label, "errors.unexpected_response") from e

    async def _shares(self) -> list[ET.Element]:
        root = await self._xml(f"/api/servers/{await self._machine_id()}/shared_servers")
        return list(root.iter("SharedServer"))

    async def _section_ids(self) -> list[int]:
        wanted = {str(t).lower() for t in self.option("libraries", []) or []}
        root = await self._xml(f"/api/servers/{await self._machine_id()}")
        return [int(s.get("id", "0")) for s in root.iter("Section")
                if not wanted or s.get("title", "").lower() in wanted]

    async def find(self, person: Person, parent: Account | None = None) -> Account | None:
        email = person.email.lower()
        share = next((s for s in await self._shares() if s.get("email", "").lower() == email and email), None)
        return Account(share.get("id", ""), person.email) if share is not None else None

    async def create(self, person: Person, password: str | None, parent: Account | None = None) -> Account:
        existing = await self.find(person)
        if existing:
            return existing
        machine = await self._machine_id()
        await self.tv.request("POST", f"/api/servers/{machine}/shared_servers", expect=(200, 201), json={
            "server_id": machine,
            "shared_server": {"library_section_ids": await self._section_ids(), "invited_email": person.email},
            "sharing_settings": {"allowSync": "1", "allowCameraUpload": "0", "allowChannels": "0",
                                 "filterMovies": "", "filterTelevision": "", "filterMusic": ""}})
        account = await self.find(person)
        if not account:
            raise ServiceError(self.label, "errors.missing_id")
        return account

    async def delete(self, account: Account) -> None:
        machine = await self._machine_id()
        await self.tv.request("DELETE", f"/api/servers/{machine}/shared_servers/{account.external_id}",
                              expect=(200, 204, 404))


# ===================================================================================== Audiobookshelf


@register_app("audiobookshelf")
class Audiobookshelf(AppConnector):
    default_label = "Audiobookshelf"
    default_description: ClassVar[dict[str, str]] = {"en": "Audiobooks and podcasts", "fr": "Livres audio et podcasts"}
    default_color = "orange"
    default_icon = "book"
    default_logo = "audiobookshelf"
    login_kind = "username"
    supports_disable = True

    def __init__(self, app_id: str, options: dict[str, Any], *, domain: str):
        super().__init__(app_id, options, domain=domain)
        self.http = HttpService(self.label, str(options.get("url") or ""),
                                headers={"Authorization": f"Bearer {options.get('api_key') or ''}"})

    async def version(self) -> str:
        self.require("url", "api_key")
        status = await self.http.request("GET", "/status")
        await self.http.request("GET", "/api/users")
        return str((status or {}).get("serverVersion") or "")

    async def _users(self) -> list[dict]:
        self.require("url", "api_key")
        return (await self.http.request("GET", "/api/users") or {}).get("users") or []

    async def find(self, person: Person, parent: Account | None = None) -> Account | None:
        wanted = {person.slug, person.name.lower()}
        user = next((u for u in await self._users() if str(u.get("username", "")).lower() in wanted), None)
        return Account(user["id"], user["username"]) if user else None

    async def create(self, person: Person, password: str | None, parent: Account | None = None) -> Account:
        if await self.find(person):
            raise ServiceError(self.label, "errors.account_exists", login=person.slug)
        body = await self.http.request("POST", "/api/users", json={
            "username": person.slug, "password": password, "email": person.email or None, "type": "user",
            "isActive": True, "permissions": {"download": True, "update": False, "delete": False, "upload": False,
                                              "accessAllLibraries": True, "accessAllTags": True, "accessExplicitContent": True}})
        user = (body or {}).get("user") or {}
        if not user.get("id"):
            raise ServiceError(self.label, "errors.missing_id")
        return Account(user["id"], user.get("username") or person.slug)

    async def set_password(self, account: Account, password: str) -> None:
        await self.http.request("PATCH", f"/api/users/{account.external_id}", json={"password": password})

    async def set_enabled(self, account: Account, enabled: bool) -> None:
        await self.http.request("PATCH", f"/api/users/{account.external_id}", json={"isActive": enabled})

    async def delete(self, account: Account) -> None:
        await self.http.request("DELETE", f"/api/users/{account.external_id}", expect=(200, 204, 404))


# ========================================================================================== Navidrome


@register_app("navidrome")
class Navidrome(AppConnector):
    default_label = "Navidrome"
    default_description: ClassVar[dict[str, str]] = {"en": "Music", "fr": "Musique"}
    default_color = "sky"
    default_icon = "music"
    default_logo = "navidrome"
    login_kind = "username"

    def __init__(self, app_id: str, options: dict[str, Any], *, domain: str):
        super().__init__(app_id, options, domain=domain)
        self.http = HttpService(self.label, str(options.get("url") or ""))

    async def _api(self, method: str, path: str, **kwargs: Any) -> Any:
        """Signs in with the admin account (a JWT valid for the call), then calls the web API."""
        self.require("url", "username", "password")
        login = await self.http.request("POST", "/auth/login", json={
            "username": self.option("username"), "password": self.option("password")})
        token = (login or {}).get("token")
        if not token:
            raise ServiceError(self.label, "errors.login_refused")
        return await self.http.request(method, path, headers={"X-ND-Authorization": f"Bearer {token}"},
                                       expect=kwargs.pop("expect", (200, 201, 204)), **kwargs)

    async def version(self) -> str:
        await self._api("GET", "/api/user")
        return ""

    async def find(self, person: Person, parent: Account | None = None) -> Account | None:
        wanted = {person.slug, person.name.lower()}
        user = next((u for u in await self._api("GET", "/api/user") or []
                     if str(u.get("userName", "")).lower() in wanted), None)
        return Account(user["id"], user["userName"]) if user else None

    async def create(self, person: Person, password: str | None, parent: Account | None = None) -> Account:
        if await self.find(person):
            raise ServiceError(self.label, "errors.account_exists", login=person.slug)
        user = await self._api("POST", "/api/user", json={
            "userName": person.slug, "name": person.name, "email": person.email, "password": password, "isAdmin": False})
        if not isinstance(user, dict) or not user.get("id"):
            raise ServiceError(self.label, "errors.missing_id")
        return Account(user["id"], user.get("userName") or person.slug)

    async def set_password(self, account: Account, password: str) -> None:
        user = await self._api("GET", f"/api/user/{account.external_id}")
        await self._api("PUT", f"/api/user/{account.external_id}", json={**(user or {}), "password": password})

    async def delete(self, account: Account) -> None:
        await self._api("DELETE", f"/api/user/{account.external_id}", expect=(200, 204, 404))
