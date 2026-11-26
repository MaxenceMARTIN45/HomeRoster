"""Vaultwarden and Home Assistant.

* **Vaultwarden**: its admin API, signed in with the ``ADMIN_TOKEN`` (a ``VW_ADMIN`` cookie). HomeRoster
  *invites* the person's e-mail address; they create their account and master password themselves (it
  encrypts their vault, nobody else may know it). With SMTP configured in Vaultwarden they get an e-mail,
  otherwise they sign up on the web vault with that address. Options: url, admin_token.
* **Home Assistant**: users are managed through the WebSocket API (``/api/websocket``) with a long-lived
  access token of an administrator. The same API is served by Home Assistant OS (VM), Container (Docker)
  and Core, in an LXC or anywhere else. People get a regular, non-admin user. Options: url, token,
  local_only (true: their account only works from the local network, i.e. through the VPN).
"""

from __future__ import annotations

import json
import re
from collections.abc import Awaitable, Callable
from typing import Any, ClassVar

from ..base import HttpService, ServiceError
from ..registry import register_app
from .base import Account, AppConnector, Person

# ======================================================================================== Vaultwarden


@register_app("vaultwarden")
class Vaultwarden(AppConnector):
    default_label = "Vaultwarden"
    default_description: ClassVar[dict[str, str]] = {"en": "Password manager", "fr": "Gestionnaire de mots de passe"}
    default_color = "indigo"
    default_icon = "key"
    default_logo = "vaultwarden"
    login_kind = "email"
    requires_email = True
    manages_password = False   # the master password is theirs alone
    supports_disable = True

    def __init__(self, app_id: str, options: dict[str, Any], *, domain: str):
        super().__init__(app_id, options, domain=domain)
        self.http = HttpService(self.label, str(options.get("url") or ""))

    async def _admin(self, method: str, path: str, **kwargs: Any) -> Any:
        self.require("url", "admin_token")
        # The cookie may be flagged Secure; read it from the header so plain-HTTP LAN URLs work too.
        async with self.http.client() as client:
            login = await client.post("/admin", data={"token": self.option("admin_token")})
        cookie = re.search(r"VW_ADMIN=([^;]+)", login.headers.get("set-cookie", ""))
        if not cookie:
            raise ServiceError(self.label, "errors.login_refused")
        return await self.http.request(method, "/admin" + path, headers={"Cookie": f"VW_ADMIN={cookie.group(1)}"},
                                       expect=kwargs.pop("expect", (200, 204)), **kwargs)

    async def version(self) -> str:
        await self._admin("GET", "/users")
        return ""

    async def find(self, person: Person, parent: Account | None = None) -> Account | None:
        if not person.email:
            return None
        user = await self._admin("GET", f"/users/by-mail/{person.email}", expect=(200, 404))
        return Account(str(user["id"]), person.email) if isinstance(user, dict) and user.get("id") else None

    async def create(self, person: Person, password: str | None, parent: Account | None = None) -> Account:
        existing = await self.find(person)
        if existing:
            return existing
        user = await self._admin("POST", "/invite", json={"email": person.email})
        if not isinstance(user, dict) or not user.get("id"):
            raise ServiceError(self.label, "errors.missing_id")
        return Account(str(user["id"]), person.email)

    async def set_enabled(self, account: Account, enabled: bool) -> None:
        await self._admin("POST", f"/users/{account.external_id}/{'enable' if enabled else 'disable'}", json={})

    async def delete(self, account: Account) -> None:
        await self._admin("POST", f"/users/{account.external_id}/delete", json={}, expect=(200, 204, 404))


# ===================================================================================== Home Assistant

#: Opens a WebSocket session and returns a "send one command, get its result" function. The tests replace it.
Session = Callable[[str, str], Awaitable[Any]]


