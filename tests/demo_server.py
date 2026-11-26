"""LOCAL demo server: the real interface, simulated services, made-up people. Never use it in production.

    python tests/demo_server.py            then open http://127.0.0.1:8765
    Sign in: demo / demo-demo-demo, then the TOTP code printed in the terminal.
    Shortcut for screenshots: http://127.0.0.1:8765/__demo/login?next=/people (demo only).
"""

from __future__ import annotations

import asyncio
import copy
import os
import sys
import tempfile
import time

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

from fakes import CONFIG, NEXTCLOUD, FakeHomelab  # noqa: E402

LANGUAGE = os.environ.get("DEMO_LANG", "en")
SECRET = "JBSWY3DPEHPK3PXP"

raw = copy.deepcopy(CONFIG)
raw["homeroster"].update(cookie_secure=False, language=LANGUAGE)
raw["apps"]["nextcloud"] = {**copy.deepcopy(NEXTCLOUD), "description": {"en": "Files and calendar", "fr": "Fichiers et agenda"},
                            "icon": "cloud", "color": "teal"}
config = Config.from_dict(raw)

lab = FakeHomelab()
lab.peers[2]["status"] = "running"
lab.peers += [
    {"id": "PK-riley", "name": "riley - phone", "allowed_ip": "10.8.0.5/32", "status": "running"},
    {"id": "PK-riley-tv", "name": "riley - tv", "allowed_ip": "10.8.0.6/32", "status": "stopped"},
    {"id": "PK-morgan", "name": "morgan - phone", "allowed_ip": "10.8.0.7/32", "status": "stopped"},
    {"id": "PK-jamie", "name": "jamie - tablet", "allowed_ip": "10.8.0.8/32", "status": "running"},
]
base.TRANSPORT = httpx.MockTransport(lab.handler)
smtp.SENDER = lambda message: print("[simulated e-mail]", message["To"], message["Subject"])

roster = Roster(config)
db.connect(config.db_path)
db.kv_set("language", LANGUAGE)
db.run("INSERT INTO admin(id, username, password_hash, totp_secret, totp_confirmed, created_at) VALUES(1, 'demo', ?, ?, 1, ?)",
       (sec.hash_password("demo-demo-demo"), SECRET, time.time()))
roster.import_toml('admins = ["alex"]\n[users]\n'
                   'sam = ["jellyfin", "seerr", "immich", "nextcloud"]\n'
                   'riley = ["jellyfin", "seerr", "openwebui"]\n'
                   'morgan = ["immich"]\n'
                   'jamie = ["jellyfin", "seerr", "immich", "openwebui", "nextcloud"]\n')
for slug, email, lang in (("alex", "alex@example.com", "en"), ("sam", "sam@example.com", "en"),
                          ("riley", "riley@example.com", "fr"), ("morgan", "morgan@example.com", "en"),
                          ("jamie", "jamie@example.com", "en")):
    db.run("UPDATE people SET email = ?, language = ? WHERE slug = ?", (email, lang, slug))


async def _accounts() -> None:
    for slug in ("sam", "riley", "morgan", "jamie"):
        person = db.person_by_slug(slug)
        for app_id in db.grants_of(person["id"]):
            await roster.ensure_account(person["id"], app_id, "Demo-Password-2026")
    await roster.sync_access(reason="demo")
    morgan = db.person_by_slug("morgan")
    db.set_account_status(morgan["id"], "immich", "disabled")


asyncio.run(_accounts())

# A finished creation job, to show the live progress page.
jamie = db.person_by_slug("jamie")
steps = roster.plan_steps({"jellyfin", "seerr", "immich", "openwebui", "nextcloud"}, True)
for step in steps:
    step.update(status="ok", message="")
steps[0]["message"], steps[-1]["message"] = "jamie - tablet · 10.8.0.8", "sent to jamie@example.com"
db.job_create("demo-job", "creation", jamie["id"], steps)
db.job_update("demo-job", steps=steps, status="done",
              result={"person_id": jamie["id"], "device_id": "PK-jamie", "device_name": "jamie - tablet"})

app = create_app(roster=roster, background_sync=False)


@app.get("/__demo/login", include_in_schema=False)
async def demo_login(request: Request, next: str = "/people"):
    response = RedirectResponse(next if next.startswith("/") else "/people", status_code=303)
    response.set_cookie(sec.SESSION_COOKIE, sec.create_session("ok", "127.0.0.1"), **sec.cookie_kwargs(False))
    return response


PHONE_CSS = ("html,body{margin:0;height:100%;overflow:hidden}"
             "body{display:grid;place-items:center;background:linear-gradient(135deg,#7b6dff,#3a2fb8)}"
             "iframe{border:0;display:block;border-radius:34px;background:#0e1015;"
             "box-shadow:0 0 0 10px #111320,0 30px 80px rgba(10,8,40,.55)}")


@app.get("/__demo/phone", include_in_schema=False)
async def demo_phone(src: str = "/people"):
    """The page in a 390x844 phone frame, for README screenshots (headless Chrome windows cannot be that narrow)."""
    src = src if src.startswith("/") else "/people"
    return HTMLResponse(f"<!doctype html><html><head><style>{PHONE_CSS}</style></head><body>"
                        f"<iframe src='{src}' width='390' height='844'></iframe></body></html>",
                        headers={"Content-Security-Policy": "default-src 'self'; style-src 'unsafe-inline'; frame-src 'self'"})


@app.middleware("http")
async def allow_demo_frame(request: Request, call_next):
    response = await call_next(request)
    if "x-frame-options" in response.headers:
        del response.headers["x-frame-options"]
    csp = response.headers.get("content-security-policy", "")
    response.headers["content-security-policy"] = csp.replace("frame-ancestors 'none'", "frame-ancestors 'self'")
    return response


if __name__ == "__main__":
    print(f"\n  Demo: http://127.0.0.1:8765  (demo / demo-demo-demo, TOTP now: {pyotp.TOTP(SECRET).now()})\n")
    uvicorn.run(app, host="127.0.0.1", port=int(os.environ.get("PORT", "8765")))
