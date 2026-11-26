"""Capture the README screenshots from the local demo server (made-up data, simulated services).

    python scripts/take_screenshots.py            (needs Chrome, Edge or Chromium)

Writes docs/assets/screenshots/*.png. The demo server is started and stopped automatically.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
import urllib.request
from urllib.parse import quote

from _browser import ROOT, find_browser, screenshot_url

OUT = ROOT / "docs" / "assets" / "screenshots"
PORT = 8766
BASE = f"http://127.0.0.1:{PORT}"

# (file name, path, width, height, scale, dark)
SHOTS = [
    ("people-light", "/people", 1360, 860, 1.5, False),
    ("people-dark", "/people", 1360, 860, 1.5, True),
    ("new-person", "/people/new", 1360, 1180, 1.5, False),
    ("person", "/people/2", 1360, 1500, 1.5, False),
    ("creation", "/jobs/demo-job", 1360, 900, 1.5, True),
    ("status", "/status", 1360, 900, 1.5, False),
    ("welcome-email", "/people/2/welcome/preview", 700, 1640, 1.5, False),
    ("mobile-people", "/people", 390, 844, 2, False),
    ("mobile-person", "/people/2", 390, 844, 2, True),
]


def wait_for(url: str, timeout: float = 30) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            urllib.request.urlopen(url, timeout=2)
            return
        except OSError:
            time.sleep(0.3)
    sys.exit(f"demo server did not start on {url}")


def main() -> None:
    browser = find_browser()
    env = {**os.environ, "PORT": str(PORT), "DEMO_LANG": os.environ.get("DEMO_LANG", "en")}
    server = subprocess.Popen([sys.executable, str(ROOT / "tests" / "demo_server.py")], env=env,
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        wait_for(f"{BASE}/healthz")
        for name, path, width, height, scale, dark in SHOTS:
            # Each capture runs in a fresh browser profile, so it signs in through the demo-only shortcut.
            if width < 500:  # headless windows cannot be that narrow: show the page in a 390x844 phone frame
                url = f"{BASE}/__demo/login?next={quote('/__demo/phone?src=' + quote(path))}"
                width, height = 560, 960
            else:
                url = f"{BASE}/__demo/login?next={quote(path)}"
            screenshot_url(browser, url, OUT / f"{name}.png", width, height, scale=scale, dark=dark)
    finally:
        server.terminate()


if __name__ == "__main__":
    main()
