"""The alternative VPN and reverse proxy providers: wg-easy and Traefik."""

from __future__ import annotations

import yaml

from homeroster.providers.proxy.traefik import hosts_of

WG_EASY = {"provider": "wg-easy", "url": "http://wgeasy.test", "username": "bot", "password": "test-password",
           "public_url": "https://vpn.example.com/"}


def test_wg_easy_devices(make_roster, run):
    vpn = make_roster(vpn=WG_EASY).vpn
    assert run(vpn.version()) == "15.5.0"
    devices = run(vpn.devices())
    assert [(d.name, d.ip) for d in devices][:2] == [("alex - phone", "10.8.0.2"), ("alex - laptop", "10.8.0.3")]
    created = run(vpn.create_device("sam - tv"))
    assert created.name == "sam - tv" and created.ip.startswith("10.8.0.")
    assert "[Interface]" in run(vpn.client_config(created.id))
    run(vpn.delete_devices([created.id]))
    assert all(d.name != "sam - tv" for d in run(vpn.devices()))


def test_wg_easy_drives_the_whole_sync(make_roster, homelab, run):
    roster = make_roster(vpn=WG_EASY)
    roster.import_toml('admins = ["alex"]\n[users]\nsam = ["jellyfin"]\n')
    assert run(roster.sync_access(reason="test"))["ok"]
    assert homelab.acl("jellyfin") == ["10.8.0.2", "10.8.0.3", "10.8.0.4", "all"]


def test_traefik_rules():
    assert hosts_of("Host(`a.example.com`)") == ["a.example.com"]
    assert hosts_of("Host(`a.example.com`, `B.example.com`) && PathPrefix(`/x`)") == ["a.example.com", "b.example.com"]
    assert hosts_of("Host(`a.example.com`) || Host(`b.example.com`)") == ["a.example.com", "b.example.com"]
    assert hosts_of("PathPrefix(`/api`)") == []


def test_traefik_writes_allow_lists(make_roster, homelab, run, tmp_path):
    file = tmp_path / "traefik" / "homeroster.yml"
    file.parent.mkdir()
    roster = make_roster(proxy={"provider": "traefik", "url": "http://traefik.test", "file": str(file)})
    roster.import_toml('admins = ["alex"]\n[users]\nsam = ["jellyfin"]\n')
    assert run(roster.proxy.version()) == "3.5.0"

    report = run(roster.sync_access(reason="test"))
    assert report["ok"], report
    middlewares = yaml.safe_load(file.read_text(encoding="utf-8"))["http"]["middlewares"]
    assert middlewares["homeroster-jellyfin"] == {"ipAllowList": {"sourceRange": ["10.8.0.2", "10.8.0.3", "10.8.0.4"]}}
    assert middlewares["homeroster-openwebui"]["ipAllowList"]["sourceRange"] == ["10.8.0.2", "10.8.0.3"]
    assert middlewares["homeroster-radarr"]["ipAllowList"]["sourceRange"] == ["10.8.0.2", "10.8.0.3"]
    # No router uses the middlewares yet: every site is reported.
    assert sum("homeroster-jellyfin@file" in w for w in report["warnings"]) == 1

    for router in homelab.routers:
        router["middlewares"] = [f"homeroster-{roster.site_for_host(h)}@file" for h in hosts_of(router["rule"])]
    again = run(roster.sync_access(reason="test"))
    assert again["changed"] == [] and not any("@file" in w for w in again["warnings"])   # idempotent, all protected
