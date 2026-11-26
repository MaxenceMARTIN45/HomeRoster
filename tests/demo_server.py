"""LOCAL demo server: the real interface, simulated services, made-up people. Never use it in production.

    python tests/demo_server.py            then open http://127.0.0.1:8765
    Sign in: maxence / demo-demo-demo, then the TOTP code printed in the terminal.

Demo-only helpers (used by scripts/take_screenshots.py):
    /__demo/login?next=/people            signs in without the form
    /__demo/p/<slug>[/rest]               redirects to /people/<id of slug>[/rest]
    /__demo/desktop?src=/people           the page in a 1440x900 browser window frame
    /__demo/phone?src=/people&scroll=0    the page in a 390x844 phone frame

The welcome e-mail shows the official app icons (downloaded from the dashboard-icons CDN) and, if
``DEMO_LOGO`` or ``private/homelab-logo.png`` exists, your homelab's logo.
"""

from __future__ import annotations

import asyncio
import copy
import html
import os
import sys
import tempfile
import time
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [os.path.join(HERE, "..", "src"), HERE]
os.environ.update({"HOMEROSTER_DATA": tempfile.mkdtemp(prefix="homeroster-demo-"),
                   "HOMEROSTER_SETUP_TOKEN": "demo-setup-token"})

import httpx  # noqa: E402
import pyotp  # noqa: E402
import uvicorn  # noqa: E402
from fastapi import Request  # noqa: E402
from fastapi.responses import HTMLResponse, RedirectResponse  # noqa: E402

from homeroster import db  # noqa: E402
from homeroster import security as sec  # noqa: E402
from homeroster.config import Config  # noqa: E402
from homeroster.core.roster import Roster  # noqa: E402
from homeroster.providers import base  # noqa: E402
from homeroster.providers.mail import smtp  # noqa: E402
from homeroster.web.app import create_app  # noqa: E402

from fakes import CONFIG, DOMAIN, NEXTCLOUD, FakeHomelab  # noqa: E402

LANGUAGE = os.environ.get("DEMO_LANG", "en")
SECRET = "JBSWY3DPEHPK3PXP"
LOGO = Path(os.environ.get("DEMO_LOGO") or Path(HERE).parent / "private" / "homelab-logo.png")

raw = copy.deepcopy(CONFIG)
raw["homeroster"].update(owner="Maxence", cookie_secure=False, language=LANGUAGE)
if LOGO.is_file():
    raw["homeroster"]["logo"] = str(LOGO)
raw["apps"]["nextcloud"] = {**copy.deepcopy(NEXTCLOUD), "description": {"en": "Files and calendar", "fr": "Fichiers et agenda"},
                            "icon": "cloud", "color": "teal", "logo": "nextcloud"}
config = Config.from_dict(raw)

lab = FakeHomelab()
lab.peers = [
    {"id": "PK-maxence-phone", "name": "maxence - phone", "allowed_ip": "10.8.0.2/32", "status": "running"},
    {"id": "PK-maxence-laptop", "name": "maxence - laptop", "allowed_ip": "10.8.0.3/32", "status": "stopped"},
    {"id": "PK-hugo", "name": "hugo - laptop", "allowed_ip": "10.8.0.4/32", "status": "running"},
    {"id": "PK-hugo-phone", "name": "hugo - phone", "allowed_ip": "10.8.0.5/32", "status": "stopped"},
    {"id": "PK-julia", "name": "julia - phone", "allowed_ip": "10.8.0.6/32", "status": "running"},
    {"id": "PK-alex", "name": "alex - phone", "allowed_ip": "10.8.0.7/32", "status": "running"},
    {"id": "PK-alex-tv", "name": "alex - tv", "allowed_ip": "10.8.0.8/32", "status": "stopped"},
    {"id": "PK-jb", "name": "jb - tablet", "allowed_ip": "10.8.0.9/32", "status": "stopped"},
]
lab.jf_users[0]["Name"] = "Maxence"


