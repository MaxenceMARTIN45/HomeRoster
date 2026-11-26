"""Headless Chromium helpers shared by the asset and screenshot scripts."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def find_browser() -> str:
    candidates = [
        os.environ.get("CHROME_PATH", ""),
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        shutil.which("chromium") or "", shutil.which("chromium-browser") or "", shutil.which("google-chrome") or "",
    ]
    for candidate in candidates:
        if candidate and Path(candidate).exists():
            return candidate
    sys.exit("No Chromium-based browser found (set CHROME_PATH).")


def screenshot_url(browser: str, url: str, out: Path, width: int, height: int, *, scale: float = 1,
                   dark: bool = False, transparent: bool = False) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    out.unlink(missing_ok=True)
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        args = [browser, "--headless=new", "--disable-gpu", "--hide-scrollbars", f"--force-device-scale-factor={scale}",
                f"--user-data-dir={tmp}/profile", f"--window-size={width},{height}", f"--screenshot={out}"]
        if transparent:
            args.append("--default-background-color=00000000")
        # Do not inherit the OS theme: 0 = dark, 1 = light (prefers-color-scheme).
        args.append(f"--blink-settings=preferredColorScheme={0 if dark else 1}")
        args.append("--virtual-time-budget=6000")  # let page scripts (live job polling) finish first
        subprocess.run([*args, url], check=True, capture_output=True, timeout=90)
        deadline = time.monotonic() + 30  # some launchers return before the headless child wrote the file
        while not out.exists() and time.monotonic() < deadline:
            time.sleep(0.2)
    if not out.exists():
        sys.exit(f"the browser did not write {out}")
    print(f"  wrote {out.relative_to(ROOT).as_posix()}")


def screenshot_html(browser: str, html: str, out: Path, width: int, height: int) -> None:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        page = Path(tmp) / "page.html"
        page.write_text(html, encoding="utf-8")
        screenshot_url(browser, page.as_uri(), out, width, height, transparent=True)
