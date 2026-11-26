# AGENTS.md: technical guide for AI agents

You are helping someone install, configure or extend **HomeRoster**, a self-hosted, admin-only web app that
manages the people allowed into a homelab: VPN devices, reverse-proxy access lists, app accounts and welcome
e-mails. Read this file entirely before changing anything. Humans: see [README.md](README.md).

## TL;DR for agents

* Python 3.11+, FastAPI, Jinja2, SQLite, httpx. No frontend build step, no JS framework, no CDN.
* Structure in `config/homeroster.yaml`, secrets in `.env` referenced as `${VAR}`. Both are gitignored.
* Every external system is a **plugin** registered by name (`providers/registry.py`). Never hard-code an app
  in the core: the core (`core/roster.py`) only talks to the four interfaces below.
* Every user-visible string goes through `t("key")` and exists in **every** `locales/*.json`.
* Run `pytest` and `ruff check src tests scripts` before you finish. Tests simulate the whole homelab
  (`tests/fakes.py`), they never touch the network.
* Never log, store or e-mail a user password. Never put a secret in `homeroster.yaml`, a test or a commit.

## Repository map

```
src/homeroster/
  __main__.py            CLI: `python -m homeroster check|serve`
  config.py              YAML loading, ${VAR} interpolation, Config dataclass
  i18n.py                locales loader, t(), current language (contextvar)
  db.py                  SQLite schema and queries (people, grants, accounts, jobs, audit, kv)
  security.py            argon2, TOTP, sessions, CSRF, brute-force throttling
  naming.py              "<person> - <device>" convention, slugify
  templating.py          Jinja2 environment (filters, globals)
  logos.py               e-mail logos: dashboard-icons name, URL or file, cached in <data>/logos
  core/roster.py         Roster: the business logic (create, grant, revoke, sync, delete, e-mail)
  providers/
    base.py              ServiceError (translated), Health, HttpService (httpx wrapper)
    registry.py          register_vpn / register_proxy / register_mail / register_app, load_plugins
    vpn/base.py          VpnProvider interface, Device
    vpn/wgdashboard.py   WireGuard through WGDashboard v4 (API key)
    vpn/wg_easy.py       WireGuard through wg-easy v15+ (HTTP Basic auth, Origin header on writes)
    vpn/wireguard.py     plain WireGuard and PiVPN: edits wg0.conf, pure-Python X25519 keys
    vpn/mesh.py          invitation VPNs: Tailscale, Headscale, NetBird, ZeroTier
    proxy/base.py        AccessProxy interface, AccessPolicy, SyncResult
    proxy/npm.py         Nginx Proxy Manager access lists, attached to the proxy hosts
    proxy/traefik.py     Traefik: reads routers from the API, writes ipAllowList middlewares to a file
    proxy/files.py       nginx, SWAG, Caddy, HAProxy: one allow-list file per managed site + "admins"
    proxy/zoraxy.py      Zoraxy access rules (session + CSRF token)
    mail/base.py         Mailer interface, InlineImage
    mail/smtp.py         SMTP (implicit TLS or STARTTLS), inline CID images
    apps/base.py         AppConnector interface, Person, Account
    apps/{jellyfin,seerr,immich,openwebui}.py
    apps/media.py        Emby, Plex (plex.tv server sharing), Audiobookshelf, Navidrome
    apps/home.py         Vaultwarden (admin invitations), Home Assistant (WebSocket API)
    apps/rest.py         declarative connector driven by YAML endpoints
  catalog.py             options the Settings > Services page offers for every provider and connector
  web/routes/services.py Settings > Services: test, save to <data>/services.yaml, swap the Roster live
  web/app.py             create_app() factory, security headers, language middleware
  web/deps.py            require_admin, CSRF, flash messages, page rendering
  web/routes/            auth.py (setup, login), people.py, system.py (status, activity, settings)
  templates/             pages and emails/ (welcome.html, welcome.txt, alert.html)
  static/                app.css, app.js, icons.svg (sprite), icon.svg (logo), apps/*.png (e-mail logos)
  locales/               en, fr, es, de, it, pt, ru, zh, hi, ar (.json; _meta.direction = rtl for Arabic)
config/homeroster.example.yaml   annotated configuration (copied to config/homeroster.yaml)
scripts/install.sh               interactive installer for the Docker host
scripts/proxmox-firewall.sh      optional Proxmox VE firewall helper
scripts/host-watcher.sh          systemd units on the host: wg syncconf / proxy reload when files change
scripts/make-config.sh           homeroster.yaml from the example's "# >>> kind: name" blocks (installers)
scripts/proxmox/                 homeroster-lxc.sh (Proxmox host), install.sh (native Debian), update.sh
scripts/render_assets.py         SVG/HTML -> PNG (logos, social preview, how-it-works diagram), headless Chromium
scripts/take_screenshots.py      README screenshots from the demo server
tests/                           fakes.py (simulated homelab), test_*.py, demo_server.py
```