def handler(request: httpx.Request) -> httpx.Response:
    """Simulated homelab, except the real dashboard-icons CDN so the e-mail shows the official icons."""
    if request.url.host == "cdn.jsdelivr.net":
        try:
            real = httpx.get(str(request.url), timeout=10, follow_redirects=True)
            return httpx.Response(real.status_code, content=real.content, headers={"content-type": "image/png"})
        except httpx.HTTPError:
            return httpx.Response(404)
    return lab.handler(request)


base.TRANSPORT = httpx.MockTransport(handler)
smtp.SENDER = lambda message: print("[simulated e-mail]", message["To"], message["Subject"])

roster = Roster(config)
db.connect(config.db_path)
db.kv_set("language", LANGUAGE)
db.run("INSERT INTO admin(id, username, password_hash, totp_secret, totp_confirmed, created_at) VALUES(1, 'maxence', ?, ?, 1, ?)",
       (sec.hash_password("demo-demo-demo"), SECRET, time.time()))
roster.import_toml('admins = ["maxence"]\n[users]\n'
                   'hugo = ["jellyfin", "seerr", "immich", "nextcloud"]\n'
                   'julia = ["jellyfin", "seerr", "immich", "openwebui"]\n'
                   'alex = ["jellyfin", "seerr", "openwebui"]\n'
                   'jb = ["immich"]\n')
for slug, name, lang in (("maxence", "Maxence", "en"), ("hugo", "Hugo", "en"), ("julia", "Julia", "en"),
                         ("alex", "Alex", "fr"), ("jb", "JB", "en")):
    db.run("UPDATE people SET display_name = ?, email = ?, language = ? WHERE slug = ?", (name, f"{slug}@{DOMAIN}", lang, slug))


async def _accounts() -> None:
    for slug in ("hugo", "julia", "alex", "jb"):
        person = db.person_by_slug(slug)
        for app_id in db.grants_of(person["id"]):
            await roster.ensure_account(person["id"], app_id, "Demo-Password-2026")
    await roster.sync_access(reason="demo")
    db.set_account_status(db.person_by_slug("jb")["id"], "immich", "disabled")


asyncio.run(_accounts())

# A finished creation job, to show the live progress page.
julia = db.person_by_slug("julia")
steps = roster.plan_steps({"jellyfin", "seerr", "immich", "openwebui"}, True)
for step in steps:
    step.update(status="ok", message="")
steps[0]["message"], steps[-1]["message"] = "julia - phone · 10.8.0.6", f"sent to julia@{DOMAIN}"
db.job_create("demo-job", "creation", julia["id"], steps)
db.job_update("demo-job", steps=steps, status="done",
              result={"person_id": julia["id"], "device_id": "PK-julia", "device_name": "julia - phone"})

app = create_app(roster=roster, background_sync=False)


def _local(path: str, default: str = "/people") -> str:
    """A same-site path; /__demo/p/<slug>/... becomes the real /people/<id>/... page."""
    path = path if path.startswith("/") and not path.startswith("//") else default
    if path.startswith("/__demo/p/"):
        slug, _, rest = path.removeprefix("/__demo/p/").partition("/")
        person = db.person_by_slug(slug)
        path = f"/people/{person['id'] if person else 0}" + (f"/{rest}" if rest else "")
    return path


@app.get("/__demo/login", include_in_schema=False)
async def demo_login(request: Request, next: str = "/people"):
    response = RedirectResponse(_local(next), status_code=303)
    response.set_cookie(sec.SESSION_COOKIE, sec.create_session("ok", "127.0.0.1"), **sec.cookie_kwargs(False))
    return response


@app.get("/__demo/p/{rest:path}", include_in_schema=False)
async def demo_person(rest: str):
    return RedirectResponse(_local(f"/__demo/p/{rest}"), status_code=303)


