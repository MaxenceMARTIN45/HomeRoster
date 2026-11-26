"""Naming convention linking VPN devices to people.

Every device is named ``"<person> - <device>"`` where ``<person>`` is the person's slug:
    "Alice"       -> "alice"        -> device "alice - phone"
    "Jean-Luc"    -> "jean_luc"     -> device "jean_luc - laptop"
    "Zoé Durand"  -> "zoe_durand"
This is what lets HomeRoster give a device the rights of its owner, even when the device was created by
hand in the VPN dashboard.
"""

from __future__ import annotations

import re
import unicodedata

SLUG_RE = re.compile(r"^[a-z0-9_]{1,32}$")
DEVICE_RE = re.compile(r"^[a-z0-9_]{1,24}$")
DEVICE_NAME_RE = re.compile(r"^\s*([a-z0-9_]+)\s+-\s+(.+?)\s*$")


def slugify(text: str) -> str:
    text = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode("ascii")
    text = re.sub(r"[^a-zA-Z0-9]+", "_", text).strip("_").lower()
    return re.sub(r"_+", "_", text)[:32]


def device_slug(text: str) -> str:
    return slugify(text)[:24]


def device_name(person_slug: str, device: str) -> str:
    return f"{person_slug} - {device}"


def parse_device_name(name: str) -> tuple[str, str] | None:
    """``"alice - laptop"`` -> ``("alice", "laptop")``; ``None`` when the name does not follow the convention."""
    match = DEVICE_NAME_RE.fullmatch(name or "")
    return (match.group(1), match.group(2)) if match else None
