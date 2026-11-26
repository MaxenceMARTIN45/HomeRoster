"""Settings > Services: changes are tested, saved to the data directory and applied without a restart."""

from __future__ import annotations

import pyotp
import yaml

from homeroster import db
from homeroster.config import services_path
from test_web import _client, _csrf, _setup


def test_add_change_and_remove_services(roster, homelab):
    with _client(roster) as client:
        secret = _setup(client)

        def code() -> str:
            db.run("UPDATE admin SET last_totp_step = 0")  # each code works once: allow a new one per request
            return pyotp.TOTP(secret).now()

        page = client.get("/settings/services")
        assert page.status_code == 200 and "wg-easy" in page.text and "homeassistant" in page.text
        csrf = _csrf(page.text)
        app = {"csrf": csrf, "app_id": "books", "connector": "audiobookshelf", "audiobookshelf__url": "http://audiobookshelf.test"}

        client.post("/settings/services/app", data={**app, "code": "000000", "audiobookshelf__api_key": "abs-key"})
        assert not services_path().exists()                                   # wrong 2FA code: nothing changes
        r = client.post("/settings/services/app", data={**app, "code": code(), "audiobookshelf__api_key": "wrong"})
        assert "books" not in client.app.state.roster.apps and "Nothing was saved" in r.text   # test failed

        client.post("/settings/services/app", data={**app, "code": code(), "audiobookshelf__api_key": "abs-key"})
        saved = yaml.safe_load(services_path().read_text(encoding="utf-8"))
        assert saved["apps"]["books"] == {"connector": "audiobookshelf", "url": "http://audiobookshelf.test", "api_key": "abs-key"}
        assert "books" in client.app.state.roster.apps                         # applied without a restart

        client.post("/settings/services/app", data={**app, "code": code(), "audiobookshelf__label": "Books"})
        saved = yaml.safe_load(services_path().read_text(encoding="utf-8"))
        assert saved["apps"]["books"]["api_key"] == "abs-key" and saved["apps"]["books"]["label"] == "Books"  # secret kept

        r = client.post("/settings/services/vpn", data={"csrf": csrf, "code": code(), "provider": "wg-easy",
                                                         "wg-easy__url": "http://wgeasy.test", "wg-easy__username": "bot",
                                                         "wg-easy__password": "test-password",
                                                         "wg-easy__public_url": "https://vpn.example.com"})
        assert client.app.state.roster.vpn.provider_name == "wg-easy", r.text[:300]

        client.post("/settings/services/app/books/delete", data={"csrf": csrf, "code": code()})
        assert "books" not in client.app.state.roster.apps
        assert "books" not in yaml.safe_load(services_path().read_text(encoding="utf-8"))["apps"]
