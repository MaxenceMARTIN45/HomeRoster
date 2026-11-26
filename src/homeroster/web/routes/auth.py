"""First-run setup (language, admin account, two-factor authentication), sign-in and sign-out."""

from __future__ import annotations

import re
import secrets
import time

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from ... import db
from ... import security as sec
from ...i18n import set_language, t
from ..deps import admin_exists, back, config, page, qr_svg, session, set_session_cookie, ui_language

router = APIRouter()
USERNAME_RE = re.compile(r"[A-Za-z0-9_.-]{3,32}")


def _totp_uri(request: Request, admin) -> str:
    return sec.totp_uri(admin["totp_secret"], admin["username"], f"HomeRoster ({config(request).owner})")


@router.get("/", include_in_schema=False)
async def root(request: Request):
    if not admin_exists():
        return RedirectResponse("/setup", 303)
    s = session(request)
    return RedirectResponse("/people" if s and s["stage"] == "ok" else "/login", 303)


# ======================================================================= first-run setup


@router.get("/setup", response_class=HTMLResponse)
async def setup_page(request: Request):
    if admin_exists():
        return RedirectResponse("/login", 303)
    return page(request, "setup.html", error=None, needs_token=True, current=ui_language(request))


@router.post("/setup")
async def setup_submit(request: Request, setup_token: str = Form(""), username: str = Form(...),
                       password: str = Form(...), password2: str = Form(...), language: str = Form("")):
    if admin_exists():
        return RedirectResponse("/login", 303)
    cfg = config(request)
    ip = sec.client_ip(request, cfg.trusted_proxies)
    language = set_language(language or cfg.language)  # render this response in the chosen language
    error = None
    if sec.too_many_failures(ip):
        error = t("auth.too_many_attempts")
    elif not cfg.setup_token:
        error = t("setup.token_missing")
    elif not secrets.compare_digest(setup_token.strip(), cfg.setup_token):
        sec.record_failure(ip)
        error = t("setup.token_wrong")
    elif not USERNAME_RE.fullmatch(username.strip()):
        error = t("setup.username_invalid")
    elif password != password2:
        error = t("password.mismatch")
    else:
        error = sec.password_problem(password, sec.MIN_ADMIN_PASSWORD)
    if error:
        return page(request, "setup.html", error=error, needs_token=True, username=username, current=language, status=400)
    db.kv_set("language", language)
    db.run("DELETE FROM admin")
    db.run("INSERT INTO admin(id, username, password_hash, totp_secret, totp_confirmed, created_at) "
           "VALUES(1, ?, ?, ?, 0, ?)", (username.strip(), sec.hash_password(password), sec.new_totp_secret(), time.time()))
    response = RedirectResponse("/setup/2fa", 303)
    set_session_cookie(request, response, sec.create_session("setup", ip))
    return response


@router.get("/setup/2fa", response_class=HTMLResponse)
async def setup_totp_page(request: Request):
    s = session(request)
    admin = db.one("SELECT * FROM admin WHERE id = 1")
    if not s or s["stage"] != "setup" or not admin or admin["totp_confirmed"]:
        return RedirectResponse("/", 303)
    return page(request, "setup_2fa.html", s, qr=qr_svg(_totp_uri(request, admin)), secret=admin["totp_secret"], error=None)


@router.post("/setup/2fa")
async def setup_totp_submit(request: Request, code: str = Form(...), csrf: str = Form("")):
    s = session(request)
    admin = db.one("SELECT * FROM admin WHERE id = 1")
    if not s or s["stage"] != "setup" or not admin or admin["totp_confirmed"] or not sec.check_csrf(s, csrf):
        return RedirectResponse("/", 303)
    if not sec.verify_totp(code, secret=admin["totp_secret"]):
        return page(request, "setup_2fa.html", s, qr=qr_svg(_totp_uri(request, admin)), secret=admin["totp_secret"],
                    error=t("setup.code_wrong"), status=400)
    db.run("UPDATE admin SET totp_confirmed = 1 WHERE id = 1")
    db.audit("setup", admin["username"])
    response = back("/people", t("setup.done"))
    set_session_cookie(request, response, sec.promote_session(s))
    return response


# ======================================================================= sign-in


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    if not admin_exists():
        return RedirectResponse("/setup", 303)
    return page(request, "login.html", error=None)


@router.post("/login")
async def login_submit(request: Request, username: str = Form(...), password: str = Form(...)):
    ip = sec.client_ip(request, config(request).trusted_proxies)
    admin = db.one("SELECT * FROM admin WHERE id = 1")
    if sec.too_many_failures(ip):
        return page(request, "login.html", error=t("auth.too_many_attempts"), status=429)
    ok = bool(admin) and secrets.compare_digest(username.strip().lower(), admin["username"].lower())
    ok = (sec.verify_password(admin["password_hash"], password) and ok) if admin else False
    if not ok:
        sec.record_failure(ip)
        db.audit("login_failed", username[:40], ip, ok=False)
        return page(request, "login.html", error=t("auth.wrong_credentials"), status=401)
    response = RedirectResponse("/login/code", 303)
    set_session_cookie(request, response, sec.create_session("totp", ip))
    return response


@router.get("/login/code", response_class=HTMLResponse)
async def login_code_page(request: Request):
    s = session(request)
    if not s or s["stage"] != "totp":
        return RedirectResponse("/login", 303)
    return page(request, "login_code.html", s, error=None)


@router.post("/login/code")
async def login_code_submit(request: Request, code: str = Form(...), csrf: str = Form("")):
    s = session(request)
    ip = sec.client_ip(request, config(request).trusted_proxies)
    if not s or s["stage"] != "totp" or not sec.check_csrf(s, csrf):
        return RedirectResponse("/login", 303)
    if sec.too_many_failures(ip):
        return page(request, "login_code.html", s, error=t("auth.too_many_attempts"), status=429)
    if not sec.verify_totp(code):
        sec.record_failure(ip)
        return page(request, "login_code.html", s, error=t("auth.code_wrong"), status=401)
    sec.clear_failures(ip)
    db.audit("login", "", ip)
    response = RedirectResponse("/people", 303)
    set_session_cookie(request, response, sec.promote_session(s))
    return response


@router.post("/logout")
async def logout(request: Request):
    sec.destroy_session(request)
    response = RedirectResponse("/login", 303)
    response.delete_cookie(sec.SESSION_COOKIE, path="/")
    return response
