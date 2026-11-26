"""Plain WireGuard and PiVPN: HomeRoster edits the server's ``wg0.conf`` itself, no dashboard needed.

* ``wireguard``: peers are added to ``config_file`` (e.g. ``/etc/wireguard/wg0.conf``), each preceded by a
  ``# HomeRoster: <name>`` comment. Client files are kept next to it, in ``clients/`` (mode 600).
* ``pivpn``: same, in PiVPN's own layout, so ``pivpn -l`` / ``pivpn -c`` keep working: ``### begin <name> ###``
  markers in ``wg0.conf``, ``configs/<name>.conf`` and ``configs/clients.txt``. PiVPN names cannot contain
  spaces, so ``julia - phone`` is stored as ``julia--phone``.

HomeRoster runs in a container and cannot reload the interface: mount the WireGuard directory on
``/wireguard`` and install the host watcher (``bash scripts/host-watcher.sh wireguard``), which runs
``wg syncconf`` when the file changes and writes ``handshakes`` every minute (the online status).

Options (``vpn:`` section): config_file (/wireguard/wg0.conf), endpoint (public ``host:port``, required),
dns, allowed_ips (what the devices route through the tunnel, default the VPN subnet), keepalive (25), mtu.
"""

from __future__ import annotations

import base64
import ipaddress
import os
import re
import secrets
import tempfile
import time
from pathlib import Path
from typing import Any

from ..apps.base import Person
from ..base import NotConfigured, ServiceError
from ..registry import register_vpn
from .base import Device, VpnProvider

# ---------------------------------------------------------------------------------- X25519 (RFC 7748)
# WireGuard keys are Curve25519 keys. This small, constant-structure implementation avoids a dependency;
# it only runs when a device is created.
_P = 2**255 - 19
_A24 = 121665


def _x25519(scalar: bytes, u_point: bytes) -> bytes:
    k = bytearray(scalar)
    k[0] &= 248
    k[31] &= 127
    k[31] |= 64
    n = int.from_bytes(k, "little")
    x1 = int.from_bytes(u_point, "little") & ((1 << 255) - 1)
    x2, z2, x3, z3, swap = 1, 0, x1, 1, 0
    for t in reversed(range(255)):
        bit = (n >> t) & 1
        swap ^= bit
        if swap:
            x2, x3, z2, z3 = x3, x2, z3, z2
        swap = bit
        a, b = x2 + z2, x2 - z2
        aa, bb = a * a % _P, b * b % _P
        e = aa - bb
        c, d = x3 + z3, x3 - z3
        da, cb = d * a % _P, c * b % _P
        x3, z3 = (da + cb) ** 2 % _P, x1 * (da - cb) ** 2 % _P
        x2, z2 = aa * bb % _P, e * (aa + _A24 * e) % _P
    if swap:
        x2, z2 = x3, z3
    return (x2 * pow(z2, _P - 2, _P) % _P).to_bytes(32, "little")


def public_key(private_b64: str) -> str:
    return base64.b64encode(_x25519(base64.b64decode(private_b64), (9).to_bytes(32, "little"))).decode()


def generate_keypair() -> tuple[str, str]:
    private = base64.b64encode(secrets.token_bytes(32)).decode()
    return private, public_key(private)


# ---------------------------------------------------------------------------------- wg0.conf model

PEER_COMMENT = re.compile(r"^#\s*HomeRoster:\s*(.+?)\s*$")
PIVPN_BEGIN = re.compile(r"^###\s*begin\s+(\S+)\s*###\s*$")


def _sections(text: str) -> list[tuple[str, list[str]]]:
    """Split a WireGuard file into ``(header comment, lines)`` blocks: ``[Interface]`` first, then peers."""
    blocks: list[tuple[str, list[str]]] = []
    comment, current = "", None
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            current = [line]
            blocks.append((comment, current))
            comment = ""
        elif PEER_COMMENT.match(stripped) or PIVPN_BEGIN.match(stripped):
            comment = stripped
        elif current is not None and not stripped.startswith("### end"):
            current.append(line)
    return blocks


