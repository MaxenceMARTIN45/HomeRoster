"""The app connector contract: create, find, update and delete one person's account in one app.

A connector is configured by an entry of the ``apps:`` section::

    apps:
      jellyfin:                     # app id (also the default subdomain: jellyfin.<domain>)
        connector: jellyfin         # registered connector name
        url: http://jellyfin.lan:8096
        api_key: ${JELLYFIN_API_KEY}
        # Optional overrides: label, description ({en: ..., fr: ...}), color, icon, logo, hostname, public_url

Class attributes give the defaults; the YAML entry can override the presentation ones.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar

from ...i18n import localized
from ..base import Health, ServiceError

STATIC_APPS_DIR = Path(__file__).resolve().parent.parent.parent / "static" / "apps"
COLORS = ("violet", "indigo", "sky", "emerald", "amber", "rose", "teal", "orange")


@dataclass(frozen=True)
class Person:
    """What a connector knows about the person (never the stored password: there is none)."""

    id: int
    slug: str
    name: str
    email: str
    language: str


@dataclass(frozen=True)
class Account:
    external_id: str
    login: str


class AppConnector(ABC):
    provider_name: ClassVar[str] = ""

    # ---- defaults, overridable per app in homeroster.yaml
    default_label: ClassVar[str] = ""
    default_description: ClassVar[dict[str, str]] = {}
    default_color: ClassVar[str] = "violet"
    default_icon: ClassVar[str] = "app"
    #: Logo of the e-mails: a dashboard-icons name, a URL or a path (see homeroster.logos). "builtin" = pictogram.
    default_logo: ClassVar[str] = "builtin"

    # ---- behaviour
    #: "username" or "email": what the person types to sign in.
    login_kind: ClassVar[str] = "username"
    #: The app cannot create an account without an e-mail address.
    requires_email: ClassVar[bool] = False
    #: The app can disable an account without deleting it (otherwise only the proxy blocks access).
    supports_disable: ClassVar[bool] = False
    #: The app has its own password (False when it signs in through another app's account).
    manages_password: ClassVar[bool] = True
    #: Id of the app whose account this one reuses (e.g. Seerr signs in with Jellyfin accounts).
    default_parent: ClassVar[str | None] = None
    #: The app stores a language per account, set from the person's language (see ``set_language``).
    supports_language: ClassVar[bool] = False

    def __init__(self, app_id: str, options: dict[str, Any], *, domain: str):
        self.id = app_id
        self.options = options
        self.label = str(options.get("label") or self.default_label or app_id.title())
        self.description = options.get("description") or self.default_description
        color = str(options.get("color") or self.default_color)
        self.color = color if color in COLORS else "violet"
        self.icon = str(options.get("icon") or self.default_icon)
        self.hostname = str(options.get("hostname") or f"{app_id}.{domain}").lower()
        self.public_url = str(options.get("public_url") or f"https://{self.hostname}")
        self.parent: str | None = options.get("uses", self.default_parent) or None
        self.logo = str(options.get("logo") or self.default_logo)
        self.fallback_logo = self._pictogram()

    def _pictogram(self) -> Path:
        """HomeRoster's own PNG for this connector (``static/apps/<connector>.png``), else the generic one."""
        own = STATIC_APPS_DIR / f"{self.provider_name}.png"
        return own if own.is_file() else STATIC_APPS_DIR / "app.png"

    def describe(self, lang: str | None = None) -> str:
        return localized(self.description, lang)

    def option(self, key: str, default: Any = None) -> Any:
        value = self.options.get(key)
        return default if value is None or value == "" else value

    def require(self, *keys: str) -> None:
        missing = [k for k in keys if not self.option(k)]
        if missing:
            raise ServiceError(self.label, "errors.not_configured")

    def default_login(self, person: Person) -> str:
        return person.email if self.login_kind == "email" else person.name

    # ---- operations -------------------------------------------------------------------------------

    @abstractmethod
    async def create(self, person: Person, password: str | None, parent: Account | None = None) -> Account:
        """Create the account. ``password`` is None only for apps that do not manage passwords."""

    @abstractmethod
    async def delete(self, account: Account) -> None: ...

    async def set_language(self, account: Account, language: str) -> None:
        """Apply a HomeRoster language code (``en``, ``fr``...) to the account. Only when ``supports_language``."""
        raise ServiceError(self.label, "errors.unsupported")

    async def find(self, person: Person, parent: Account | None = None) -> Account | None:
        """Find an existing account for this person (used to link accounts created before HomeRoster)."""
        return None

    async def set_password(self, account: Account, password: str) -> None:
        raise ServiceError(self.label, "errors.unsupported")

    async def set_enabled(self, account: Account, enabled: bool) -> None:
        raise ServiceError(self.label, "errors.unsupported")

    async def version(self) -> str:
        return ""

    async def health(self) -> Health:
        try:
            return Health(ok=True, message="", version=await self.version())
        except ServiceError as e:
            return Health.from_error(e)

    def check_new(self, person: Person, password: str | None) -> None:
        """Validate the inputs before calling ``create``."""
        if self.requires_email and not person.email:
            raise ServiceError(self.label, "errors.email_required")
        if self.manages_password and not password:
            raise ServiceError(self.label, "errors.password_required")

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__} {self.id}>"
