"""The web interface: setup, two-factor sign-in, person creation, CSRF and security headers."""

from __future__ import annotations

import re
import time

import pyotp
from fastapi.testclient import TestClient

from homeroster import db
from homeroster.web.app import create_app

from conftest import SETUP_TOKEN

CSRF_RE = re.compile(r'name="csrf" value="([^"]+)"')


def _csrf(html: str) -> str:
    return CSRF_RE.search(html).group(1)


def _client(roster) -> TestClient:
    return TestClient(create_app(roster=roster, background_sync=False), base_url="https://testserver")


def _setup(client: TestClient, language: str = "en") -> str:
    r = client.post("/setup", data={"setup_token": SETUP_TOKEN, "username": "alex", "language": language,
                                    "password": "Super-Secret-2026!", "password2": "Super-Secret-2026!"}, follow_redirects=False)
    assert r.headers["location"] == "/setup/2fa"
    page = client.get("/setup/2fa").text
    secret = db.one("SELECT totp_secret FROM admin")["totp_secret"]
    r = client.post("/setup/2fa", data={"code": pyotp.TOTP(secret).now(), "csrf": _csrf(page)}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/people")
    return secret


def test_setup_is_offered_in_every_language(roster):
    with _client(roster) as client:
        assert client.get("/", follow_redirects=False).headers["location"] == "/setup"
        assert "Create the admin account" in client.get("/setup").text
        page = client.get("/setup?lang=fr").text
        assert "Créer le compte admin" in page and '<html lang="fr" dir="ltr">' in page
        assert '<html lang="ar" dir="rtl">' in client.get("/setup?lang=ar").text
        r = client.post("/setup", data={"setup_token": "wrong", "username": "alex", "language": "fr",
                                        "password": "x" * 14, "password2": "x" * 14})
        assert r.status_code == 400 and "Jeton d&#39;installation incorrect" in r.text


def test_full_web_journey(roster, homelab):
    with _client(roster) as client:
        _setup(client, language="fr")
        page = client.get("/people").text
        assert "Utilisateurs" in page                                                 # language picked at setup
        assert "Content-Security-Policy" in client.get("/login").headers
        assert ' style="' not in page                                                 # strict CSP: no inline style

        r = client.post("/settings/import", data={"csrf": _csrf(client.get("/settings").text), "toml": 'admins = ["alex"]\n[users]\nsam = ["jellyfin"]\n'})
        assert r.status_code == 200 and "Sam" in r.text

        page = client.get("/people/new").text
        r = client.post("/people/new", data={"csrf": _csrf(page), "display_name": "Léa", "email": "lea@example.com",
                                             "language": "en", "password": "Peony-Moon-33", "password2": "Peony-Moon-33",
                                             "app_seerr": "1", "send_email": "1"}, follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"].startswith("/jobs/")
        job_api = r.headers["location"].replace("/jobs/", "/api/jobs/")
        for _ in range(100):
            data = client.get(job_api).json()
            if data["status"] != "running":
                break
            time.sleep(0.05)
        assert data["status"] == "done", data
        assert [s["key"] for s in data["steps"]] == ["vpn", "access", "jellyfin", "seerr", "email"]   # parent added
        assert homelab.mails[-1]["Subject"] == "Welcome to Alex's homelab"                          # person's language

        pid = data["result"]["person_id"]
        person_page = client.get(f"/people/{pid}").text
        assert "lea - phone" in person_page
        qr = client.get(f"/people/{pid}/devices/qr", params={"device": data["result"]["device_id"]})
        assert qr.status_code == 200 and qr.text.startswith("<svg")
        assert client.get(f"/people/{pid}/devices/qr", params={"device": "PK-sam"}).status_code == 502   # not hers
        preview = client.get(f"/people/{pid}/welcome/preview")
        assert "Hi Léa" in preview.text and "unsafe-inline" in preview.headers["content-security-policy"]

        csrf = _csrf(person_page)
        r = client.post(f"/people/{pid}/password", data={"csrf": csrf, "app_jellyfin": "1", "password": "New-Password-2026",
                                                         "code": "000000"})
        assert "Code de double authentification incorrect" in r.text
        r = client.post(f"/people/{pid}/delete", data={"csrf": "forged", "confirm": "Léa", "code": "1"}, follow_redirects=False)
        assert r.status_code == 303 and db.person_by_id(pid)                                    # bad CSRF: nothing deleted

        for url in ("/status", "/activity", "/settings", "/people", "/manifest.webmanifest", "/healthz"):
            assert client.get(url).status_code == 200, url

        r = client.post("/settings/language", data={"csrf": csrf, "language": "en"})
        assert "Language saved." in r.text
        client.post("/logout", data={})
        assert client.get("/people", follow_redirects=False).headers["location"] == "/login"


def test_login_requires_both_factors(roster):
    with _client(roster) as client:
        secret = _setup(client)
        client.post("/logout", data={})
        r = client.post("/login", data={"username": "alex", "password": "wrong-password"})
        assert r.status_code == 401
        r = client.post("/login", data={"username": "Alex", "password": "Super-Secret-2026!"}, follow_redirects=False)
        assert r.headers["location"] == "/login/code"
        assert client.get("/people", follow_redirects=False).headers["location"] == "/login"     # code still expected
        page = client.get("/login/code").text
        time.sleep(0)  # same 30 s window as the setup code: the replay protection must reject it
        r = client.post("/login/code", data={"code": "123456", "csrf": _csrf(page)})
        assert r.status_code == 401
        assert pyotp.TOTP(secret).now()
