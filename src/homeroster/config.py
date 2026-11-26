"""Configuration: one YAML file for the structure, environment variables for the secrets.

``config/homeroster.yaml`` describes the instance, the VPN, the reverse proxy, the mailer and the apps.
Any value can reference an environment variable with ``${NAME}`` or ``${NAME:-default}``, so secrets
live in ``.env`` and never in the YAML file. See ``config/homeroster.example.yaml``.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

ENV_REF = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")
DEFAULT_CONFIG_PATH = "/config/homeroster.yaml"
#: VPN, proxy and apps edited in Settings > Services. When present it replaces those three sections of
#: homeroster.yaml (which is mounted read-only). Lives in the data directory, mode 600: it may hold secrets.
SERVICES_FILE = "services.yaml"
SERVICE_SECTIONS = ("vpn", "proxy", "apps")


class ConfigError(Exception):
    """The configuration file is missing or invalid."""


def interpolate(value: Any) -> Any:
    """Replace ``${VAR}`` / ``${VAR:-default}`` references with environment values, recursively."""
    if isinstance(value, str):
        return ENV_REF.sub(lambda m: os.environ.get(m.group(1), m.group(2) or ""), value)
    if isinstance(value, dict):
        return {k: interpolate(v) for k, v in value.items()}
    if isinstance(value, list):
        return [interpolate(v) for v in value]
    return value


def as_bool(value: Any, default: bool = False) -> bool:
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def as_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class ProviderConfig:
    """A pluggable building block (VPN, proxy, mailer): its provider name plus free-form options."""

    provider: str
    options: dict[str, Any] = field(default_factory=dict)

    def get(self, key: str, default: Any = None) -> Any:
        value = self.options.get(key)
        return default if value is None or value == "" else value

    @classmethod
    def parse(cls, section: str, raw: Any, *, required: bool = True) -> ProviderConfig | None:
        if not raw:
            if required:
                raise ConfigError(f"missing '{section}' section")
            return None
        if not isinstance(raw, dict) or not raw.get("provider"):
            raise ConfigError(f"'{section}' needs a 'provider' key")
        options = {k: v for k, v in raw.items() if k != "provider"}
        return cls(provider=str(raw["provider"]), options=options)


@dataclass
class Config:
    owner: str = "Admin"
    domain: str = "example.com"
    language: str = "en"
    logo: str = ""
    default_device: str = "phone"
    trusted_proxies: list[str] = field(default_factory=list)
    cookie_secure: bool = True
    session_idle_minutes: int = 30
    session_max_hours: int = 12
    sync_interval: int = 300
    plugins: list[str] = field(default_factory=list)
    data_dir: str = "/data"
    setup_token: str = ""
    vpn: ProviderConfig = field(default_factory=lambda: ProviderConfig("wgdashboard"))
    proxy: ProviderConfig = field(default_factory=lambda: ProviderConfig("npm"))
    mail: ProviderConfig | None = None
    apps: dict[str, dict[str, Any]] = field(default_factory=dict)
    #: The sections as written (``${VARIABLE}`` references kept), so the Services page can save them back.
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    @property
    def db_path(self) -> str:
        return os.path.join(self.data_dir, "homeroster.db")

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> Config:
        written = {k: (raw or {}).get(k) for k in SERVICE_SECTIONS}
        raw = interpolate(raw or {})
        core = raw.get("homeroster") or {}
        session = core.get("session") or {}
        proxies = core.get("trusted_proxies") or []
        if isinstance(proxies, str):
            proxies = proxies.replace(",", " ").split()
        apps = raw.get("apps") or {}
        if not isinstance(apps, dict):
            raise ConfigError("'apps' must be a mapping of app id -> settings")
        for app_id, app in apps.items():
            if not re.fullmatch(r"[a-z][a-z0-9_-]{0,31}", str(app_id)):
                raise ConfigError(f"invalid app id '{app_id}' (lowercase letters, digits, '-' or '_')")
            if not isinstance(app, dict) or not app.get("connector"):
                raise ConfigError(f"app '{app_id}' needs a 'connector' key")
        return cls(
            owner=str(core.get("owner") or "Admin"),
            domain=str(core.get("domain") or "example.com"),
            language=str(core.get("language") or "en"),
            logo=str(core.get("logo") or ""),
            default_device=str(core.get("default_device") or "phone"),
            trusted_proxies=[str(p) for p in proxies],
            cookie_secure=as_bool(core.get("cookie_secure"), True),
            session_idle_minutes=as_int(session.get("idle_minutes"), 30),
            session_max_hours=as_int(session.get("max_hours"), 12),
            sync_interval=as_int(core.get("sync_interval"), 300),
            plugins=[str(p) for p in core.get("plugins") or []],
            data_dir=os.environ.get("HOMEROSTER_DATA", "/data"),
            setup_token=os.environ.get("HOMEROSTER_SETUP_TOKEN", "").strip(),
            vpn=ProviderConfig.parse("vpn", raw.get("vpn")),
            proxy=ProviderConfig.parse("proxy", raw.get("proxy")),
            mail=ProviderConfig.parse("mail", raw.get("mail"), required=False),
            apps={str(k): dict(v) for k, v in apps.items()},
            raw=written,
        )


def load_config(path: str | os.PathLike[str] | None = None) -> Config:
    path = Path(path or os.environ.get("HOMEROSTER_CONFIG", DEFAULT_CONFIG_PATH))
    if not path.is_file():
        raise ConfigError(f"configuration file not found: {path} (copy config/homeroster.example.yaml)")
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        raise ConfigError(f"{path}: invalid YAML ({e})") from e
    raw = raw or {}
    services = load_services()
    if services:
        raw.update({k: services[k] for k in SERVICE_SECTIONS if k in services})
    return Config.from_dict(raw)


def services_path() -> Path:
    return Path(os.environ.get("HOMEROSTER_DATA", "/data")) / SERVICES_FILE


def load_services() -> dict[str, Any]:
    path = services_path()
    if not path.is_file():
        return {}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as e:
        raise ConfigError(f"{path}: invalid YAML ({e})") from e
    return data if isinstance(data, dict) else {}


def save_services(sections: dict[str, Any]) -> None:
    """Write the VPN, proxy and apps sections atomically, readable by HomeRoster only."""
    path = services_path()
    text = ("# Written by HomeRoster (Settings > Services). It replaces the vpn, proxy and apps sections of\n"
            "# homeroster.yaml. Delete it to go back to the YAML file.\n"
            + yaml.safe_dump({k: sections[k] for k in SERVICE_SECTIONS if k in sections}, sort_keys=False,
                             allow_unicode=True))
    tmp = path.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(text)
    os.replace(tmp, path)
