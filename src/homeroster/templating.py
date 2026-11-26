"""Jinja2 environment shared by the web pages and the e-mails."""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape

from . import __version__
from .i18n import current_language, languages, localized, t

TEMPLATES_DIR = Path(__file__).parent / "templates"

env = Environment(loader=FileSystemLoader(TEMPLATES_DIR), autoescape=select_autoescape(["html", "xml"]),
                  trim_blocks=True, lstrip_blocks=True)


def _date(ts: float | None, fmt: str | None = None) -> str:
    if not ts:
        return "—"
    return dt.datetime.fromtimestamp(ts).strftime(fmt or t("format.datetime"))


def _relative(ts: float | None) -> str:
    if not ts:
        return t("time.never")
    delta = dt.datetime.now().timestamp() - ts
    if delta < 60:
        return t("time.just_now")
    if delta < 3600:
        return t("time.minutes_ago", n=int(delta // 60))
    if delta < 86400:
        return t("time.hours_ago", n=int(delta // 3600))
    return t("time.days_ago", n=int(delta // 86400))


def _initials(name: str) -> str:
    parts = [p for p in (name or "?").replace("-", " ").split() if p]
    if len(parts) >= 2:
        return (parts[0][0] + parts[1][0]).upper()
    return (name or "?")[:2].upper()


def _tint(name: str) -> str:
    tints = ["violet", "sky", "emerald", "amber", "rose", "indigo", "teal", "orange"]
    return tints[sum(ord(c) for c in (name or "")) % len(tints)]


env.filters.update(date=_date, relative=_relative, initials=_initials, tint=_tint, localized=localized)
env.globals.update(t=t, version=__version__, LANGUAGES=languages)


def render(name: str, *, lang: str | None = None, **ctx: Any) -> str:
    """Render a template. ``lang`` forces a language (e-mails use the recipient's language)."""
    if lang:
        ctx["t"] = lambda key, **params: t(key, lang, **params)
    ctx.setdefault("lang", lang or current_language())
    return env.get_template(name).render(**ctx)
