"""SQLite storage: admin, sessions, people, grants, linked accounts, jobs, activity log and settings.

User passwords are never stored: they are sent to the apps, then forgotten.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

_lock = threading.RLock()
_conn: sqlite3.Connection | None = None
_path: str | None = None

SCHEMA = """
CREATE TABLE IF NOT EXISTS admin (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    username TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    totp_secret TEXT NOT NULL,
    totp_confirmed INTEGER NOT NULL DEFAULT 0,
    last_totp_step INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,
    csrf TEXT NOT NULL,
    stage TEXT NOT NULL,              -- 'setup', 'totp' (password ok, code expected) or 'ok'
    created_at REAL NOT NULL,
    last_seen REAL NOT NULL,
    ip TEXT
);
CREATE TABLE IF NOT EXISTS login_failures (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ip TEXT,
    ts REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS people (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    slug TEXT NOT NULL UNIQUE,        -- lowercase id, prefix of the device names "slug - device"
    display_name TEXT NOT NULL,
    email TEXT NOT NULL DEFAULT '',
    language TEXT NOT NULL DEFAULT 'en',
    is_admin INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS grants (
    person_id INTEGER NOT NULL REFERENCES people(id) ON DELETE CASCADE,
    app TEXT NOT NULL,
    PRIMARY KEY (person_id, app)
);
CREATE TABLE IF NOT EXISTS accounts (
    person_id INTEGER NOT NULL REFERENCES people(id) ON DELETE CASCADE,
    app TEXT NOT NULL,                -- app id from the configuration
    external_id TEXT NOT NULL,        -- id of the account inside the app
    login TEXT NOT NULL DEFAULT '',   -- what the person types to sign in
    status TEXT NOT NULL DEFAULT 'active',
    created_at REAL NOT NULL,
    PRIMARY KEY (person_id, app)
);
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    person_id INTEGER,
    steps TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL DEFAULT 'running',
    result TEXT NOT NULL DEFAULT '{}',
    created_at REAL NOT NULL,
    finished_at REAL
);
CREATE TABLE IF NOT EXISTS audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    action TEXT NOT NULL,             -- translation key, e.g. 'person_created'
    target TEXT NOT NULL DEFAULT '',
    detail TEXT NOT NULL DEFAULT '',
    ok INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS kv (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

SCHEMA_VERSION = 1


def connect(path: str) -> sqlite3.Connection:
    """Open (or reopen) the database. Called at startup and by the tests."""
    global _conn, _path
    if path != ":memory:":
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with _lock:
        if _conn is not None:
            _conn.close()
        _conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        _conn.row_factory = sqlite3.Row
        _conn.execute("PRAGMA foreign_keys = ON")
        if path != ":memory:":
            _conn.execute("PRAGMA journal_mode = WAL")
        _conn.executescript(SCHEMA)
        _conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        _path = path
    return _conn


def _db() -> sqlite3.Connection:
    if _conn is None:
        raise RuntimeError("database not connected (call db.connect first)")
    return _conn


def query(sql: str, params: tuple | list = ()) -> list[sqlite3.Row]:
    with _lock:
        return _db().execute(sql, params).fetchall()


def one(sql: str, params: tuple | list = ()) -> sqlite3.Row | None:
    with _lock:
        return _db().execute(sql, params).fetchone()


def run(sql: str, params: tuple | list = ()) -> int:
    with _lock:
        cur = _db().execute(sql, params)
        return cur.lastrowid or cur.rowcount


@contextmanager
def transaction() -> Iterator[sqlite3.Connection]:
    with _lock:
        conn = _db()
        conn.execute("BEGIN")
        try:
            yield conn
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise


# ---------------------------------------------------------------- key/value settings and activity log


def kv_get(key: str, default: Any = None) -> Any:
    row = one("SELECT value FROM kv WHERE key = ?", (key,))
    return json.loads(row["value"]) if row else default


def kv_set(key: str, value: Any) -> None:
    run("INSERT INTO kv(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, json.dumps(value)))


def audit(action: str, target: str = "", detail: str = "", ok: bool = True) -> None:
    """Activity log. NEVER put a password or a key in it."""
    run("INSERT INTO audit(ts, action, target, detail, ok) VALUES(?, ?, ?, ?, ?)",
        (time.time(), action, target, detail[:500], 1 if ok else 0))


# ---------------------------------------------------------------- people


def create_person(slug: str, display_name: str, email: str = "", language: str = "en", is_admin: bool = False) -> int:
    now = time.time()
    return run("INSERT INTO people(slug, display_name, email, language, is_admin, created_at, updated_at) "
               "VALUES(?, ?, ?, ?, ?, ?, ?)", (slug, display_name, email, language, 1 if is_admin else 0, now, now))


def person_by_id(pid: int) -> sqlite3.Row | None:
    return one("SELECT * FROM people WHERE id = ?", (pid,))


def person_by_slug(slug: str) -> sqlite3.Row | None:
    return one("SELECT * FROM people WHERE slug = ?", (slug,))


def people() -> list[sqlite3.Row]:
    return query("SELECT * FROM people ORDER BY is_admin DESC, display_name COLLATE NOCASE")


def grants_of(pid: int) -> set[str]:
    return {r["app"] for r in query("SELECT app FROM grants WHERE person_id = ?", (pid,))}


def set_grants(pid: int, apps: set[str]) -> None:
    with transaction() as conn:
        conn.execute("DELETE FROM grants WHERE person_id = ?", (pid,))
        for app in sorted(apps):
            conn.execute("INSERT INTO grants(person_id, app) VALUES(?, ?)", (pid, app))
        conn.execute("UPDATE people SET updated_at = ? WHERE id = ?", (time.time(), pid))


def accounts_of(pid: int) -> dict[str, sqlite3.Row]:
    return {r["app"]: r for r in query("SELECT * FROM accounts WHERE person_id = ?", (pid,))}


def link_account(pid: int, app: str, external_id: str, login: str, status: str = "active") -> None:
    run("""INSERT INTO accounts(person_id, app, external_id, login, status, created_at) VALUES(?, ?, ?, ?, ?, ?)
           ON CONFLICT(person_id, app) DO UPDATE SET external_id = excluded.external_id,
               login = excluded.login, status = excluded.status""",
        (pid, app, str(external_id), login, status, time.time()))


def set_account_status(pid: int, app: str, status: str) -> None:
    run("UPDATE accounts SET status = ? WHERE person_id = ? AND app = ?", (status, pid, app))


# ---------------------------------------------------------------- jobs (live creation progress)


def job_create(job_id: str, kind: str, person_id: int | None, steps: list[dict]) -> None:
    run("INSERT INTO jobs(id, kind, person_id, steps, status, created_at) VALUES(?, ?, ?, ?, 'running', ?)",
        (job_id, kind, person_id, json.dumps(steps), time.time()))


def job_update(job_id: str, *, steps: list[dict] | None = None, status: str | None = None,
               result: dict | None = None) -> None:
    sets, params = [], []
    if steps is not None:
        sets.append("steps = ?")
        params.append(json.dumps(steps))
    if status is not None:
        sets.append("status = ?")
        params.append(status)
        if status != "running":
            sets.append("finished_at = ?")
            params.append(time.time())
    if result is not None:
        sets.append("result = ?")
        params.append(json.dumps(result))
    if sets:
        params.append(job_id)
        run(f"UPDATE jobs SET {', '.join(sets)} WHERE id = ?", params)


def job_get(job_id: str) -> dict | None:
    row = one("SELECT * FROM jobs WHERE id = ?", (job_id,))
    if not row:
        return None
    job = dict(row)
    job["steps"] = json.loads(job["steps"])
    job["result"] = json.loads(job["result"])
    return job
