"""People: list, creation (live job), person page, apps, devices, passwords and removal."""

from __future__ import annotations

import asyncio
import re
import secrets
import time
from urllib.parse import quote

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

from ... import db
from ... import security as sec
from ...i18n import current_language, languages, normalize, t
from ...naming import DEVICE_RE, SLUG_RE, device_slug, slugify
from ...providers.base import ServiceError
from ...providers.vpn.base import INVITE_PREFIX
from ..deps import EMAIL_RE, back, check_csrf, config, page, person_or_404, qr_svg, require_admin, roster

router = APIRouter()

# Keep a reference to background jobs so they are not garbage-collected mid-run.
_jobs: set[asyncio.Task] = set()


async def _devices_safe(request: Request) -> tuple[dict, str | None]:
    try:
        by_person, _ = await roster(request).devices_by_person()
        return by_person, None
    except ServiceError as e:
        return {}, e.message


# ======================================================================= list and creation


@router.get("/people", response_class=HTMLResponse)
async def people_page(request: Request):
    s = require_admin(request)
    devices, vpn_error = await _devices_safe(request)
    rows = []
    for person in db.people():
        own = devices.get(person["slug"], [])
        rows.append({"p": person, "grants": db.grants_of(person["id"]), "devices": own,
                     "online": any(d.online for d in own)})
    stats = {"people": len(rows), "online": sum(1 for r in rows if r["online"]),
             "devices": sum(len(r["devices"]) for r in rows)}
    return page(request, "people.html", s, rows=rows, stats=stats, vpn_error=vpn_error, last_sync=db.kv_get("last_sync"))


def _new_form(request: Request, s: dict, form: dict, errors: dict, status: int = 200) -> HTMLResponse:
    return page(request, "person_new.html", s, form=form, errors=errors, status=status,
                suggestion=sec.generate_password(), default_device=config(request).default_device)


@router.get("/people/new", response_class=HTMLResponse)
async def new_person_page(request: Request):
    s = require_admin(request)
    return _new_form(request, s, {}, {})


@router.post("/people/new")
async def new_person_submit(request: Request):
    s = require_admin(request)
    form = await request.form()
    check_csrf(s, str(form.get("csrf", "")))
    r = roster(request)
    cfg = config(request)
    name = str(form.get("display_name") or "").strip()
    email = str(form.get("email") or "").strip()
    password = str(form.get("password") or "")
    language = normalize(str(form.get("language") or current_language()))
    device = device_slug(str(form.get("device") or cfg.default_device)) or cfg.default_device
    app_ids = {a for a in r.apps if form.get(f"app_{a}")}
    # An app signing in through another one needs that account too (Seerr -> Jellyfin).
    for app_id in list(app_ids):
        parent = r.apps[app_id].parent
        while parent:
            app_ids.add(parent)
            parent = r.apps[parent].parent
    send_email = bool(form.get("send_email"))
    slug = slugify(str(form.get("slug") or name))
    errors: dict[str, str] = {}
    if not name or len(name) > 40:
        errors["display_name"] = t("people.name_invalid")
    if not SLUG_RE.fullmatch(slug or ""):
        errors["slug"] = t("people.slug_invalid")
    elif db.person_by_slug(slug):
        errors["slug"] = t("people.slug_taken", slug=slug)
    if not DEVICE_RE.fullmatch(device):
        errors["device"] = t("people.device_invalid")
    if email and not EMAIL_RE.fullmatch(email):
        errors["email"] = t("people.email_invalid")
    needs_email = [r.apps[a].label for a in app_ids if r.apps[a].requires_email]
    if not email and (needs_email or send_email):
        errors["email"] = t("people.email_required", apps=", ".join(needs_email + ([t("steps.email")] if send_email else [])))
    if any(r.apps[a].manages_password for a in app_ids):
        problem = sec.password_problem(password)
        if problem:
            errors["password"] = problem
        elif password != str(form.get("password2") or ""):
            errors["password"] = t("password.mismatch")
    if errors:
        kept = {k: form.get(k) for k in ("display_name", "slug", "email", "device")}
        return _new_form(request, s, {**kept, "apps": app_ids, "send_email": send_email, "language": language},
                         errors, status=400)
    pid = db.create_person(slug, name, email, language)
    db.set_grants(pid, app_ids)
    job_id = secrets.token_urlsafe(12)
    db.job_create(job_id, "creation", pid, r.plan_steps(app_ids, send_email))
    task = asyncio.create_task(r.run_creation(job_id, pid, password, app_ids, device, send_email))
    _jobs.add(task)
    task.add_done_callback(_jobs.discard)
    return RedirectResponse(f"/jobs/{job_id}", 303)


