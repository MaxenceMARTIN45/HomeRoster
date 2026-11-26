"""Status, activity log, settings, web app manifest and health check."""

from __future__ import annotations

import json

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, Response

from ... import db
from ... import security as sec
from ...i18n import languages, set_language, t
from ...providers.base import ServiceError, error_message
from ...templating import render
from ..deps import back, check_csrf, config, page, require_admin, roster, ui_language

router = APIRouter()


@router.get("/status", response_class=HTMLResponse)
async def status_page(request: Request):
    s = require_admin(request)
    health = await roster(request).health_all()
    return page(request, "status.html", s, health=health, last_sync=db.kv_get("last_sync"))


@router.post("/status/sync")
async def status_sync(request: Request, csrf: str = Form("")):
    s = require_admin(request)
    check_csrf(s, csrf)
    report = await roster(request).sync_access(reason="manual")
    if report["ok"]:
        changed = report["changed"]
        return back("/status", t("sync.done_changed", sites=", ".join(changed)) if changed else t("sync.done_unchanged"))
    return back("/status", t("sync.failed", error=report.get("error", "")), "error")


@router.post("/status/test-mail")
async def status_test_mail(request: Request, csrf: str = Form("")):
    s = require_admin(request)
    check_csrf(s, csrf)
    mailer = roster(request).mail
    if not mailer:
        return back("/status", t("errors.not_configured"), "error")
    subject, detail = t("alerts.test_subject"), t("alerts.test_body")
    try:
        await mailer.send(mailer.admin_address, subject,
                          render("emails/alert.html", subject=subject, detail=detail, owner=config(request).owner),
                          detail)
    except ServiceError as e:
        return back("/status", e.message, "error")
    return back("/status", t("alerts.test_sent", email=mailer.admin_address))


@router.get("/activity", response_class=HTMLResponse)
async def activity_page(request: Request):
    s = require_admin(request)
    rows = db.query("SELECT * FROM audit ORDER BY id DESC LIMIT 300")
    return page(request, "activity.html", s, rows=rows)


@router.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request):
    s = require_admin(request)
    admin = db.one("SELECT username, created_at FROM admin WHERE id = 1")
    return page(request, "settings.html", s, admin=admin, people_count=len(db.people()), current=ui_language(request))


@router.post("/settings/language")
async def settings_language(request: Request, csrf: str = Form(""), language: str = Form(...)):
    s = require_admin(request)
    check_csrf(s, csrf)
    if language not in languages():
        return back("/settings", t("settings.language_unknown"), "error")
    db.kv_set("language", language)
    set_language(language)
    return back("/settings", t("settings.language_saved"))


@router.post("/settings/import")
async def settings_import(request: Request, csrf: str = Form(""), toml: str = Form(...)):
    s = require_admin(request)
    check_csrf(s, csrf)
    r = roster(request)
    try:
        result = r.import_toml(toml)
    except Exception as e:  # invalid TOML or structure: shown to the admin as is
        detail = error_message(e) if isinstance(e, ServiceError) else str(e)
        return back("/settings", t("settings.import_invalid", error=detail), "error")
    for slug in result["created"] + result["updated"]:
        person = db.person_by_slug(slug)
        if person:
            await r.link_existing(person["id"])
    report = await r.sync_access(reason="import")
    message = t("settings.import_done", created=len(result["created"]), updated=len(result["updated"]))
    if result["ignored"]:
        message += " " + t("settings.import_ignored", items=", ".join(result["ignored"]))
    message += " " + (t("sync.done_unchanged") if report["ok"] else t("sync.failed", error=report.get("error", "")))
    return back("/people", message, "ok" if report["ok"] else "warn")


@router.post("/settings/password")
async def settings_password(request: Request, csrf: str = Form(""), current: str = Form(...),
                            password: str = Form(...), password2: str = Form(...), code: str = Form(...)):
    s = require_admin(request)
    check_csrf(s, csrf)
    admin = db.one("SELECT * FROM admin WHERE id = 1")
    if not sec.verify_password(admin["password_hash"], current):
        return back("/settings", t("settings.current_password_wrong"), "error")
    if password != password2:
        return back("/settings", t("password.mismatch"), "error")
    problem = sec.password_problem(password, sec.MIN_ADMIN_PASSWORD)
    if problem:
        return back("/settings", problem, "error")
    if not sec.verify_totp(code):
        return back("/settings", t("auth.code_wrong"), "error")
    db.run("UPDATE admin SET password_hash = ? WHERE id = 1", (sec.hash_password(password),))
    sec.destroy_all_sessions()
    db.audit("admin_password_changed")
    response = back("/login", t("settings.password_changed"))
    response.delete_cookie(sec.SESSION_COOKIE, path="/")
    return response


@router.get("/manifest.webmanifest")
async def manifest(request: Request):
    data = {"name": f"HomeRoster · {config(request).owner}", "short_name": "HomeRoster", "start_url": "/people",
            "display": "standalone", "background_color": "#0f1115", "theme_color": "#5b4cf5", "lang": ui_language(request),
            "icons": [{"src": "/static/icon.svg", "sizes": "any", "type": "image/svg+xml", "purpose": "any maskable"},
                      {"src": "/static/icon-192.png", "sizes": "192x192", "type": "image/png"},
                      {"src": "/static/icon-512.png", "sizes": "512x512", "type": "image/png"}]}
    return Response(json.dumps(data), media_type="application/manifest+json")


@router.get("/healthz", include_in_schema=False)
async def healthcheck():
    return {"ok": True}
