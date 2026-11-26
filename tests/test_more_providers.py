"""Plain WireGuard / PiVPN, the invitation VPNs, the file-based proxies, Zoraxy, and app languages."""

from __future__ import annotations

import base64
import time

import pytest

from homeroster import db
from homeroster.providers.vpn.wireguard import _x25519, generate_keypair, public_key

SERVER_KEY = base64.b64encode(bytes(range(32))).decode()
WG0 = f"""[Interface]
Address = 10.8.0.1/24
ListenPort = 51820
PrivateKey = {SERVER_KEY}

# HomeRoster: alex - phone
[Peer]
PublicKey = PK-alex-phone
AllowedIPs = 10.8.0.2/32
"""
NOW = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + 60))


def test_x25519_matches_rfc_7748():
    scalar = bytes.fromhex("a546e36bfbe7e6ac5fbbe4e5b6bea5b9bd5d6cb1a0b3fbd8b8b9e5a65c2ebb35"[:64])
    assert len(_x25519(scalar, (9).to_bytes(32, "little"))) == 32
    # RFC 7748 section 6.1, Alice's key pair
    alice = bytes.fromhex("77076d0a7318a57d3c16c17251b26645df4c2f87ebc0992ab177fba51db92c2a")
    assert _x25519(alice, (9).to_bytes(32, "little")).hex() == "8520f0098930a754748b7ddcb43ef75a0dbf3a0d26381af4eba4a98eaa9b4e6a"
    private, public = generate_keypair()
    assert public_key(private) == public


@pytest.mark.parametrize("provider", ["wireguard", "pivpn"])
def test_wireguard_file(make_roster, run, tmp_path, provider):
    conf = tmp_path / "wg0.conf"
    conf.write_text(WG0, encoding="utf-8")
    roster = make_roster(vpn={"provider": provider, "config_file": str(conf), "endpoint": "vpn.example.com:51820",
                              "dns": "10.8.0.1"})
    pid = db.create_person("julia", "Julia", "julia@example.com")
    device = run(roster.vpn.create_device("julia - phone", roster.person(pid)))
    assert device.ip == "10.8.0.3"
    client = run(roster.vpn.client_config(device.id))
    assert "Endpoint = vpn.example.com:51820" in client and f"PublicKey = {public_key(SERVER_KEY)}" in client
    names = {d.name for d in run(roster.vpn.devices())}
    assert names == {"alex - phone", "julia - phone"} if provider == "wireguard" else {"julia - phone"} <= names
    if provider == "pivpn":
        assert "### begin julia--phone ###" in conf.read_text() and (tmp_path / "configs" / "julia--phone.conf").exists()
        assert "julia--phone" in (tmp_path / "configs" / "clients.txt").read_text()
    run(roster.vpn.delete_devices([device.id]))
    text = conf.read_text()
    assert "julia" not in text and "PK-alex-phone" in text and "[Interface]" in text


def _invite_flow(make_roster, run, vpn, join):
    roster = make_roster(vpn=vpn)
    roster.import_toml('[users]\nalex = []\n')
    db.run("UPDATE people SET is_admin = 1, email = 'alex@example.com' WHERE slug = 'alex'")
    pid = db.create_person("julia", "Julia", "julia@example.com")
    db.set_grants(pid, {"jellyfin"})
    device = run(roster.add_device(pid, "phone"))
    assert device.pending and device.id.startswith("invite:julia:")
    name, invite = run(roster.device_access(pid, device.id))
    by_person, _ = run(roster.devices_by_person())
    assert [d.pending for d in by_person["julia"]] == [True]
    join(invite)
    by_person, _ = run(roster.devices_by_person())
    julia = by_person["julia"]
    assert len(julia) == 1 and not julia[0].pending and julia[0].ip       # the invitation was used
    return roster, invite, julia[0]


def test_headscale(make_roster, homelab, run):
    homelab.hs_users.append({"id": "9", "name": "alex"})
    homelab.hs_nodes.append({"id": "1", "givenName": "alex-phone", "user": {"name": "alex"}, "ipAddresses": ["100.64.0.1"],
                             "online": True, "createdAt": "2026-01-01T00:00:00Z"})
    vpn = {"provider": "headscale", "url": "http://headscale.test", "api_key": "hs-key", "server_url": "https://hs.example.com"}
    roster, invite, device = _invite_flow(make_roster, run, vpn,
                                          lambda inv: homelab.headscale_join(inv.split()[-1], "julias-iphone", NOW))
    assert "https://hs.example.com" in invite and device.extra["device"] == "julias-iphone"
    report = run(roster.sync_access(reason="test"))
    assert report["ok"] and device.ip in homelab.acl("jellyfin") and device.ip not in homelab.acl("radarr")


def test_tailscale(make_roster, homelab, run):
    homelab.ts_devices.append({"id": "d1", "hostname": "alex-phone", "user": "alex@example.com", "addresses": ["100.70.0.1"],
                               "connectedToControl": True, "created": "2026-01-01T00:00:00Z"})
    vpn = {"provider": "tailscale", "api_key": "tskey-api-test", "url": "http://tailscale.test"}

    def join(invite):
        assert invite.startswith("https://login.tailscale.com/admin/invite/")
        homelab.ts_devices.append({"id": "d2", "hostname": "julia-pixel", "user": "JULIA@example.com",
                                   "addresses": ["100.70.0.2", "fd7a::2"], "created": NOW})

    roster, _, device = _invite_flow(make_roster, run, vpn, join)
    assert device.ip == "100.70.0.2"                                     # matched through the e-mail address


