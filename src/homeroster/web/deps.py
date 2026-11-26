"""Shared helpers for the routes: access control, CSRF, flash messages, page rendering."""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import quote

import segno
from fastapi import Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from .. import db
from .. import security as sec
from ..config import Config
from ..core.roster import Roster
from ..i18n import client_strings, languages, normalize, t
from ..templating import render

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class Redirect(Exception):
    def __init__(self, url: str):
        self.url = url


def roster(request: Request) -> Roster:
    return request.app.state.roster


def config(request: Request) -> Config:
    return request.app.state.roster.config


def admin_exists() -> bool:
    row = db.one("SELECT totp_confirmed FROM admin WHERE id = 1")
    return bool(row and row["totp_confirmed"])


def ui_language(request: Request) -> str:
    """Interface language: chosen at setup (or in Settings), else the configuration default."""
    if not admin_exists() and request.query_params.get("lang"):
        return normalize(request.query_params["lang"])
    return normalize(db.kv_get("language") or config(request).language)


def session(request: Request) -> dict | None:
    cfg = config(request)
    return sec.get_session(request, idle_minutes=cfg.session_idle_minutes, max_hours=cfg.session_max_hours)


def require_admin(request: Request) -> dict:
    if not admin_exists():
        raise Redirect("/setup")
    s = session(request)
    if not s or s["stage"] != "ok":
        raise Redirect("/login")
    return s


def flash_qs(message: str, kind: str = "ok") -> str:
    return f"flash={quote(message)}&kind={kind}"


def check_csrf(s: dict, token: str) -> None:
    if not sec.check_csrf(s, token):
        raise Redirect("/people?" + flash_qs(t("errors.form_expired"), "error"))


def back(url: str, message: str | None = None, kind: str = "ok") -> RedirectResponse:
    if message:
        url += ("&" if "?" in url else "?") + flash_qs(message, kind)
    return RedirectResponse(url, status_code=303)


def page(request: Request, template: str, s: dict | None = None, status: int = 200, **ctx: Any) -> HTMLResponse:
    kind = request.query_params.get("kind", "ok")
    cfg = config(request)
    html = render(template, request=request, csrf=(s or {}).get("csrf", ""), path=request.url.path,
                  flash=request.query_params.get("flash"), flash_kind=kind if kind in ("ok", "error", "warn") else "ok",
                  owner=cfg.owner, cfg=cfg, apps=roster(request).apps, vpn=roster(request).vpn,
                  languages=languages(), js_strings=client_strings(), **ctx)
    return HTMLResponse(html, status_code=status)


def set_session_cookie(request: Request, response: Response, token: str) -> None:
    response.set_cookie(sec.SESSION_COOKIE, token, **sec.cookie_kwargs(config(request).cookie_secure))


def qr_svg(text: str) -> str:
    return segno.make(text, error="m").svg_inline(scale=6, border=2, dark="#0b0b0b", light="#ffffff", omitsize=True)


def person_or_404(pid: int):
    person = db.person_by_id(pid)
    if not person:
        raise Redirect("/people?" + flash_qs(t("errors.person_missing"), "error"))
    return person
