# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses [Semantic Versioning](https://semver.org/).

## [1.0.0] - 2026-11-26

### Added

- One-tap onboarding: VPN device with QR code, reverse-proxy access, app accounts and welcome e-mail, with live progress.
- Access rights rebuilt from HomeRoster's database every 5 minutes, with self lock-out protection.
- VPNs: WGDashboard, wg-easy, plain WireGuard (`wg0.conf`), PiVPN, and the invitation VPNs Tailscale, Headscale,
  NetBird and ZeroTier. Reverse proxies: Nginx Proxy Manager, Traefik, Caddy, nginx, SWAG, HAProxy, Zoraxy.
  The installer asks which ones you use; `scripts/host-watcher.sh` reloads file-based services.
- Apps: Plex (server sharing by e-mail), Emby, Audiobookshelf, Navidrome, Vaultwarden (invitations) and
  Home Assistant (WebSocket API: OS, Container and Core).
- Settings > Services: add, change or remove apps, switch VPN or reverse proxy from the interface; each change is
  tested, saved to `services.yaml` and applied without a restart.
- The installer asks which apps you run. Proxmox VE: a one-command LXC installer and an `update` command that
  keeps every setting and rolls back on failure.
- A language picker in the sidebar.
- The person's language is applied to their Seerr, Jellyfin (audio and subtitles) and Nextcloud accounts.
- App connectors: Jellyfin, Seerr, Immich, Open WebUI, and a declarative `rest` connector configured in YAML.
- 10 languages (English, Chinese, Hindi, Spanish, French, Arabic with right-to-left layout, Portuguese, Russian,
  German, Italian), chosen at setup; per-person e-mail language; one JSON file per language.
- Welcome e-mail with your homelab's logo, the official app icons (dashboard-icons, cached) and an in-app preview.
- Admin-only access with argon2 and TOTP, CSRF protection, strict CSP and brute-force throttling.
- Interactive installer, `homeroster check` command, hardened Docker image.