def _value(lines: list[str], key: str) -> str:
    for line in lines:
        name, sep, value = line.partition("=")
        if sep and name.strip().lower() == key.lower():
            return value.strip()
    return ""


@register_vpn("wireguard")
class WireGuardFile(VpnProvider):
    label = "WireGuard"
    client_app = "WireGuard"
    pivpn = False

    def __init__(self, options: dict[str, Any]):
        super().__init__(options)
        self.file = Path(str(options.get("config_file") or ("/wireguard/wg0.conf")))
        self.endpoint = str(options.get("endpoint") or "")

    # ---- names (PiVPN cannot store spaces)
    def _stored(self, name: str) -> str:
        return re.sub(r"\s*-\s*", "--", name.strip(), count=1).replace(" ", "_") if self.pivpn else name

    def _shown(self, stored: str) -> str:
        return stored.replace("--", " - ", 1) if self.pivpn else stored

    def _clients_dir(self) -> Path:
        return self.file.parent / ("configs" if self.pivpn else "clients")

    def _read(self) -> str:
        if not self.endpoint:
            raise NotConfigured(self.label)
        try:
            return self.file.read_text(encoding="utf-8")
        except OSError as e:
            raise ServiceError(self.label, "errors.file_write", path=str(self.file), error=e.strerror or str(e)) from e

    def _write(self, path: Path, text: str, mode: int = 0o600) -> None:
        try:
            fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".homeroster-", suffix=".tmp")
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(text)
            os.chmod(tmp, mode)
            os.replace(tmp, path)
        except OSError as e:
            raise ServiceError(self.label, "errors.file_write", path=str(path), error=e.strerror or str(e)) from e

    def _peers(self, text: str) -> list[dict[str, str]]:
        peers = []
        for comment, lines in _sections(text)[1:]:
            match = PEER_COMMENT.match(comment) or PIVPN_BEGIN.match(comment)
            key = _value(lines, "PublicKey")
            allowed = _value(lines, "AllowedIPs").split(",")[0].strip()
            peers.append({"name": self._shown(match.group(1)) if match else "", "key": key,
                          "ip": allowed.split("/")[0]})
        return peers

    def _handshakes(self) -> dict[str, float]:
        """``wg show wg0 latest-handshakes`` written by the host watcher: ``<public key>\\t<unix time>``."""
        try:
            lines = (self.file.parent / "handshakes").read_text(encoding="utf-8").splitlines()
        except OSError:
            return {}
        out = {}
        for line in lines:
            parts = line.split()
            if len(parts) == 2 and parts[1].isdigit():
                out[parts[0]] = float(parts[1])
        return out

    async def version(self) -> str:
        interface = _sections(self._read())[0][1] if _sections(self._read()) else []
        if not _value(interface, "PrivateKey"):
            raise ServiceError(self.label, "errors.unexpected_response")
        return "PiVPN" if self.pivpn else "wg0.conf"

    async def devices(self) -> list[Device]:
        handshakes = self._handshakes()
        now = time.time()
        return [Device(id=p["key"], name=p["name"], ip=p["ip"], online=now - handshakes.get(p["key"], 0) < 180)
                for p in self._peers(self._read()) if p["key"]]

    def _next_ip(self, text: str, interface: list[str]) -> ipaddress.IPv4Interface:
        server = ipaddress.ip_interface(_value(interface, "Address").split(",")[0].strip())
        used = {ipaddress.ip_address(p["ip"]) for p in self._peers(text) if p["ip"]} | {server.ip}
        for host in server.network.hosts():
            if host not in used:
                return ipaddress.ip_interface(f"{host}/32")
        raise ServiceError(self.label, "errors.raw", text=f"no free address left in {server.network}")

    async def create_device(self, name: str, owner: Person | None = None) -> Device:
        text = self._read()
        if any(p["name"] == name for p in self._peers(text)):
            raise ServiceError(self.label, "errors.device_exists", name=name)
        blocks = _sections(text)
        interface = blocks[0][1] if blocks else []
        server_private = _value(interface, "PrivateKey")
        if not server_private:
            raise ServiceError(self.label, "errors.unexpected_response")
        private, public = generate_keypair()
        psk = base64.b64encode(secrets.token_bytes(32)).decode()
        ip = self._next_ip(text, interface)
        stored = self._stored(name)
        marker = f"### begin {stored} ###" if self.pivpn else f"# HomeRoster: {name}"
        peer = f"{marker}\n[Peer]\nPublicKey = {public}\nPresharedKey = {psk}\nAllowedIPs = {ip}\n"
        if self.pivpn:
            peer += f"### end {stored} ###\n"
        network = ipaddress.ip_interface(_value(interface, "Address").split(",")[0].strip()).network
        client = (f"[Interface]\nPrivateKey = {private}\nAddress = {ip}\n"
                  + (f"DNS = {self.options['dns']}\n" if self.options.get("dns") else "")
                  + (f"MTU = {self.options['mtu']}\n" if self.options.get("mtu") else "")
                  + f"\n[Peer]\nPublicKey = {public_key(server_private)}\nPresharedKey = {psk}\n"
                  f"Endpoint = {self.endpoint}\nAllowedIPs = {self.options.get('allowed_ips') or network}\n"
                  f"PersistentKeepalive = {int(self.options.get('keepalive') or 25)}\n")
        clients = self._clients_dir()
        try:
            clients.mkdir(mode=0o700, exist_ok=True)
        except OSError as e:
            raise ServiceError(self.label, "errors.file_write", path=str(clients), error=e.strerror or str(e)) from e
        self._write(clients / f"{stored}.conf", client)
        if self.pivpn:  # "name public-key creation-time" lines, as PiVPN writes them
            index = clients / "clients.txt"
            listing = index.read_text(encoding="utf-8") if index.exists() else ""
            self._write(index, listing + f"{stored} {public} {int(time.time())}\n", 0o644)
        self._write(self.file, text.rstrip("\n") + "\n\n" + peer)
        return Device(id=public, name=name, ip=str(ip.ip))

    async def client_config(self, device_id: str) -> str:
        peer = next((p for p in self._peers(self._read()) if p["key"] == device_id), None)
        path = self._clients_dir() / f"{self._stored(peer['name'])}.conf" if peer else None
        if not path or not path.is_file():
            raise ServiceError(self.label, "errors.config_missing")
        return path.read_text(encoding="utf-8")

    async def delete_devices(self, device_ids: list[str]) -> None:
        wanted = {d for d in device_ids if not d.startswith("invite:")}
        if not wanted:
            return
        text = self._read()
        removed = [p["name"] for p in self._peers(text) if p["key"] in wanted]
        lines = text.splitlines()
        headers = [i for i, line in enumerate(lines) if line.strip().startswith("[") and line.strip().endswith("]")]
        # A block starts at its section header, or at the marker comment right above it, and runs until the
        # next block starts.
        starts = [h - 1 if h and (PEER_COMMENT.match(lines[h - 1].strip()) or PIVPN_BEGIN.match(lines[h - 1].strip()))
                  else h for h in headers]
        drop: set[int] = set()
        for n, begin in enumerate(starts):
            finish = starts[n + 1] if n + 1 < len(starts) else len(lines)
            if lines[headers[n]].strip().lower() == "[peer]" and _value(lines[begin:finish], "PublicKey") in wanted:
                drop.update(range(begin, finish))
        out = [line for i, line in enumerate(lines) if i not in drop]
        self._write(self.file, re.sub(r"\n{3,}", "\n\n", "\n".join(out)).rstrip("\n") + "\n")
        clients = self._clients_dir()
        for name in removed:
            (clients / f"{self._stored(name)}.conf").unlink(missing_ok=True)
        if self.pivpn and (clients / "clients.txt").exists():
            keep = [line for line in (clients / "clients.txt").read_text(encoding="utf-8").splitlines()
                    if len(line.split()) < 2 or line.split()[1] not in wanted]
            self._write(clients / "clients.txt", "\n".join(keep) + ("\n" if keep else ""), 0o644)


@register_vpn("pivpn")
class PiVPN(WireGuardFile):
    label = "PiVPN"
    pivpn = True
