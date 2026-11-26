# Contributing to HomeRoster

Thanks for helping! Bug reports, new connectors, translations and documentation fixes are all welcome.

## Before you start

* For a bug, open an issue with the steps to reproduce, what you expected and the relevant lines of
  `docker compose logs`. **Remove every secret, domain and IP address you do not want to publish.**
* For a new feature or a new provider, open an issue first so we can agree on the shape.
* Security issues go through [SECURITY.md](SECURITY.md), never a public issue.

## Development setup

```bash
python -m venv .venv && . .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt
pytest
ruff check src tests scripts
python tests/demo_server.py                      # the real UI with made-up data on http://127.0.0.1:8765
```

The tests and the demo simulate WGDashboard, wg-easy, Nginx Proxy Manager, Traefik and every app in `tests/fakes.py`: no real
service or network access is needed.

## Guidelines

* Read [AGENTS.md](AGENTS.md): it describes the architecture, the access model and step-by-step recipes for
  new connectors, providers and languages.
* Keep the core generic: app-specific code lives in its connector.
* Every user-visible string uses `t()` and exists in every `locales/*.json` file.
* Add or update tests for any behaviour change; keep `pytest` and `ruff check` green.
* Keep dependencies to a minimum. No CDN, no inline script or style in pages (strict CSP).
* Write code, comments, docs and commit messages in English. Commit messages: imperative mood, short subject.

## Pull requests

1. Fork, create a branch from `develop`.
2. Make focused commits; update the README, AGENTS.md and CHANGELOG when behaviour changes.
3. Refresh the screenshots (`python scripts/take_screenshots.py`) if the UI changed.
4. Open the pull request and fill in the template.
