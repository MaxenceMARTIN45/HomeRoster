"""Emby, Plex, Audiobookshelf, Navidrome, Vaultwarden and Home Assistant against their simulated APIs."""

from __future__ import annotations

import pytest

from homeroster import db
from homeroster.providers.apps.home import HomeAssistant

from fakes import FakeHomeAssistantSocket

APPS = {
    "emby": {"connector": "emby", "url": "http://emby.test", "api_key": "k"},
    "plex": {"connector": "plex", "url": "http://plex.test", "token": "plex-token", "plex_tv": "http://plex.test"},
    "audiobookshelf": {"connector": "audiobookshelf", "url": "http://audiobookshelf.test", "api_key": "abs-key"},
    "navidrome": {"connector": "navidrome", "url": "http://navidrome.test", "username": "admin", "password": "nd-pass"},
    "vaultwarden": {"connector": "vaultwarden", "url": "http://vaultwarden.test", "admin_token": "vw-admin-token"},
    "homeassistant": {"connector": "homeassistant", "url": "http://homeassistant.test", "token": "ha-token"},
}


@pytest.fixture()
def ha_socket(monkeypatch):
    socket = FakeHomeAssistantSocket()
    monkeypatch.setattr(HomeAssistant, "connect", staticmethod(socket.connect))
    return socket


@pytest.mark.parametrize("app_id", list(APPS))
def test_account_lifecycle(make_roster, homelab, run, ha_socket, app_id):
    roster = make_roster(apps={app_id: APPS[app_id]})
    app = roster.app(app_id)
    assert run(app.health()).ok, run(app.health()).message
    roster.import_toml('admins = ["alex"]\n')
    pid = db.create_person("julia", "Julia", "julia@example.com")
    account = run(roster.ensure_account(pid, app_id, "Correct-Horse-12"))
    assert account.external_id
    assert run(app.find(roster.person(pid))) == account                      # found again for "link accounts"
    if app.manages_password:
        run(app.set_password(account, "Other-Horse-34"))
    if app.supports_disable:
        run(app.set_enabled(account, False))
        run(app.set_enabled(account, True))
    run(app.delete(account))
    assert run(app.find(roster.person(pid))) is None


def test_details(make_roster, homelab, run, ha_socket):
    roster = make_roster(apps=APPS)
    roster.import_toml('admins = ["alex"]\n')
    pid = db.create_person("julia", "Julia", "julia@example.com")
    for app_id in APPS:
        run(roster.ensure_account(pid, app_id, "Correct-Horse-12"))
    assert homelab.plex_shares[0]["email"] == "julia@example.com" and homelab.plex_shares[0]["sections"] == [11, 12]
    assert next(u for u in homelab.emby_users if u["Name"] == "Julia")["password"] == "Correct-Horse-12"
    assert homelab.abs_users[0]["username"] == "julia" and homelab.abs_users[0]["type"] == "user"
    assert any(u["userName"] == "julia" and not u["isAdmin"] for u in homelab.nd_users)
    assert homelab.vw_users[0]["email"] == "julia@example.com"              # invited: they choose the master password
    ha_user = next(u for u in ha_socket.users if u["username"] == "julia")
    assert ha_user["group_ids"] == ["system-users"] and ha_user["password"] == "Correct-Horse-12"
    assert ha_socket.closed >= 2                                              # one connection per operation
