from __future__ import annotations

import asyncio
import copy

import httpx
import pytest

from homeroster import db
from homeroster.config import Config
from homeroster.core.roster import Roster
from homeroster.i18n import set_language
from homeroster.providers import base
from homeroster.providers.mail import smtp

from fakes import CONFIG, FakeHomelab

SETUP_TOKEN = "test-setup-token"


@pytest.fixture()
def homelab(monkeypatch, tmp_path):
    """A fake homelab wired into every provider, plus a fresh database."""
    monkeypatch.setenv("HOMEROSTER_DATA", str(tmp_path))
    monkeypatch.setenv("HOMEROSTER_SETUP_TOKEN", SETUP_TOKEN)
    lab = FakeHomelab()
    lab.mails = []
    monkeypatch.setattr(base, "TRANSPORT", httpx.MockTransport(lab.handler))
    monkeypatch.setattr(smtp, "SENDER", lab.mails.append)
    set_language("en")
    yield lab


@pytest.fixture()
def make_roster(homelab):
    def build(**overrides) -> Roster:
        raw = copy.deepcopy(CONFIG)
        for section, value in overrides.items():
            raw[section] = value
        config = Config.from_dict(raw)
        db.connect(config.db_path)
        return Roster(config)
    return build


@pytest.fixture()
def roster(make_roster) -> Roster:
    return make_roster()


@pytest.fixture()
def run():
    return asyncio.run
