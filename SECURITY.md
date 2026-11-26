# Security policy

HomeRoster holds API keys that can create and delete accounts across a homelab, and decides who can reach
which service. Security reports are taken seriously.

## Reporting a vulnerability

Please **do not open a public issue**. Use GitHub's private
[security advisory form](https://github.com/MaxenceMARTIN45/homeroster/security/advisories/new) instead.
Include the affected version, the steps to reproduce and the impact. You will get an answer within a week.

## Supported versions

Only the latest release receives security fixes.

## Deployment recommendations

* Expose HomeRoster only through your reverse proxy, restricted to your own VPN devices; never to the internet.
* Use dedicated, least-privilege credentials for each service (for example a non-admin NPM user limited to
  proxy hosts and access lists).
* Keep `.env` readable by its owner only (`chmod 600`) and back up `data/` securely: it contains the TOTP
  secret of the admin account.
* Keep the host, Docker and HomeRoster up to date.