## Access model

This is the most important part. Get it wrong and people either lose access or gain access they should not have.

1. **Naming convention.** A VPN device belongs to a person when its name is `<slug> - <device>`
   (`naming.parse_device_name`). The slug is `[a-z0-9_]{1,32}`. Devices whose name does not match, or whose
   slug is unknown, get **no rights** and are reported as warnings on the Status page.
2. **Source of truth.** The `grants` table says which person may open which app id. Admins (`people.is_admin`)
   may open everything.
3. **Policy.** `Roster.sync_access()` builds an `AccessPolicy`:
   * `admin_ips` = VPN IPs of every admin device;
   * `allowed[app_id]` = VPN IPs of the devices of every non-admin person granted `app_id`;
   * `site_for_host(hostname)` = the app id whose `hostname` matches (default `<app id>.<domain>`), otherwise
     the first DNS label of the hostname.
4. **Application.** Each proxy provider turns the policy into allow-lists, one per site key, containing
   `policy.ips_for(site)` (admins + allowed, sorted) and nothing else. Unchanged lists are not touched:
   **the sync is idempotent** and a second run reports `changed == []`.
   * `proxy/npm.py`: access list `<acl_prefix><site>` = `allow <ip>`... then `deny all`, attached to every
     proxy host of that site by HomeRoster itself.
   * `proxy/traefik.py`: middleware `<middleware_prefix><site>` (`ipAllowList`, `ipWhiteList` on v2) in the
     YAML file `file`, written atomically. Traefik's API is read-only, so the human attaches
     `<prefix><site>@file` to each router; a router referencing a missing middleware is disabled by Traefik
     (fail closed). Routers serving a site without its middleware become `SyncResult.warnings`.
5. **Unmanaged sites are admin-only.** A proxy host that is not a configured app (Radarr, Proxmox, HomeRoster
   itself…) still gets a list, containing only admin IPs.
6. **Safety rules** (do not weaken them):
   * if no admin device is found, the sync is **aborted** before touching the proxy (no self lock-out);
   * if the VPN cannot be listed, the sync is aborted;
   * if there is nobody in the database yet, nothing is done and no alert is sent;
   * after 3 consecutive failed syncs, the admin gets an e-mail.
7. **When the sync runs:** every `sync_interval` seconds, and right after any change (grant, revoke, device
   added or removed, profile saved, person created or removed, import).
8. **Revocation.** Removing an app cuts proxy access at once, then disables the account when the connector
   supports it (`supports_disable`). An account stays enabled while one of its *children* is still granted
   (Seerr signs in with the Jellyfin account, so Jellyfin stays enabled while Seerr is granted).
9. **Parents.** An app with `uses: <other app>` (or `default_parent`) reuses that app's account. Creating it
   creates the parent account first; granting it through the web form also grants the parent.

## Configuration reference

`config/homeroster.yaml` (see the annotated example). Environment variables:

| Variable | Default | Meaning |
|---|---|---|
| `HOMEROSTER_CONFIG` | `/config/homeroster.yaml` | Configuration file |
| `HOMEROSTER_DATA` | `/data` | SQLite database directory |
| `HOMEROSTER_SETUP_TOKEN` | — | Required once to create the admin account |
| `HOMEROSTER_NO_SYNC` | — | `1` disables the background sync (tests, demo) |
| `HOMEROSTER_PORT` | `8000` | `serve` port |
| `TZ` | `UTC` | Container timezone |

`${NAME}` and `${NAME:-default}` are replaced from the environment in every YAML value.

## Installation procedure (for an agent driving a shell)

**Proxmox VE (preferred when available):** on the node, as root,
`bash -c "$(curl -fsSL https://raw.githubusercontent.com/MaxenceMARTIN45/HomeRoster/develop/scripts/proxmox/homeroster-lxc.sh)"`
(interactive dialogs). It installs natively in a Debian 12 LXC: code `/opt/homeroster`, config `/etc/homeroster`,
data `/var/lib/homeroster`, systemd unit `homeroster`, and `/usr/local/bin/update` (upgrade with rollback).
Non-interactive on any Debian/Ubuntu host: `VPN=… PROXY=… APPS="…" bash scripts/proxmox/install.sh`.

