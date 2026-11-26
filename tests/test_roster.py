"""End-to-end behaviour of the core against the fake homelab."""

from __future__ import annotations

from homeroster import db
from homeroster.naming import parse_device_name, slugify
from homeroster.providers.apps.base import STATIC_APPS_DIR

from fakes import PNG

STATIC = STATIC_APPS_DIR.parent


def test_naming_convention():
    assert slugify("Alice") == "alice"
    assert slugify("Jean-Luc") == "jean_luc"
    assert slugify("  Zoé Durand ") == "zoe_durand"
    assert parse_device_name("sam - laptop") == ("sam", "laptop")
    assert parse_device_name("Media Server") is None


def test_apps_are_ordered_parents_first(roster):
    assert list(roster.apps) == ["jellyfin", "immich", "openwebui", "seerr"]
    assert roster.site_for_host("ai.example.com") == "openwebui"
    assert roster.site_for_host("radarr.example.com") == "radarr"


def test_import_and_sync(roster, homelab, run):
    result = roster.import_toml('admins = ["alex"]\n[users]\nsam = ["jellyfin", "seerr", "immich", "radarr"]\n')
    assert result["created"] == ["alex", "sam"] and result["ignored"] == ["sam:radarr"]
    report = run(roster.sync_access(reason="test"))
    assert report["ok"], report
    assert homelab.acl("jellyfin") == ["10.8.0.2", "10.8.0.3", "10.8.0.4", "all"]
    assert homelab.acl("openwebui") == ["10.8.0.2", "10.8.0.3", "all"]      # matched through its hostname
    assert homelab.acl("radarr") == ["10.8.0.2", "10.8.0.3", "all"]         # unmanaged: admins only
    assert homelab.acl("roster") == ["10.8.0.2", "10.8.0.3", "all"]         # HomeRoster itself too
    assert all(h["access_list_id"] for h in homelab.hosts)
    assert any("Media Server" in w for w in report["warnings"])
    again = run(roster.sync_access(reason="test"))
    assert again["ok"] and again["changed"] == []                            # idempotent


def test_sync_refuses_without_admin_device(roster, homelab, run):
    roster.import_toml('[users]\nsam = ["jellyfin"]\n')
    report = run(roster.sync_access(reason="test"))
    assert not report["ok"] and "admin" in report["error"] and homelab.acls == []


def test_sync_refuses_when_vpn_is_down(roster, homelab, run):
    roster.import_toml('admins = ["alex"]\n')
    homelab.fail.add("wg.test")
    report = run(roster.sync_access(reason="test"))
    assert not report["ok"] and homelab.acls == []


def test_sync_without_people_does_nothing(roster, homelab, run):
    report = run(roster.sync_access(reason="test"))
    assert report.get("skipped") and homelab.calls == []


def test_full_creation_isolates_failures(roster, homelab, run):
    roster.import_toml('admins = ["alex"]\n')
    homelab.fail.add("immich.test")
    pid = db.create_person("riley", "Riley", "riley@example.com", "fr")
    apps = {"jellyfin", "seerr", "immich", "openwebui"}
    db.set_grants(pid, apps)
    db.job_create("j1", "creation", pid, roster.plan_steps(apps, True))
    run(roster.run_creation("j1", pid, "Tiger-Lamp-42-Orchard", apps, "phone", True))
    job = db.job_get("j1")
    status = {s["key"]: s["status"] for s in job["steps"]}
    assert status == {"vpn": "ok", "access": "ok", "jellyfin": "ok", "immich": "error", "openwebui": "ok",
                      "seerr": "ok", "email": "ok"}
    assert job["status"] == "partial" and job["result"]["device_name"] == "riley - phone"
    assert set(db.accounts_of(pid)) == {"jellyfin", "seerr", "openwebui"}
    mail = homelab.mails[0]
    html = mail.get_body(("html",)).get_content()
    assert mail["Subject"] == "Bienvenue sur le homelab de Alex"                # the person's language
    assert "Salut Riley" in html and "Open WebUI" in html and "Tiger" not in html  # never the password
    assert "cid:app-jellyfin" in html
    inline = [p for p in mail.walk() if p.get_content_maintype() == "image"]
    assert {p["Content-ID"] for p in inline} >= {"<app-jellyfin>", "<app-seerr>", "<app-openwebui>"}
    images = {p["Content-ID"]: p.get_content() for p in inline}
    assert images["<app-jellyfin>"] == PNG                                       # official icon from the CDN
    assert images["<app-seerr>"] == (STATIC / "apps" / "seerr.png").read_bytes()  # CDN miss: built-in pictogram
    assert "10.8.0.14" in homelab.acl("jellyfin") and "10.8.0.14" not in homelab.acl("radarr")