async def websocket_session(url: str, token: str):  # pragma: no cover - needs a real Home Assistant
    from websockets.asyncio.client import connect

    ws_url = re.sub(r"^http", "ws", url.rstrip("/")) + "/api/websocket"
    connection = await connect(ws_url, open_timeout=10)
    await connection.recv()  # auth_required
    await connection.send(json.dumps({"type": "auth", "access_token": token}))
    if json.loads(await connection.recv()).get("type") != "auth_ok":
        await connection.close()
        raise ServiceError("Home Assistant", "errors.login_refused")
    counter = 0

    async def command(payload: dict[str, Any]) -> Any:
        nonlocal counter
        counter += 1
        await connection.send(json.dumps({"id": counter, **payload}))
        while True:
            message = json.loads(await connection.recv())
            if message.get("id") == counter and message.get("type") == "result":
                if not message.get("success"):
                    error = message.get("error") or {}
                    raise ServiceError("Home Assistant", "errors.raw", text=error.get("message") or error.get("code") or "")
                return message.get("result")

    return connection, command


@register_app("homeassistant")
class HomeAssistant(AppConnector):
    default_label = "Home Assistant"
    default_description: ClassVar[dict[str, str]] = {"en": "Smart home", "fr": "Maison connectée"}
    default_color = "sky"
    default_icon = "home"
    default_logo = "home-assistant"
    login_kind = "username"
    supports_disable = True
    #: Replaced by the tests with a fake; returns (connection, command).
    connect: ClassVar[Callable[..., Awaitable[Any]]] = staticmethod(websocket_session)  # type: ignore[assignment]

    def __init__(self, app_id: str, options: dict[str, Any], *, domain: str):
        super().__init__(app_id, options, domain=domain)
        self.http = HttpService(self.label, str(options.get("url") or ""),
                                headers={"Authorization": f"Bearer {options.get('token') or ''}"})

    async def _run(self, *payloads: dict[str, Any]) -> list[Any]:
        self.require("url", "token")
        try:
            connection, command = await type(self).connect(str(self.option("url")), str(self.option("token")))
        except ServiceError:
            raise
        except (OSError, TimeoutError) as e:
            raise ServiceError(self.label, "errors.unreachable") from e
        try:
            return [await command(p) for p in payloads]
        finally:
            await connection.close()

    async def version(self) -> str:
        self.require("url", "token")
        config = await self.http.request("GET", "/api/config")
        await self._run({"type": "config/auth/list"})  # the token must belong to an administrator
        return str((config or {}).get("version") or "")

    async def find(self, person: Person, parent: Account | None = None) -> Account | None:
        (users,) = await self._run({"type": "config/auth/list"})
        wanted = {person.slug, person.name.lower()}
        user = next((u for u in users or [] if str(u.get("username") or "").lower() in wanted), None)
        return Account(user["id"], user["username"]) if user else None

    async def create(self, person: Person, password: str | None, parent: Account | None = None) -> Account:
        if await self.find(person):
            raise ServiceError(self.label, "errors.account_exists", login=person.slug)
        (created,) = await self._run({"type": "config/auth/create", "name": person.name, "group_ids": ["system-users"],
                                      "local_only": bool(self.option("local_only", False))})
        user_id = str(((created or {}).get("user") or {}).get("id") or "")
        if not user_id:
            raise ServiceError(self.label, "errors.missing_id")
        try:
            await self._run({"type": "config/auth_provider/homeassistant/create", "user_id": user_id,
                             "username": person.slug, "password": password})
        except ServiceError:
            await self._run({"type": "config/auth/delete", "user_id": user_id})  # no half-created user
            raise
        return Account(user_id, person.slug)

    async def set_password(self, account: Account, password: str) -> None:
        await self._run({"type": "config/auth_provider/homeassistant/admin_change_password",
                         "user_id": account.external_id, "password": password})

    async def set_enabled(self, account: Account, enabled: bool) -> None:
        await self._run({"type": "config/auth/update", "user_id": account.external_id, "is_active": enabled})

    async def delete(self, account: Account) -> None:
        await self._run({"type": "config/auth/delete", "user_id": account.external_id})
