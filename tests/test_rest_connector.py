"""The declarative ``rest`` connector, exercised against a fake Nextcloud OCS API."""

from __future__ import annotations

import copy

import pytest

from homeroster import db
from homeroster.providers.apps.rest import dig, render

from fakes import CONFIG, NEXTCLOUD


def test_helpers():
    assert dig({"a": {"b": [{"c": 3}]}}, "a.b.0.c") == 3
    assert dig({"a": 1}, "a.b") is None
    assert render("/users/{username}", {"username": "a b/c"}, url=True) == "/users/a%20b%2Fc"
    assert render({"k": ["{x}", 2]}, {"x": "y"}) == {"k": ["y", 2]}
    assert render("{unknown}", {}) == "{unknown}"


@pytest.fixture()
def nextcloud_roster(make_roster):
    apps = copy.deepcopy(CONFIG["apps"])
    apps["nextcloud"] = copy.deepcopy(NEXTCLOUD)
    return make_roster(apps=apps)


def test_rest_connector_lifecycle(nextcloud_roster, homelab, run):
    roster = nextcloud_roster
    app = roster.apps["nextcloud"]
    assert app.supports_disable and app.login_kind == "username"
    health = run(app.health())
    assert health.ok and health.version == "30.0.4"

    pid = db.create_person("sam", "Sam", "sam@example.com")
    assert run(roster.create_account(pid, "nextcloud", "Correct-Horse-12")) == "account created"
    assert homelab.nc_users["sam"]["displayName"] == "Sam" and homelab.nc_users["sam"]["password"] == "Correct-Horse-12"
    assert db.accounts_of(pid)["nextcloud"]["login"] == "sam"

    run(roster.reset_password(pid, ["nextcloud"], "Another-Pass-34"))
    assert homelab.nc_users["sam"]["password"] == "Another-Pass-34"

    db.set_grants(pid, {"nextcloud"})
    roster.import_toml('admins = ["alex"]\n')
    run(roster.revoke(pid, "nextcloud"))
    assert homelab.nc_users["sam"]["enabled"] is False

    # An API-level error (HTTP 200 with a failing OCS status code) is reported, not ignored.
    other = db.create_person("sam2", "Sam", "sam2@example.com")
    homelab.nc_users["sam2"] = {"enabled": True}
    result = run(roster.grant(other, "nextcloud", "Correct-Horse-12"))
    assert not result["ok"] and "already exists" in result["message"]


def test_rest_connector_finds_existing_accounts(nextcloud_roster, homelab, run):
    homelab.nc_users["sam"] = {"email": "sam@example.com", "enabled": True}
    pid = db.create_person("sam", "Sam", "sam@example.com")
    assert "Nextcloud" in run(nextcloud_roster.link_existing(pid))


def test_rest_connector_requires_create_and_delete(make_roster):
    apps = {"broken": {"connector": "rest", "url": "http://x.test", "endpoints": {"create": {"path": "/"}}}}
    with pytest.raises(ValueError, match="delete"):
        make_roster(apps=apps)
