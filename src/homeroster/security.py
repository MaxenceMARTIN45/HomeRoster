"""Security: admin password (argon2), two-factor authentication (TOTP), sessions, CSRF and brute-force protection."""

from __future__ import annotations

import hashlib
import hmac
import secrets
import time

import pyotp
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from fastapi import Request

from . import db
from .i18n import t

_hasher = PasswordHasher()

SESSION_COOKIE = "homeroster_session"
MAX_FAILURES = 5             # failed attempts allowed...
FAILURE_WINDOW = 15 * 60     # ...per 15-minute window and per IP
PENDING_SESSION_TTL = 5 * 60  # a half-finished sign-in expires after 5 minutes
MIN_ADMIN_PASSWORD = 12
MIN_USER_PASSWORD = 10


# ---------------------------------------------------------------- passwords


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerificationError, InvalidHashError):
        return False


def generate_password(lang: str | None = None) -> str:
    """A strong password that is easy to read out loud: ``Tiger-Lamp-42-Orchard``."""
    words = t("password.words", lang).split()
    picked = [secrets.choice(words).capitalize() for _ in range(3)]
    return f"{picked[0]}-{picked[1]}-{secrets.randbelow(90) + 10}-{picked[2]}"


def password_problem(password: str, minimum: int = MIN_USER_PASSWORD) -> str | None:
    if len(password) < minimum:
        return t("password.too_short", min=minimum)
    if len(set(password)) < 5:
        return t("password.too_repetitive")
    return None


# ---------------------------------------------------------------- two-factor authentication


def new_totp_secret() -> str:
    return pyotp.random_base32()


def totp_uri(secret: str, username: str, issuer: str) -> str:
    return pyotp.TOTP(secret).provisioning_uri(name=username, issuer_name=issuer)


def verify_totp(code: str, *, secret: str | None = None, consume: bool = True) -> bool:
    """Check a 6-digit code. A code that was already used is rejected (replay protection)."""
    code = (code or "").replace(" ", "")
    if not code.isdigit() or len(code) != 6:
        return False
    admin = db.one("SELECT * FROM admin WHERE id = 1")
    if secret is None:
        if not admin:
            return False
        secret = admin["totp_secret"]
    totp = pyotp.TOTP(secret)
    now = int(time.time())
    last = admin["last_totp_step"] if admin else 0
    for offset in (-1, 0, 1):  # tolerate 30 s of clock drift
        step = now // 30 + offset
        if step <= last:
            continue
        if hmac.compare_digest(totp.at(step * 30), code):
            if consume and admin:
                db.run("UPDATE admin SET last_totp_step = ? WHERE id = 1", (step,))
            return True
    return False


# ---------------------------------------------------------------- client IP and brute-force protection


def client_ip(request: Request, trusted_proxies: list[str]) -> str:
    """Real client IP. ``X-Real-IP`` is only trusted when the request comes from the reverse proxy."""
    peer = request.client.host if request.client else ""
    if peer in trusted_proxies:
        real = request.headers.get("x-real-ip", "").strip()
        if real:
            return real
    return peer


def too_many_failures(ip: str) -> bool:
    since = time.time() - FAILURE_WINDOW
    row = db.one("SELECT COUNT(*) AS n FROM login_failures WHERE ip = ? AND ts > ?", (ip, since))
    return bool(row and row["n"] >= MAX_FAILURES)


def record_failure(ip: str) -> None:
    db.run("INSERT INTO login_failures(ip, ts) VALUES(?, ?)", (ip, time.time()))
    db.run("DELETE FROM login_failures WHERE ts < ?", (time.time() - 24 * 3600,))


def clear_failures(ip: str) -> None:
    db.run("DELETE FROM login_failures WHERE ip = ?", (ip,))


# ---------------------------------------------------------------- sessions and CSRF


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_session(stage: str, ip: str) -> str:
    token = secrets.token_urlsafe(32)
    now = time.time()
    db.run("INSERT INTO sessions(token_hash, csrf, stage, created_at, last_seen, ip) VALUES(?, ?, ?, ?, ?, ?)",
           (_hash_token(token), secrets.token_urlsafe(24), stage, now, now, ip))
    return token


def get_session(request: Request, *, idle_minutes: int, max_hours: int) -> dict | None:
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return None
    row = db.one("SELECT * FROM sessions WHERE token_hash = ?", (_hash_token(token),))
    if not row:
        return None
    now = time.time()
    idle_limit = idle_minutes * 60 if row["stage"] == "ok" else PENDING_SESSION_TTL
    if now - row["last_seen"] > idle_limit or now - row["created_at"] > max_hours * 3600:
        db.run("DELETE FROM sessions WHERE token_hash = ?", (row["token_hash"],))
        return None
    db.run("UPDATE sessions SET last_seen = ? WHERE token_hash = ?", (now, row["token_hash"]))
    return dict(row)


def promote_session(session: dict) -> str:
    """Turn a half-finished session into a full one, with a new token (session fixation protection)."""
    db.run("DELETE FROM sessions WHERE token_hash = ?", (session["token_hash"],))
    return create_session("ok", session.get("ip") or "")


def destroy_session(request: Request) -> None:
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        db.run("DELETE FROM sessions WHERE token_hash = ?", (_hash_token(token),))


def destroy_all_sessions() -> None:
    db.run("DELETE FROM sessions")


def check_csrf(session: dict | None, token: str | None) -> bool:
    return bool(session and token and hmac.compare_digest(session["csrf"], token))


def cookie_kwargs(secure: bool) -> dict:
    return {"httponly": True, "secure": secure, "samesite": "strict", "path": "/"}
