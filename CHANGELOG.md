# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses [Semantic Versioning](https://semver.org/).

## [1.0.0] - 2026-11-26

### Added

- One-tap onboarding: VPN device with QR code, reverse-proxy access, app accounts and welcome e-mail, with live progress.
- Access rights rebuilt from HomeRoster's database every 5 minutes, with self lock-out protection.
- Pluggable providers: WGDashboard (WireGuard), Nginx Proxy Manager, SMTP.
- App connectors: Jellyfin, Seerr, Immich, Open WebUI, and a declarative `rest` connector configured in YAML.
- English and French interface, chosen at setup; per-person e-mail language; one JSON file per language.
- Welcome e-mail with inline app logos and an in-app preview.
- Admin-only access with argon2 and TOTP, CSRF protection, strict CSP and brute-force throttling.
- Interactive installer, `homeroster check` command, hardened Docker image.