FRAME_CSS = """
html,body{margin:0;height:100%;overflow:hidden;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif}
body{display:grid;place-items:center;background:radial-gradient(circle at 18% 12%,#9c8cff 0,transparent 42%),
     radial-gradient(circle at 85% 90%,#2bc4f0 0,transparent 38%),linear-gradient(135deg,#7b6dff,#3a2fb8)}
iframe{border:0;display:block;background:#0e1015}
.phone iframe{border-radius:34px;box-shadow:0 0 0 10px #111320,0 30px 80px rgba(10,8,40,.55)}
.window{border-radius:14px;overflow:hidden;box-shadow:0 0 0 1px rgba(255,255,255,.14),0 40px 100px rgba(10,8,40,.55)}
.bar{height:44px;display:flex;align-items:center;gap:8px;padding:0 16px;background:#eceef4;border-bottom:1px solid #d9dce6}
.dark .bar{background:#1b1e27;border-bottom-color:#2a2f3c}
.dot{width:12px;height:12px;border-radius:50%}
.url{margin:0 auto;transform:translateX(-30px);width:520px;height:28px;border-radius:9px;background:#fff;color:#5b6070;
     font-size:13px;display:flex;align-items:center;justify-content:center;gap:6px}
.dark .url{background:#262a35;color:#a8aec0}
"""
LOCK = ("<svg width='12' height='12' viewBox='0 0 24 24' fill='none' stroke='currentColor' stroke-width='2.4' "
        "stroke-linecap='round'><rect x='5' y='11' width='14' height='10' rx='2'/><path d='M8 11V7a4 4 0 0 1 8 0v4'/></svg>")
FRAME_CSP = "default-src 'self'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; frame-src 'self'; img-src 'self' data:"
SCROLL_JS = ("<script>const f=document.querySelector('iframe');f.addEventListener('load',()=>{{"
             "f.contentWindow.scrollTo(0,{scroll});}});</script>")


def _frame(body: str, scroll: int) -> HTMLResponse:
    script = SCROLL_JS.format(scroll=int(scroll)) if scroll else ""
    return HTMLResponse(f"<!doctype html><html><head><style>{FRAME_CSS}</style></head>{body}{script}</html>",
                        headers={"Content-Security-Policy": FRAME_CSP})


@app.get("/__demo/phone", include_in_schema=False)
async def demo_phone(src: str = "/people", scroll: int = 0):
    """The page in a 390x844 phone frame (headless Chrome windows cannot be that narrow)."""
    return _frame(f"<body class='phone'><iframe src='{html.escape(_local(src))}' width='390' height='844'></iframe></body>", scroll)


@app.get("/__demo/desktop", include_in_schema=False)
async def demo_desktop(src: str = "/people", dark: bool = False, scroll: int = 0):
    """The page in a 1440x900 browser window, so every desktop screenshot has the same size and frame."""
    src = _local(src)
    shown = f"roster.{DOMAIN}{src.split('?')[0]}"
    dots = "".join(f"<span class='dot' style='background:{c}'></span>" for c in ("#ff5f57", "#febc2e", "#28c840"))
    return _frame(f"<body class='{'dark' if dark else ''}'><div class='window'><div class='bar'>{dots}"
                  f"<div class='url'>{LOCK}{html.escape(shown)}</div></div>"
                  f"<iframe src='{html.escape(src)}' width='1440' height='900'></iframe></div></body>", scroll)


@app.middleware("http")
async def allow_demo_frame(request: Request, call_next):
    response = await call_next(request)
    if "x-frame-options" in response.headers:
        del response.headers["x-frame-options"]
    csp = response.headers.get("content-security-policy", "")
    response.headers["content-security-policy"] = csp.replace("frame-ancestors 'none'", "frame-ancestors 'self'")
    return response


if __name__ == "__main__":
    print(f"\n  Demo: http://127.0.0.1:8765  (maxence / demo-demo-demo, TOTP now: {pyotp.TOTP(SECRET).now()})\n")
    uvicorn.run(app, host="127.0.0.1", port=int(os.environ.get("PORT", "8765")))
