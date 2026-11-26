"""Translations: one JSON file per language in ``locales/``.

Keys are English identifiers (``people.title``), values are the text shown to the user. Nested objects are
flattened into dotted keys. A missing key falls back to English, then to the key itself, so a partial
translation never breaks a page. To add a language, copy ``locales/en.json`` to ``locales/<code>.json``,
translate the values and restart: it appears in the language pickers automatically.
"""

from __future__ import annotations

import json
from contextvars import ContextVar
from functools import lru_cache
from pathlib import Path
from typing import Any

LOCALES_DIR = Path(__file__).parent / "locales"
FALLBACK = "en"

_current: ContextVar[str] = ContextVar("homeroster_language", default=FALLBACK)


def _flatten(data: dict[str, Any], prefix: str = "") -> dict[str, str]:
    out: dict[str, str] = {}
    for key, value in data.items():
        full = f"{prefix}{key}"
        if isinstance(value, dict):
            out.update(_flatten(value, full + "."))
        else:
            out[full] = str(value)
    return out


@lru_cache(maxsize=1)
def catalogs() -> dict[str, dict[str, str]]:
    found: dict[str, dict[str, str]] = {}
    for file in sorted(LOCALES_DIR.glob("*.json")):
        found[file.stem] = _flatten(json.loads(file.read_text(encoding="utf-8")))
    if FALLBACK not in found:
        raise RuntimeError(f"missing {LOCALES_DIR / (FALLBACK + '.json')}")
    return found


def languages() -> dict[str, str]:
    """``{"en": "English", "fr": "Français", ...}`` from each file's ``_meta.name``."""
    codes = sorted(catalogs(), key=lambda code: (code != FALLBACK, code))  # English first, then by code
    return {code: catalogs()[code].get("_meta.name", code) for code in codes}


def normalize(lang: str | None) -> str:
    lang = (lang or "").strip().lower().replace("_", "-")
    if lang in catalogs():
        return lang
    base = lang.split("-")[0]
    return base if base in catalogs() else FALLBACK


def set_language(lang: str | None) -> str:
    lang = normalize(lang)
    _current.set(lang)
    return lang


def current_language() -> str:
    return _current.get()


def t(key: str, lang: str | None = None, **params: Any) -> str:
    """Translate ``key`` in ``lang`` (default: the current request's language)."""
    cats = catalogs()
    text = cats.get(normalize(lang) if lang else _current.get(), {}).get(key) or cats[FALLBACK].get(key) or key
    if params:
        try:
            return text.format(**params)
        except (KeyError, IndexError, ValueError):
            return text
    return text


def localized(value: Any, lang: str | None = None) -> str:
    """Pick a translation from a ``{"en": "...", "fr": "..."}`` mapping (used by app descriptions)."""
    if isinstance(value, dict):
        lang = normalize(lang) if lang else _current.get()
        return str(value.get(lang) or value.get(FALLBACK) or next(iter(value.values()), ""))
    return str(value or "")


def client_strings(lang: str | None = None) -> dict[str, str]:
    """The subset of strings needed by ``app.js`` (keys under ``js.``)."""
    lang = normalize(lang) if lang else _current.get()
    merged = {**catalogs()[FALLBACK], **catalogs().get(lang, {})}
    strings = {k[3:]: v for k, v in merged.items() if k.startswith("js.")}
    strings["password_words"] = merged.get("password.words", "")
    return strings