def test_netbird(make_roster, homelab, run):
    homelab.nb_peers.append({"id": "p1", "name": "alex", "ip": "100.90.0.1", "connected": True,
                             "groups": [{"name": "homeroster-alex"}], "created_at": "2026-01-01T00:00:00Z"})
    vpn = {"provider": "netbird", "url": "http://netbird.test", "token": "nbp_test"}

    def join(invite):
        key = next(k for k in homelab.nb_keys if k["key"] in invite)
        group = next(g for g in homelab.nb_groups if g["id"] == key["auto_groups"][0])
        homelab.nb_peers.append({"id": "p2", "name": "julia-phone", "ip": "100.90.0.2", "connected": False,
                                 "groups": [{"name": group["name"]}], "created_at": NOW})

    _invite_flow(make_roster, run, vpn, join)


def test_zerotier_claims_the_new_member(make_roster, homelab, run):
    homelab.zt_members.append({"nodeId": "aaaa111111", "name": "alex - phone", "description": "",
                               "config": {"authorized": True, "ipAssignments": ["10.147.17.2"]}, "creationTime": 1})
    vpn = {"provider": "zerotier", "token": "zt-token", "network_id": "abcd1234", "url": "http://zerotier.test/api/v1"}

    def join(invite):
        assert "abcd1234" in invite
        homelab.zt_members.append({"nodeId": "bbbb222222", "name": "", "description": "", "creationTime": int(time.time() * 1000) + 5000,
                                   "config": {"authorized": False, "ipAssignments": ["10.147.17.3"]}})

    _, _, device = _invite_flow(make_roster, run, vpn, join)
    member = next(m for m in homelab.zt_members if m["nodeId"] == "bbbb222222")
    assert member["config"]["authorized"] and member["description"] == "homeroster:julia" and device.ip == "10.147.17.3"


@pytest.mark.parametrize("provider, file, expected", [
    ("nginx", "homeroster-jellyfin.conf", "allow 10.8.0.2;\nallow 10.8.0.3;\nallow 10.8.0.4;\ndeny all;\n"),
    ("swag", "homeroster-jellyfin.conf", "deny all;"),
    ("caddy", "homeroster-jellyfin.caddy", "@homeroster_denied not remote_ip 10.8.0.2 10.8.0.3 10.8.0.4\n"),
    ("haproxy", "homeroster-jellyfin.lst", "10.8.0.2\n10.8.0.3\n10.8.0.4\n"),
])
def test_file_proxies(make_roster, run, tmp_path, provider, file, expected):
    roster = make_roster(proxy={"provider": provider, "dir": str(tmp_path)})
    roster.import_toml('admins = ["alex"]\n[users]\nsam = ["jellyfin"]\n')
    report = run(roster.sync_access(reason="test"))
    assert report["ok"], report
    assert expected in (tmp_path / file).read_text()
    admins = (tmp_path / file.replace("jellyfin", "admins")).read_text()
    assert "10.8.0.2" in admins and "10.8.0.4" not in admins             # unmanaged sites: admins only
    assert run(roster.sync_access(reason="test"))["changed"] == []         # idempotent


def test_zoraxy(make_roster, homelab, run):
    roster = make_roster(proxy={"provider": "zoraxy", "url": "http://zoraxy.test", "username": "bot", "password": "zx-pass"})
    roster.import_toml('admins = ["alex"]\n[users]\nsam = ["jellyfin"]\n')
    assert run(roster.sync_access(reason="test"))["ok"]
    rule = next(r for r in homelab.zx_rules if r["Name"] == "homeroster-jellyfin")
    assert sorted(homelab.zx_whitelist[rule["ID"]]) == ["10.8.0.2", "10.8.0.3", "10.8.0.4"] and rule["WhitelistEnabled"]
    host = next(h for h in homelab.zx_hosts if h["RootOrMatchingDomain"].startswith("jellyfin."))
    assert host["AccessFilterUUID"] == rule["ID"]
    assert run(roster.sync_access(reason="test"))["changed"] == []


def test_language_reaches_the_apps(make_roster, homelab, run):
    from fakes import NEXTCLOUD

    nextcloud = {**NEXTCLOUD, "languages": {"pt": "pt_BR"}, "endpoints": {
        **NEXTCLOUD["endpoints"],
        "language": {"method": "PUT", "path": "/ocs/v1.php/cloud/users/{id}?format=json", "form": {"key": "language", "value": "{language}"}}}}
    from conftest import CONFIG

    roster = make_roster(apps={**CONFIG["apps"], "nextcloud": nextcloud})
    roster.import_toml('admins = ["alex"]\n')
    pid = db.create_person("ana", "Ana", "ana@example.com", "pt")
    for app_id in ("jellyfin", "seerr", "nextcloud"):
        run(roster.ensure_account(pid, app_id, "Correct-Horse-12"))
    jf_user = next(u for u in homelab.jf_users if u["Name"] == "Ana")
    assert jf_user["Configuration"]["AudioLanguagePreference"] == "por"
    seerr_id = db.accounts_of(pid)["seerr"]["external_id"]
    assert homelab.seerr_settings[seerr_id]["locale"] == "pt-BR" and homelab.seerr_settings[seerr_id]["username"]
    assert homelab.nc_users["ana"]["language"] == "pt_BR"
    db.run("UPDATE people SET language = 'fr' WHERE id = ?", (pid,))
    assert set(run(roster.apply_language(pid))) == {"Jellyfin", "Seerr", "Nextcloud"}
    assert homelab.seerr_settings[seerr_id]["locale"] == "fr" and homelab.nc_users["ana"]["language"] == "fr"