**Docker:**

1. Check prerequisites on the Docker host: `docker compose version`; the VPN (WGDashboard or wg-easy), the
   reverse proxy API (NPM or Traefik) and the apps are
   reachable from this host (`curl -s -o /dev/null -w '%{http_code}' <url>`).
2. `git clone` the repository, `cd` into it.
3. Create the config non-interactively if needed: `cp config/homeroster.example.yaml config/homeroster.yaml`,
   keep ONE `# >>> vpn: <name>` block and ONE `# >>> proxy: <name>` block (uncomment it, delete the others;
   `install.sh` does this with awk), then edit `homeroster.owner`, `domain`, `language`, `trusted_proxies`, the `vpn`, `proxy`, `mail` URLs and
   the `apps` section (delete unused apps). Keep secrets as `${VAR}`.
4. Write `.env` with `chmod 600`: `HOMEROSTER_SETUP_TOKEN` (random, 24+ chars), `TZ`, and every variable the
   YAML references (`grep -oE '\$\{[A-Z0-9_]+\}' config/homeroster.yaml | sort -u`). Double any `$` in values
   (`$` → `$$`), Docker Compose expands them. Or run `bash scripts/install.sh` interactively.
5. `mkdir -p data && chown 10001:10001 data && chmod 700 data` (the container runs as uid 10001).
6. `docker compose up -d --build`, wait for `docker inspect -f '{{.State.Health.Status}}' homeroster` = `healthy`.
7. `docker exec homeroster python -m homeroster check` must print ✓ for every service. Fix URLs, keys or
   firewall rules until it does.
8. Ask the human to create a proxy host for HomeRoster (port 8000) restricted to their own VPN IP, then to
   open it, choose the language, create the admin account with the setup token and scan the TOTP QR code.
   **Do not** do the 2FA enrolment yourself.
9. Migrating from an existing setup: Settings → *Bulk import* takes TOML (`admins = [...]` plus a `[users]`
   table of `slug = ["app", ...]`), then *Link their existing accounts* on each person page. Set
   `proxy.acl_prefix` to the existing access-list prefix to reuse the lists already attached in NPM.

Updating: `git pull && bash scripts/install.sh --update`. The database schema is created on start
(`db.SCHEMA`, `PRAGMA user_version`); add a migration there if you change it.

## Recipes

### Add an app without code

Add an entry with `connector: rest` (full syntax in `providers/apps/rest.py` and the Nextcloud example in
`config/homeroster.example.yaml`). Required endpoints: `create`, `delete`. Optional: `find` (link existing
accounts), `set_password`, `enable` + `disable` (both, to support disabling), `health` (+ `version` path).
Placeholders: `{slug} {name} {email} {username} {password} {id} {login}`. Use `success` when the API answers
HTTP 200 with an error code in the body. Test it with `docker exec homeroster python -m homeroster check`.

### Add an app connector in Python

1. Create `src/homeroster/providers/apps/<name>.py`, subclass `AppConnector`, decorate with
   `@register_app("<name>")`. Set `default_label`, `default_description = {"en": ..., "fr": ...}`,
   `default_color`, `default_icon` (a symbol id of `static/icons.svg`), `login_kind`, `requires_email`,
   `supports_disable`, `manages_password`, `default_parent` as needed.
2. Implement `create` and `delete`; add `find`, `set_password`, `set_enabled`, `version` when the API allows.
   Use `HttpService` for HTTP calls: it turns network and HTTP failures into translated `ServiceError`s.
   Raise `ServiceError(self.label, "errors.<key>", **params)` for your own errors.
3. Add the module to `BUILTIN_MODULES` in `providers/registry.py` (or load it with `homeroster.plugins`), its
   options to `catalog.APPS` (Settings > Services) and a `# >>> app: <name>` block to the example config.
