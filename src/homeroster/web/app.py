"""FastAPI application factory.

Run with ``uvicorn --factory homeroster.web.app:create_app``. The configuration is read from
``$HOMEROSTER_CONFIG`` (default ``/config/homeroster.yaml``).
"""

from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from .. import db
from ..config import Config, load_config
from ..core.roster import Roster
from ..i18n import set_language
from .deps import Redirect, ui_language
from .routes import auth, people, services, system

log = logging.getLogger("homeroster")
STATIC_DIR = Path(__file__).resolve().parent.parent / "static"

CSP = ("default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; font-src 'self'; "
       "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'; object-src 'none'")


async def sync_loop(app: FastAPI) -> None:
    """Background access sync. Reads app.state.roster each time: the Services page may replace it."""
    await asyncio.sleep(15)
    while True:
        roster: Roster = app.state.roster
        await roster.sync_access(reason="auto")
        await asyncio.sleep(max(60, roster.config.sync_interval))


def create_app(config: Config | None = None, *, roster: Roster | None = None, background_sync: bool | None = None) -> FastAPI:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    roster = roster or Roster(config or load_config())
    if background_sync is None:
        background_sync = os.environ.get("HOMEROSTER_NO_SYNC") != "1"

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        db.connect(roster.config.db_path)
        task = asyncio.create_task(sync_loop(app)) if background_sync else None
        log.info("HomeRoster ready: %d app(s), VPN %s, proxy %s", len(roster.apps), roster.vpn.label, roster.proxy.label)
        yield
        if task:
            task.cancel()

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.roster = roster
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.middleware("http")
    async def language_and_security_headers(request: Request, call_next):
        if not request.url.path.startswith("/static"):
            set_language(ui_language(request))
        response = await call_next(request)
        response.headers.setdefault("Content-Security-Policy", CSP)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=(), payment=()"
        response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
        if not request.url.path.startswith("/static"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(Redirect)
    async def redirect_handler(request: Request, exc: Redirect):
        return RedirectResponse(exc.url, status_code=303)

    for module in (auth, people, services, system):
        app.include_router(module.router)
    return app
