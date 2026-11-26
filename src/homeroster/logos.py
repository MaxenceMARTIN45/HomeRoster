"""Logos embedded in the e-mails: the homelab's own logo and one per app.

A logo setting (``logo:`` of an app, ``homeroster.logo``) can be:

* ``builtin``: HomeRoster's own pictogram (``static/apps/<connector>.png``);
* an ``http(s)://`` URL: downloaded once, then cached in ``<data>/logos``;
* a file path (contains ``/`` or ends in .png / .jpg), e.g. ``/config/logo.png``;
* any other word: an icon name from the dashboard-icons collection (https://dashboard-icons.homarr.dev),
  such as ``nextcloud``. The built-in connectors default to their app's official icon from there.

The trademarks and logos belong to their owners; HomeRoster does not ship them, it only downloads them.
A logo that cannot be loaded falls back to the pictogram, so an e-mail never fails because of a logo.
"""

from __future__ import annotations

import hashlib
import logging
import os
from pathlib import Path

import httpx

from .providers import base

log = logging.getLogger(__name__)

ICONS_URL = "https://cdn.jsdelivr.net/gh/homarr-labs/dashboard-icons/png/{name}.png"
MAX_BYTES = 1_000_000
SIGNATURES = {b"\x89PNG\r\n\x1a\n": "png", b"\xff\xd8\xff": "jpeg"}


def image_type(data: bytes) -> str | None:
    """``png`` or ``jpeg`` (what every mail client displays), else None."""
    return next((kind for magic, kind in SIGNATURES.items() if data.startswith(magic)), None)


def source_of(setting: str) -> str:
    """Normalise a logo setting to ``builtin``, a URL or a path."""
    setting = setting.strip()
    if not setting or setting == "builtin" or setting.startswith(("http://", "https://")):
        return setting or "builtin"
    if "/" in setting or "\\" in setting or setting.lower().endswith((".png", ".jpg", ".jpeg")):
        return setting
    return ICONS_URL.format(name=setting.lower())


async def _download(url: str, cache_dir: Path) -> bytes | None:
    cached = cache_dir / (hashlib.sha256(url.encode()).hexdigest()[:24] + ".img")
    if cached.is_file():
        return cached.read_bytes()
    try:
        async with httpx.AsyncClient(timeout=base.TIMEOUT, transport=base.TRANSPORT, follow_redirects=True) as client:
            response = await client.get(url)
    except httpx.HTTPError as e:
        log.info("logo %s not downloaded: %s", url, e.__class__.__name__)
        return None
    data = response.content if response.status_code == 200 else b""
    if not image_type(data) or len(data) > MAX_BYTES:
        log.info("logo %s ignored (HTTP %s, not a PNG/JPEG under 1 MB)", url, response.status_code)
        return None
    try:
        cache_dir.mkdir(parents=True, exist_ok=True)
        tmp = cached.with_suffix(".tmp")
        tmp.write_bytes(data)
        os.replace(tmp, cached)
    except OSError:
        pass  # a read-only data directory only costs a download per e-mail
    return data


async def load(setting: str, *, cache_dir: Path, fallback: Path | None = None) -> bytes | None:
    """The logo's bytes (PNG or JPEG), else the fallback file, else None."""
    source = source_of(setting)
    data: bytes | None = None
    if source.startswith(("http://", "https://")):
        data = await _download(source, cache_dir)
    elif source != "builtin":
        try:
            data = Path(source).read_bytes()
        except OSError:
            log.warning("logo file not readable: %s", source)
        if data is not None and (not image_type(data) or len(data) > MAX_BYTES):
            log.warning("logo file ignored (not a PNG/JPEG under 1 MB): %s", source)
            data = None
    if data is None and fallback is not None and fallback.is_file():
        data = fallback.read_bytes()
    return data
