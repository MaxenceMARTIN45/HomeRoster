"""Capture the README screenshots from the local demo server (made-up data, simulated services).

    python scripts/take_screenshots.py            (needs Chrome, Edge or Chromium, and internet for the app icons)

Two fixed formats, so the README stays tidy:
  desktop-*.png   a 1440x900 page in a browser window, on the brand gradient (1568x1072, rendered at 1.25x)
  phone-*.png     a 390x844 page in a phone frame, on the same gradient (560x960, rendered at 2x)

Writes docs/assets/screenshots/. The demo server is started and stopped automatically. If
private/homelab-logo.png exists (gitignored), the welcome e-mail shows it as the homelab's logo.
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
DESKTOP = (1568, 1072, 1.25)
PHONE = (560, 960, 2)

# (file name, format, page, dark, scroll)
SHOTS = [
    ("desktop-people-light", "desktop", "/people", False, 0),
    ("desktop-people-dark", "desktop", "/people", True, 0),
    ("desktop-new-person", "desktop", "/people/new", False, 0),
    ("desktop-creation", "desktop", "/jobs/demo-job", True, 0),
    ("desktop-person", "desktop", "/__demo/p/julia", False, 0),
    ("desktop-status", "desktop", "/status", True, 0),
    ("desktop-services", "desktop", "/settings/services", False, 0),
    ("desktop-settings", "desktop", "/settings", True, 0),
    ("phone-people", "phone", "/people", False, 0),
    ("phone-person", "phone", "/__demo/p/julia", True, 0),
    ("phone-email", "phone", "/__demo/p/julia/welcome/preview", False, 0),
    ("phone-email-steps", "phone", "/__demo/p/julia/welcome/preview", True, 560),
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
        for name, kind, path, dark, scroll in SHOTS:
            width, height, scale = DESKTOP if kind == "desktop" else PHONE
            frame = f"/__demo/{kind}?src={quote(path)}&scroll={scroll}" + ("&dark=1" if dark else "")
            # Each capture runs in a fresh browser profile, so it signs in through the demo-only shortcut.
            url = f"{BASE}/__demo/login?next={quote(frame)}"
            screenshot_url(browser, url, OUT / f"{name}.png", width, height, scale=scale, dark=dark)
    finally:
        server.terminate()


if __name__ == "__main__":
    main()