4. E-mail logo: set `default_logo` to the app's icon name in the dashboard-icons collection
   (https://dashboard-icons.homarr.dev). The offline fallback pictogram comes from `APP_ICONS` in
   `scripts/render_assets.py` (`static/apps/<name>.png`); without one the generic pictogram is used.
5. Simulate the API in `tests/fakes.py` (a `_<host>` method on `FakeHomelab`) and add a test like
   `tests/test_rest_connector.py`.
6. Document the required API key permissions in the module docstring and in the README table.

### Replace the VPN

Implement `VpnProvider` (`devices`, `create_device`, `delete_devices`, `client_config`, optionally `version`),
register it with `@register_vpn("<name>")`, set `client_app` (shown in the e-mail and QR dialog). The device
IP must be the address the reverse proxy sees (split tunnel: the VPN subnet). Keep the naming convention:
HomeRoster creates devices named `"<slug> - <device>"` and relies on reading those names back.
Implementations to copy: `vpn/wgdashboard.py` (API key), `vpn/wg_easy.py` (Basic auth, pagination),
`vpn/wireguard.py` (files). **Invitation VPNs** (`vpn/mesh.py`): set `invite_only = True`; `create_device`
returns `Device(pending=True, extra={"invite": text})` with an id `invite:<slug>:<ref>` (HomeRoster stores the
text in kv `vpn_invitations` and shows it as the QR code), `devices()` fills `Device.owner` (slug or e-mail) and
`extra["created"]` (Unix time: an invitation is dropped once a device of that person appears after it). When
the VPN cannot tell who a new device belongs to, implement `claim()` (see ZeroTier).
Add a `# >>> vpn: <name>` block to `config/homeroster.example.yaml` and the choice to `scripts/install.sh`.

### Replace the reverse proxy

Implement `AccessProxy.apply(policy, dry_run=...)` and register it with `@register_proxy("<name>")`. It must
enumerate the sites it serves, map each hostname through `policy.site_for_host`, enforce exactly
`policy.ips_for(site)` + deny all, be idempotent, report `changed` / `unchanged` / `sites`, and change nothing
when `dry_run` is true. Never leave a site without a list: unmanaged sites must stay admin-only.
Implementations to copy: `proxy/npm.py` and `proxy/zoraxy.py` (write through the API), `proxy/traefik.py`
(read-only API, writes a file; reports unprotected routers in `SyncResult.warnings`), `proxy/files.py` (cannot
list its sites: one file per `self.managed_sites` plus `admins`, which every other site must include). Add a `# >>> proxy: <name>` block to the
example config and the choice to `scripts/install.sh`.

### Add a language

Copy `src/homeroster/locales/en.json` to `<code>.json`, set `_meta.name` to the language's own name, translate
every value, keep every `{placeholder}`. Set `_meta.direction` to `rtl` for right-to-left scripts. `pytest tests/test_config_i18n.py` checks keys and placeholders.
`password.words` is the word list for generated passwords (lowercase ASCII, space separated: keep English
words for non-Latin scripts, passwords must be easy to type on any keyboard).

### Apply the person's language to an app

Set `supports_language = True` and implement `set_language(account, language)` (HomeRoster codes: `en`, `fr`,
`es`, `de`, `it`, `pt`, `ru`, `zh`, `hi`, `ar`; map them to the app's codes). It runs after the account is
created and when the person's language changes, and its failures are only logged. With the `rest` connector,
add a `language` endpoint using `{language}` and an optional `languages:` mapping.

### Add a user-visible string

Use `t("area.key", name=value)` in Python and templates, `t("key")` in `app.js` for keys under `js.`. Add the
key to **every** locale file. E-mails are rendered with the recipient's language (`render(..., lang=...)`).

## Invariants and conventions

* The web layer never calls providers directly for business actions: it calls `Roster` methods.
* `Roster` methods never raise for a single failing service during multi-step operations: they collect
  per-step results (`run_creation`, `delete_person`, `reset_password`).
* Audit entries store a key (`db.audit("grant_added", ...)`) translated at display time (`audit.<key>`).
* Templates must not contain inline `style=""` or `<script>` code (CSP); the only inline script is the JSON
  data block `#i18n`. E-mail templates are the exception (they are not served by the app, except the
  preview route, which sends its own CSP).
* Destructive actions require CSRF and a fresh TOTP code.
* Keep dependencies minimal and pinned in `requirements.txt` (ranges in `pyproject.toml`).
* Code, comments, docs and commit messages are in English.

## Commands

```bash
pip install -r requirements-dev.txt
pytest                                  # full suite, ~2 s
ruff check src tests scripts
python tests/demo_server.py             # http://127.0.0.1:8765, sign in maxence / demo-demo-demo + printed TOTP
python -m homeroster check --config config/homeroster.yaml
python scripts/render_assets.py         # after changing a logo, an icon or docs/assets/how-it-works.html
python scripts/take_screenshots.py      # after changing the UI
```
