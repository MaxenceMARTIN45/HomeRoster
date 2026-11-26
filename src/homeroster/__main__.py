"""Command line: ``python -m homeroster <command>``.

  check     Load the configuration and test the connection to every service (exit code 1 on failure).
  serve     Start the web server (what the Docker image runs).
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys

from . import __version__
from .config import ConfigError, load_config


def _check(args: argparse.Namespace) -> int:
    from .core.roster import Roster
    from .i18n import set_language

    try:
        config = load_config(args.config)
        roster = Roster(config)
    except (ConfigError, LookupError, ValueError) as e:
        print(f"✗ {e}")
        return 1
    set_language(config.language)
    print(f"HomeRoster {__version__} · {len(roster.apps)} app(s)\n")
    results = asyncio.run(roster.health_all())
    for item in results:
        mark = "✓" if item["ok"] else "✗"
        version = f" ({item['version']})" if item["version"] else ""
        print(f"  {mark} {item['label']:<24} {item['message']}{version}")
    return 0 if all(item["ok"] for item in results) else 1


def _serve(args: argparse.Namespace) -> int:
    import uvicorn

    uvicorn.run("homeroster.web.app:create_app", factory=True, host=args.host, port=args.port,
                proxy_headers=False, server_header=False, log_level="info")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="homeroster", description="Manage the people you share your homelab with.")
    parser.add_argument("--version", action="version", version=f"HomeRoster {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)
    check = sub.add_parser("check", help="test the connection to every configured service")
    check.add_argument("--config", default=os.environ.get("HOMEROSTER_CONFIG"))
    check.set_defaults(func=_check)
    serve = sub.add_parser("serve", help="start the web server")
    serve.add_argument("--host", default="0.0.0.0")
    serve.add_argument("--port", type=int, default=int(os.environ.get("HOMEROSTER_PORT", "8000")))
    serve.set_defaults(func=_serve)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