def test_revoke_disables_and_grant_reenables(roster, homelab, run):
    roster.import_toml('admins = ["alex"]\n')
    pid = db.create_person("zoe", "Zoé", "zoe@example.com")
    assert run(roster.grant(pid, "jellyfin", "Correct-Horse-12"))["ok"]
    assert run(roster.grant(pid, "seerr", None))["ok"]
    user = next(u for u in homelab.jf_users if u["Name"] == "Zoé")
    # Jellyfin stays enabled while Seerr (which signs in through it) is still granted.
    run(roster.revoke(pid, "jellyfin"))
    assert user["Policy"]["IsDisabled"] is False
    run(roster.revoke(pid, "seerr"))
    assert user["Policy"]["IsDisabled"] is True
    assert db.accounts_of(pid)["jellyfin"]["status"] == "disabled"
    assert run(roster.grant(pid, "jellyfin", None))["ok"]
    assert user["Policy"]["IsDisabled"] is False


def test_reset_password_follows_parent(roster, homelab, run):
    pid = db.create_person("sam", "Sam", "sam@example.com")
    run(roster.ensure_account(pid, "seerr", "Correct-Horse-12"))
    results = run(roster.reset_password(pid, ["jellyfin", "seerr", "immich"], "Another-Pass-34"))
    assert [(r["app"], r["ok"]) for r in results] == [("jellyfin", True), ("seerr", True), ("immich", False)]


def test_link_existing_accounts(roster, homelab, run):
    homelab.jf_users.append({"Id": "jf-sam", "Name": "Sam", "Policy": {}})
    homelab.seerr_users.append({"id": 7, "jf": "jf-sam"})
    homelab.owui_users.append({"id": "ow-sam", "email": "sam@example.com"})
    pid = db.create_person("sam", "Sam", "sam@example.com")
    assert run(roster.link_existing(pid)) == ["Jellyfin", "Open WebUI", "Seerr"]
    assert db.accounts_of(pid)["seerr"]["external_id"] == "7"


def test_delete_person_removes_everything(roster, homelab, run):
    roster.import_toml('admins = ["alex"]\n[users]\nsam = ["jellyfin"]\n')
    sam = db.person_by_slug("sam")
    run(roster.ensure_account(sam["id"], "seerr", "Correct-Horse-12"))
    results = run(roster.delete_person(sam["id"], delete_accounts=True))
    assert all(r["ok"] for r in results), results
    assert not any(p["name"].startswith("sam -") for p in homelab.peers)
    assert [u["Name"] for u in homelab.jf_users] == ["Alex"] and homelab.seerr_users == []
    assert db.person_by_slug("sam") is None


def test_health_reports_every_service(roster, homelab, run):
    homelab.fail.add("immich.test")
    health = {h["label"]: h for h in run(roster.health_all())}
    assert health["WGDashboard"]["ok"] and health["WGDashboard"]["version"] == "v4.2.0"
    assert health["Nginx Proxy Manager"]["version"] == "2.12.3"
    assert not health["Immich"]["ok"] and "HTTP 404" in health["Immich"]["message"]
    assert health["E-mail (SMTP)"]["ok"]


def test_logos(make_roster, homelab, run, tmp_path):
    logo = tmp_path / "logo.png"
    logo.write_bytes(PNG)
    raw_apps = {"jellyfin": {"connector": "jellyfin", "url": "http://jellyfin.test", "api_key": "k", "logo": "builtin"}}
    roster = make_roster(homeroster={"owner": "Alex", "domain": "example.com", "logo": str(logo)}, apps=raw_apps)
    assert run(roster.logo()).data == PNG and run(roster.logo()).cid == "homelab"
    assert run(roster.logo("jellyfin")).data == (STATIC / "apps" / "jellyfin.png").read_bytes()   # no download
    assert not any("cdn" in c for c in homelab.calls)