@router.get("/jobs/{job_id}", response_class=HTMLResponse)
async def job_page(request: Request, job_id: str):
    s = require_admin(request)
    job = db.job_get(job_id)
    if not job:
        return back("/people", t("jobs.missing"), "error")
    person = db.person_by_id(job["person_id"]) if job["person_id"] else None
    return page(request, "job.html", s, job=job, person=person)


@router.get("/api/jobs/{job_id}")
async def job_api(request: Request, job_id: str):
    require_admin(request)
    job = db.job_get(job_id)
    if not job:
        return JSONResponse({"error": "not found"}, status_code=404)
    return JSONResponse({"status": job["status"], "steps": job["steps"], "result": job["result"]})


# ======================================================================= person page


@router.get("/people/{pid}", response_class=HTMLResponse)
async def person_page(request: Request, pid: int):
    s = require_admin(request)
    person = person_or_404(pid)
    devices, vpn_error = await _devices_safe(request)
    return page(request, "person.html", s, p=person, grants=db.grants_of(pid), accounts=db.accounts_of(pid),
                devices=devices.get(person["slug"], []), vpn_error=vpn_error, suggestion=sec.generate_password(),
                open_qr=request.query_params.get("qr", ""))


@router.post("/people/{pid}/profile")
async def person_update(request: Request, pid: int, csrf: str = Form(""), display_name: str = Form(...),
                        email: str = Form(""), language: str = Form(""), is_admin: str = Form("")):
    s = require_admin(request)
    check_csrf(s, csrf)
    person = person_or_404(pid)
    display_name, email = display_name.strip(), email.strip()
    if not display_name or len(display_name) > 40:
        return back(f"/people/{pid}", t("people.name_invalid"), "error")
    if email and not EMAIL_RE.fullmatch(email):
        return back(f"/people/{pid}", t("people.email_invalid"), "error")
    language = language if language in languages() else person["language"]
    db.run("UPDATE people SET display_name = ?, email = ?, language = ?, is_admin = ?, updated_at = ? WHERE id = ?",
           (display_name, email, language, 1 if is_admin else 0, time.time(), pid))
    if language != person["language"]:
        await roster(request).apply_language(pid)  # Jellyfin, Seerr, Nextcloud... follow the new language
    db.audit("profile_updated", person["slug"], f"admin={'yes' if is_admin else 'no'}")
    report = await roster(request).sync_access(reason="profile updated")
    if report["ok"]:
        return back(f"/people/{pid}", t("people.profile_saved"))
    return back(f"/people/{pid}", t("people.profile_saved_sync_failed", error=report.get("error", "")), "warn")


@router.post("/people/{pid}/apps/{app_id}")
async def person_app(request: Request, pid: int, app_id: str, csrf: str = Form(""), action: str = Form(...),
                     password: str = Form("")):
    s = require_admin(request)
    check_csrf(s, csrf)
    person_or_404(pid)
    r = roster(request)
    if app_id not in r.apps:
        return back(f"/people/{pid}", t("errors.unknown_app"), "error")
    if action == "grant":
        if password:
            problem = sec.password_problem(password)
            if problem:
                return back(f"/people/{pid}", problem, "error")
        result = await r.grant(pid, app_id, password or None)
    else:
        result = await r.revoke(pid, app_id)
    ok = result["ok"] and result["sync"]["ok"]
    return back(f"/people/{pid}", f"{r.apps[app_id].label}: {result['message']}", "ok" if ok else "error")


@router.post("/people/{pid}/password")
async def person_password(request: Request, pid: int):
    s = require_admin(request)
    form = await request.form()
    check_csrf(s, str(form.get("csrf", "")))
    person_or_404(pid)
    r = roster(request)
    app_ids = [a for a in r.apps if form.get(f"app_{a}")]
    password = str(form.get("password") or "")
    if not app_ids:
        return back(f"/people/{pid}", t("people.pick_an_app"), "error")
    problem = sec.password_problem(password)
    if problem:
        return back(f"/people/{pid}", problem, "error")
    if not sec.verify_totp(str(form.get("code", ""))):
        return back(f"/people/{pid}", t("auth.code_wrong_nothing_changed"), "error")
    results = await r.reset_password(pid, app_ids, password)
    detail = " · ".join(f"{x['label']}: {x['message']}" for x in results)
    return back(f"/people/{pid}", detail, "ok" if all(x["ok"] for x in results) else "warn")


@router.post("/people/{pid}/devices")
async def person_add_device(request: Request, pid: int, csrf: str = Form(""), device: str = Form(...)):
    s = require_admin(request)
    check_csrf(s, csrf)
    person_or_404(pid)
    name = device_slug(device)
    if not DEVICE_RE.fullmatch(name or ""):
        return back(f"/people/{pid}", t("people.device_invalid"), "error")
    try:
        created = await roster(request).add_device(pid, name)
    except ServiceError as e:
        return back(f"/people/{pid}", e.message, "error")
    return back(f"/people/{pid}?qr={quote(created.id)}", t("people.device_created", name=created.name))


