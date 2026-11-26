"""Configuration loading and translations."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from homeroster.config import Config, ConfigError, load_config
from homeroster.core.roster import Roster
from homeroster.i18n import LOCALES_DIR, catalogs, languages, localized, t

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src" / "homeroster"


def test_env_interpolation(monkeypatch, tmp_path):
    monkeypatch.setenv("JF_KEY", "s3cret")
    path = tmp_path / "homeroster.yaml"
    path.write_text("homeroster: {owner: Alex, trusted_proxies: '172.16.0.1, 172.16.0.2'}\n"
                    "vpn: {provider: wgdashboard, api_key: '${JF_KEY}', dns: '${MISSING:-1.1.1.1}'}\n"
                    "proxy: {provider: npm}\n"
                    "apps: {jellyfin: {connector: jellyfin, api_key: '${JF_KEY}'}}\n", encoding="utf-8")
    config = load_config(path)
    assert config.vpn.get("api_key") == "s3cret" and config.vpn.get("dns") == "1.1.1.1"
    assert config.apps["jellyfin"]["api_key"] == "s3cret"
    assert config.trusted_proxies == ["172.16.0.1", "172.16.0.2"]
    assert config.mail is None


@pytest.mark.parametrize("raw, message", [
    ({"proxy": {"provider": "npm"}}, "vpn"),
    ({"vpn": {"provider": "x"}, "proxy": {"provider": "npm"}, "apps": {"Bad Id": {"connector": "x"}}}, "invalid app id"),
    ({"vpn": {"provider": "x"}, "proxy": {"provider": "npm"}, "apps": {"a": {}}}, "connector"),
])
def test_invalid_config(raw, message):
    with pytest.raises(ConfigError, match=message):
        Config.from_dict(raw)


def test_example_config_is_valid():
    config = load_config(ROOT / "config" / "homeroster.example.yaml")
    assert set(config.apps) == {"jellyfin", "seerr", "immich", "openwebui"}


def _pick_blocks(text: str, vpn: str, proxy: str, apps: tuple[str, ...] = ("jellyfin", "seerr")) -> str:
    """What scripts/install.sh does with awk: keep the chosen "# >>> kind: name" blocks, uncommented."""
    out, block, keep = [], False, False
    for line in text.splitlines():
        if line.startswith("# >>> "):
            kind, name = line[6:].split(": ")
            block, keep = True, name in apps if kind == "app" else {"vpn": vpn, "proxy": proxy}[kind] == name
        elif line.startswith("# <<<"):
            block = False
        elif not block:
            out.append(line)
        elif keep:
            out.append(line[2:] if line.startswith("# ") else line)
    return chr(10).join(out)


@pytest.mark.parametrize("vpn", ["wgdashboard", "wg-easy", "wireguard", "pivpn", "tailscale", "headscale", "netbird",
                                 "zerotier"])
@pytest.mark.parametrize("proxy", ["npm", "traefik", "caddy", "nginx", "swag", "haproxy", "zoraxy"])
def test_installer_choices_give_a_valid_config(vpn, proxy, tmp_path):
    text = (ROOT / "config" / "homeroster.example.yaml").read_text(encoding="utf-8")
    path = tmp_path / "homeroster.yaml"
    path.write_text(_pick_blocks(text, vpn, proxy), encoding="utf-8")
    config = load_config(path)
    assert (config.vpn.provider, config.proxy.provider) == (vpn, proxy)
    roster = Roster(config)                                   # every provider is registered and accepts its options
    assert (roster.vpn.provider_name, roster.proxy.provider_name) == (vpn, proxy)
    assert set(roster.apps) == {"jellyfin", "seerr"}


def test_installer_can_keep_every_app(tmp_path):
    text = (ROOT / "config" / "homeroster.example.yaml").read_text(encoding="utf-8")
    apps = ("jellyfin", "plex", "emby", "seerr", "immich", "openwebui", "audiobookshelf", "navidrome", "vaultwarden",
            "homeassistant", "nextcloud")
    path = tmp_path / "homeroster.yaml"
    path.write_text(_pick_blocks(text, "wgdashboard", "npm", apps), encoding="utf-8")
    assert set(Roster(load_config(path)).apps) == set(apps)


def _flat_keys(path: Path) -> set[str]:
    def walk(data, prefix=""):
        for key, value in data.items():
            if isinstance(value, dict):
                yield from walk(value, f"{prefix}{key}.")
            else:
                yield f"{prefix}{key}"
    return set(walk(json.loads(path.read_text(encoding="utf-8"))))


def test_every_locale_has_the_same_keys_and_placeholders():
    reference = catalogs()["en"]
    for file in LOCALES_DIR.glob("*.json"):
        assert _flat_keys(file) == set(reference), file.name
        for key, text in catalogs()[file.stem].items():
            assert set(re.findall(r"\{(\w+)\}", text)) == set(re.findall(r"\{(\w+)\}", reference[key])), (file.name, key)


def test_every_key_used_in_code_exists():
    # Full keys only: dynamic prefixes such as t('status.kind_' ~ kind) end with "_" and are skipped.
    pattern = re.compile(r"""\bt\(\s*['"]([a-z_]+\.[a-z0-9_.]*[a-z0-9])['"]""")
    used = set()
    for file in [*SRC.rglob("*.py"), *SRC.rglob("*.html"), *SRC.rglob("*.txt")]:
        used |= set(pattern.findall(file.read_text(encoding="utf-8")))
    missing = sorted(k for k in used if k not in catalogs()["en"])
    assert not missing, missing


def test_translation_helpers():
    assert list(languages())[:2] == ["en", "ar"] and languages()["fr"] == "Français"
    assert set(languages()) == {"en", "fr", "es", "de", "it", "pt", "ru", "zh", "hi", "ar"}
    assert t("_meta.direction", "ar") == "rtl" and t("_meta.direction", "de") == "ltr"
    assert t("people.slug_taken", "fr", slug="sam") == "L'identifiant « sam » est déjà utilisé."
    assert t("does.not.exist") == "does.not.exist"
    assert localized({"en": "Movies", "fr": "Films"}, "fr") == "Films"
    assert localized({"en": "Movies"}, "de") == "Movies"