@router.post("/people/{pid}/devices/delete")
async def person_delete_device(request: Request, pid: int, csrf: str = Form(""), device_id: str = Form(...)):
    s = require_admin(request)
    check_csrf(s, csrf)
    person_or_404(pid)
    try:
        name = await roster(request).delete_device(pid, device_id)
    except ServiceError as e:
        return back(f"/people/{pid}", e.message, "error")
    return back(f"/people/{pid}", t("people.device_deleted", name=name))


async def _device_config(request: Request, pid: int, device_id: str) -> tuple[str, str]:
    return await roster(request).device_access(pid, device_id)


@router.get("/people/{pid}/devices/qr")
async def person_device_qr(request: Request, pid: int, device: str):
    require_admin(request)
    person_or_404(pid)
    try:
        name, conf = await _device_config(request, pid, device)
    except ServiceError as e:
        return HTMLResponse(f'<p class="qr-error">{e.message}</p>', status_code=502)
    db.audit("qr_shown", name)
    return HTMLResponse(qr_svg(conf))


@router.get("/people/{pid}/devices/conf")
async def person_device_conf(request: Request, pid: int, device: str):
    require_admin(request)
    person_or_404(pid)
    try:
        name, conf = await _device_config(request, pid, device)
    except ServiceError as e:
        return back(f"/people/{pid}", e.message, "error")
    db.audit("config_downloaded", name)
    extension = ".txt" if device.startswith(INVITE_PREFIX) else ".conf"
    filename = re.sub(r"[^a-z0-9_-]+", "-", name.lower()).strip("-") + extension
    return Response(conf, media_type="application/octet-stream",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@router.post("/people/{pid}/welcome")
async def person_welcome(request: Request, pid: int, csrf: str = Form("")):
    s = require_admin(request)
    check_csrf(s, csrf)
    person_or_404(pid)
    try:
        message = await roster(request).send_welcome(pid)
    except ServiceError as e:
        return back(f"/people/{pid}", e.message, "error")
    return back(f"/people/{pid}", t("email.welcome_sent", detail=message))


@router.get("/people/{pid}/welcome/preview", response_class=HTMLResponse)
async def person_welcome_preview(request: Request, pid: int):
    """The welcome e-mail as the person will receive it (inline images served from /static)."""
    require_admin(request)
    person_or_404(pid)
    _, html, _, images = await roster(request).welcome_message(pid)
    for image in images:
        html = html.replace(f'src="cid:{image.cid}"', f'src="/people/{pid}/welcome/logo/{image.cid}"')
    # E-mails rely on inline styles: allow them on this page only (still no script, no external resource).
    csp = "default-src 'none'; img-src 'self' data:; style-src 'unsafe-inline'; frame-ancestors 'none'; base-uri 'none'"
    return HTMLResponse(html, headers={"Content-Security-Policy": csp})


@router.get("/people/{pid}/welcome/logo/{cid}")
async def person_welcome_logo(request: Request, pid: int, cid: str):
    require_admin(request)
    r = roster(request)
    app_id = cid.removeprefix("app-")
    if cid != "homelab" and (not cid.startswith("app-") or app_id not in r.apps):
        return Response(status_code=404)
    image = await r.logo(None if cid == "homelab" else app_id)
    if not image:
        return Response(status_code=404)
    return Response(image.data, media_type=f"image/{image.subtype}", headers={"Cache-Control": "private, max-age=3600"})


@router.post("/people/{pid}/link")
async def person_link(request: Request, pid: int, csrf: str = Form("")):
    s = require_admin(request)
    check_csrf(s, csrf)
    person_or_404(pid)
    found = await roster(request).link_existing(pid)
    return back(f"/people/{pid}", t("people.linked", apps=", ".join(found) or t("people.linked_none")))


@router.post("/people/{pid}/delete")
async def person_delete(request: Request, pid: int, csrf: str = Form(""), confirm: str = Form(""),
                        code: str = Form(""), delete_accounts: str = Form("")):
    s = require_admin(request)
    check_csrf(s, csrf)
    person = person_or_404(pid)
    if confirm.strip().lower() not in (person["slug"], person["display_name"].lower()):
        return back(f"/people/{pid}", t("people.confirm_name"), "error")
    if not sec.verify_totp(code):
        return back(f"/people/{pid}", t("auth.code_wrong_nothing_deleted"), "error")
    results = await roster(request).delete_person(pid, delete_accounts=bool(delete_accounts))
    detail = " · ".join(f"{x['label']}: {x['message']}" for x in results)
    kind = "ok" if all(x["ok"] for x in results) else "warn"
    return back("/people", t("people.deleted", name=person["display_name"], detail=detail), kind)
