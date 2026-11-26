"""An in-memory homelab: fake WGDashboard, wg-easy, Nginx Proxy Manager, Traefik, Jellyfin, Seerr, Immich, Open WebUI and Nextcloud.

Served through ``httpx.MockTransport``, so the tests (and the local demo) never touch the network.
"""

from __future__ import annotations

import base64
import json
import re
from typing import Any

import httpx

DOMAIN = "example.com"
# The smallest valid PNG (1x1, transparent).
PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII=")

CONFIG: dict[str, Any] = {
    "homeroster": {"owner": "Alex", "domain": DOMAIN, "language": "en", "trusted_proxies": ["172.16.0.1"]},
    "vpn": {"provider": "wgdashboard", "url": "http://wg.test", "api_key": "test-key", "dns": "10.8.0.1",
            "allowed_ips": "10.8.0.0/24"},
    "proxy": {"provider": "npm", "url": "http://npm.test", "email": "bot@example.com", "password": "test-password"},
    "mail": {"provider": "smtp", "host": "smtp.test", "username": "homelab@example.com", "password": "test"},
    "apps": {
        "jellyfin": {"connector": "jellyfin", "url": "http://jellyfin.test", "api_key": "k"},
        "seerr": {"connector": "seerr", "url": "http://seerr.test", "api_key": "k", "uses": "jellyfin"},
        "immich": {"connector": "immich", "url": "http://immich.test", "api_key": "k"},
        "openwebui": {"connector": "openwebui", "url": "http://openwebui.test", "api_key": "sk-k", "hostname": f"ai.{DOMAIN}"},
    },
}

NEXTCLOUD: dict[str, Any] = {
    "connector": "rest", "label": "Nextcloud", "url": "http://nextcloud.test", "login": "username",
    "auth": {"type": "basic", "username": "admin", "password": "secret"},
    "headers": {"OCS-APIRequest": "true"},
    "success": {"field": "ocs.meta.statuscode", "equals": [100, 200], "message": "ocs.meta.message"},
    "endpoints": {
        "health": {"method": "GET", "path": "/ocs/v1.php/cloud/capabilities?format=json", "version": "ocs.data.version.string"},
        "find": {"method": "GET", "path": "/ocs/v1.php/cloud/users/{username}?format=json", "id": "ocs.data.id"},
        "create": {"method": "POST", "path": "/ocs/v1.php/cloud/users?format=json", "id": "{username}",
                   "form": {"userid": "{username}", "password": "{password}", "email": "{email}", "displayName": "{name}"}},
        "set_password": {"method": "PUT", "path": "/ocs/v1.php/cloud/users/{id}?format=json",
                         "form": {"key": "password", "value": "{password}"}},
        "disable": {"method": "PUT", "path": "/ocs/v1.php/cloud/users/{id}/disable?format=json"},
        "enable": {"method": "PUT", "path": "/ocs/v1.php/cloud/users/{id}/enable?format=json"},
        "delete": {"method": "DELETE", "path": "/ocs/v1.php/cloud/users/{id}?format=json"},
    },
}


class FakeHomelab:
    def __init__(self) -> None:
        self.peers = [
            {"id": "PK-alex-phone", "name": "alex - phone", "allowed_ip": "10.8.0.2/32", "status": "running"},
            {"id": "PK-alex-laptop", "name": "alex - laptop", "allowed_ip": "10.8.0.3/32", "status": "stopped"},
            {"id": "PK-sam", "name": "sam - laptop", "allowed_ip": "10.8.0.4/32", "status": "stopped"},
            {"id": "PK-other", "name": "Media Server", "allowed_ip": "10.8.0.9/32", "status": "stopped"},
        ]
        names = ["jellyfin", "seerr", "immich", "ai", "radarr", "roster"]
        self.hosts = [{"id": i + 1, "domain_names": [f"{n}.{DOMAIN}"], "access_list_id": 0} for i, n in enumerate(names)]
        self.acls: list[dict] = []
        self.jf_users = [{"Id": "jf-admin", "Name": "Alex", "Policy": {"IsAdministrator": True, "IsDisabled": False}}]
        self.seerr_users: list[dict] = []
        self.immich_users: list[dict] = []
        self.owui_users: list[dict] = []
        self.nc_users: dict[str, dict] = {}
        # wg-easy clients mirror the WGDashboard peers; Traefik serves the same hosts as NPM.
        self.wge_clients = [{"id": i + 1, "name": p["name"], "ipv4Address": p["allowed_ip"].split("/")[0],
                             "enabled": True, "latestHandshakeAt": None} for i, p in enumerate(self.peers)]
        self.routers = [{"name": f"{n}@docker", "provider": "docker", "rule": f"Host(`{n}.{DOMAIN}`)", "middlewares": []}
                        for n in names]
        self.routers.append({"name": "api@internal", "provider": "internal", "rule": "PathPrefix(`/api`)"})
        self.seerr_settings: dict[str, dict] = {}
        # Mesh VPNs and Zoraxy
        self.hs_users: list[dict] = []
        self.hs_nodes: list[dict] = []
        self.hs_keys: list[dict] = []
        self.ts_devices: list[dict] = []
        self.ts_invites: list[dict] = []
        self.nb_groups: list[dict] = []
        self.nb_peers: list[dict] = []
        self.nb_keys: list[dict] = []
        self.zt_members: list[dict] = []
        self.zx_rules: list[dict] = []
        self.zx_whitelist: dict[str, list[str]] = {}
        self.zx_hosts = [{"RootOrMatchingDomain": f"{n}.{DOMAIN}", "AccessFilterUUID": "default"} for n in names]
        self.emby_users: list[dict] = []
        self.plex_shares: list[dict] = []
        self.abs_users: list[dict] = []
        self.nd_users: list[dict] = [{"id": "nd-admin", "userName": "admin", "isAdmin": True}]
        self.vw_users: list[dict] = []
        self.fail: set[str] = set()
        self.calls: list[str] = []

    def acl(self, site: str, prefix: str = "homeroster-") -> list[str]:
        acl = next(a for a in self.acls if a["name"] == prefix + site)
        return [c["address"] for c in acl["clients"]]

    def handler(self, req: httpx.Request) -> httpx.Response:
        self.calls.append(f"{req.method} {req.url.host}{req.url.path}")
        if req.url.host in self.fail:
            return httpx.Response(404, json={"message": "simulated outage"})
        body: Any = None
        if req.content:
            ctype = req.headers.get("content-type", "")
            body = json.loads(req.content) if "json" in ctype else dict(httpx.QueryParams(req.content.decode()))
        route = getattr(self, "_" + req.url.host.split(".")[0], None)
        response = route(req, req.url.path, req.method, body) if route else None
        return response or httpx.Response(404, json={"message": f"route not simulated: {req.method} {req.url.path}"})

    @staticmethod
    def j(data: Any, code: int = 200) -> httpx.Response:
        return httpx.Response(code, json=data)

    # ------------------------------------------------------------------ WGDashboard
    def _wg(self, req, path, m, body):
        if path == "/api/getDashboardVersion":
            return self.j({"status": True, "data": "v4.2.0"})
        if path == "/api/getWireguardConfigurationInfo":
            return self.j({"status": True, "data": {"configurationPeers": self.peers}})
        if path == "/api/addPeers/wg0":
            n = 10 + len(self.peers)
            self.peers.append({"id": f"PK-{n}", "name": body["name"], "allowed_ip": f"10.8.0.{n}/32", "status": "stopped"})
            return self.j({"status": True, "data": []})
        if path == "/api/downloadPeer/wg0":
            return self.j({"status": True, "data": {"fileName": "x", "file": f"[Interface]\nPrivateKey = test-{req.url.params['id']}\n"}})
        if path == "/api/deletePeers/wg0":
            self.peers = [p for p in self.peers if p["id"] not in body["peers"]]
            return self.j({"status": True, "data": None})
        return None

    # ------------------------------------------------------------------ wg-easy
    def _wgeasy(self, req, path, m, body):
        if path == "/api/information":
            return self.j({"currentRelease": "15.5.0"})
        if req.headers.get("authorization") != "Basic " + base64.b64encode(b"bot:test-password").decode():
            return self.j({"message": "Unauthorized"}, 401)
        if m != "GET" and req.headers.get("origin") != "https://vpn.example.com":
            return self.j({"message": "Invalid request origin"}, 403)
        if path == "/api/client" and m == "GET":
            page, size = int(req.url.params.get("page", 1)), int(req.url.params.get("pageSize", 25))
            return self.j({"total": len(self.wge_clients), "clients": self.wge_clients[(page - 1) * size:page * size]})
        if path == "/api/client" and m == "POST":
            n = max(c["id"] for c in self.wge_clients) + 1
            self.wge_clients.append({"id": n, "name": body["name"], "ipv4Address": f"10.8.0.{10 + n}",
                                     "enabled": True, "latestHandshakeAt": None})
            return self.j({"success": True, "clientId": n})
        match = re.fullmatch(r"/api/client/(\d+)(/configuration)?", path)
        if match and m == "GET" and match.group(2):
            return httpx.Response(200, text=f"[Interface]\nPrivateKey = test-{match.group(1)}\n",
                                  headers={"content-type": "application/octet-stream"})
        if match and m == "DELETE":
            self.wge_clients = [c for c in self.wge_clients if c["id"] != int(match.group(1))]
            return self.j({"success": True})
        return None

    # ------------------------------------------------------------------ dashboard-icons CDN (only Jellyfin's logo)
    def _cdn(self, req, path, m, body):
        if path.endswith("/png/jellyfin.png"):
            return httpx.Response(200, content=PNG, headers={"content-type": "image/png"})
        return None

    # ------------------------------------------------------------------ Traefik (read-only API)
    def _traefik(self, req, path, m, body):
        if path == "/api/version":
            return self.j({"Version": "3.5.0"})
        if path == "/api/http/routers":
            return self.j(self.routers)
        return None

    # ------------------------------------------------------------------ Headscale
    def _headscale(self, req, path, m, body):
        if req.headers.get("authorization") != "Bearer hs-key":
            return self.j({"message": "unauthorized"}, 401)
        if path == "/api/v1/user" and m == "GET":
            name = req.url.params.get("name")
            return self.j({"users": [u for u in self.hs_users if not name or u["name"] == name]})
        if path == "/api/v1/user" and m == "POST":
            user = {"id": str(len(self.hs_users) + 1), "name": body["name"]}
            self.hs_users.append(user)
            return self.j({"user": user})
        if path == "/api/v1/preauthkey" and m == "POST":
            key = {"id": str(len(self.hs_keys) + 1), "key": f"hskey-{len(self.hs_keys) + 1}", "user": body["user"], "used": False}
            self.hs_keys.append(key)
            return self.j({"preAuthKey": key})
        if path == "/api/v1/preauthkey/expire":
            return self.j({})
        if path == "/api/v1/node" and m == "GET":
            return self.j({"nodes": self.hs_nodes})
        if path.startswith("/api/v1/node/") and m == "DELETE":
            self.hs_nodes = [n for n in self.hs_nodes if n["id"] != path.rsplit("/", 1)[1]]
            return self.j({})
        return None

    def headscale_join(self, key: str, hostname: str, created: str) -> None:
        """A phone signs in with a pre-auth key."""
        found = next(k for k in self.hs_keys if k["key"] == key)
        user = next(u for u in self.hs_users if u["id"] == found["user"])
        self.hs_nodes.append({"id": str(100 + len(self.hs_nodes)), "givenName": hostname, "user": user,
                              "ipAddresses": [f"100.64.0.{10 + len(self.hs_nodes)}", "fd7a::1"], "online": True,
                              "createdAt": created})

    # ------------------------------------------------------------------ Tailscale
    def _tailscale(self, req, path, m, body):
        if req.headers.get("authorization") != "Bearer tskey-api-test":
            return self.j({"message": "unauthorized"}, 401)
        if path == "/api/v2/tailnet/-/devices":
            return self.j({"devices": self.ts_devices})
        if path == "/api/v2/tailnet/-/user-invites" and m == "POST":
            invite = {"id": str(len(self.ts_invites) + 1), "email": body[0]["email"],
                      "inviteUrl": f"https://login.tailscale.com/admin/invite/{len(self.ts_invites) + 1}"}
            self.ts_invites.append(invite)
            return self.j([invite])
        if path.startswith("/api/v2/user-invites/") and m == "DELETE":
            return self.j({})
        if path.startswith("/api/v2/device/") and m == "DELETE":
            self.ts_devices = [d for d in self.ts_devices if d["id"] != path.rsplit("/", 1)[1]]
            return self.j({})
        return None

    # ------------------------------------------------------------------ NetBird
    def _netbird(self, req, path, m, body):
        if req.headers.get("authorization") != "Token nbp_test":
            return self.j({"message": "unauthorized"}, 401)
        if path == "/api/groups" and m == "GET":
            return self.j(self.nb_groups)
        if path == "/api/groups" and m == "POST":
            group = {"id": f"g{len(self.nb_groups) + 1}", "name": body["name"]}
            self.nb_groups.append(group)
            return self.j(group)
        if path == "/api/setup-keys" and m == "POST":
            key = {"id": str(len(self.nb_keys) + 1), "key": f"NB-KEY-{len(self.nb_keys) + 1}", **body}
            self.nb_keys.append(key)
            return self.j(key)
        if path == "/api/peers":
            return self.j(self.nb_peers)
        if path.startswith("/api/peers/") and m == "DELETE":
            self.nb_peers = [p for p in self.nb_peers if p["id"] != path.rsplit("/", 1)[1]]
            return self.j({})
        return None

    # ------------------------------------------------------------------ ZeroTier Central
    def _zerotier(self, req, path, m, body):
        if req.headers.get("authorization") != "token zt-token":
            return self.j({"message": "unauthorized"}, 401)
        if path == "/api/v1/network/abcd1234":
            return self.j({"id": "abcd1234", "config": {"name": "home"}})
        if path == "/api/v1/network/abcd1234/member" and m == "GET":
            return self.j(self.zt_members)
        match = re.fullmatch(r"/api/v1/network/abcd1234/member/(\w+)", path)
        if match and m == "POST":
            member = next(x for x in self.zt_members if x["nodeId"] == match.group(1))
            member.update(name=body["name"], description=body["description"])
            member["config"]["authorized"] = body["config"]["authorized"]
            return self.j(member)
        if match and m == "DELETE":
            self.zt_members = [x for x in self.zt_members if x["nodeId"] != match.group(1)]
            return self.j({})
        return None

    # ------------------------------------------------------------------ Zoraxy
    def _zoraxy(self, req, path, m, body):
        if path == "/login.html":
            return httpx.Response(200, text='<meta name="zoraxy.csrf.Token" content="csrf-123">',
                                  headers={"set-cookie": "zoraxy_csrf=1; Path=/"})
        if m == "POST" and req.headers.get("x-csrf-token") != "csrf-123":
            return httpx.Response(403, text="Forbidden - CSRF token invalid")
        if path == "/api/auth/login":
            if body != {"username": "bot", "password": "zx-pass"}:
                return self.j({"error": "Invalid username or password"})
            return httpx.Response(200, json="OK", headers={"set-cookie": "Zoraxy=session; Path=/"})
        if "zoraxy=session" not in req.headers.get("cookie", "").lower():
            return httpx.Response(401, text="Unauthorized")
        if path == "/api/proxy/list":
            return self.j(self.zx_hosts)
        if path == "/api/access/list":
            return self.j([{**r, "WhitelistEnabled": r.get("WhitelistEnabled", False)} for r in self.zx_rules])
        if path == "/api/access/create":
            self.zx_rules.append({"ID": f"rule-{len(self.zx_rules) + 1}", "Name": body["name"]})
            return self.j("OK")
        if path == "/api/whitelist/list":
            return self.j([{"IP": ip} for ip in self.zx_whitelist.get(req.url.params["id"], [])])
        if path == "/api/whitelist/ip/add":
            self.zx_whitelist.setdefault(body["id"], []).append(body["ip"])
            return self.j("OK")
        if path == "/api/whitelist/ip/remove":
            self.zx_whitelist[body["id"]].remove(body["ip"])
            return self.j("OK")
        if path == "/api/whitelist/enable":
            next(r for r in self.zx_rules if r["ID"] == body["id"])["WhitelistEnabled"] = True
            return self.j("OK")
        if path == "/api/access/attach":
            next(h for h in self.zx_hosts if h["RootOrMatchingDomain"] == body["host"])["AccessFilterUUID"] = body["id"]
            return self.j("OK")
        return None

    # ------------------------------------------------------------------ Emby
    def _emby(self, req, path, m, body):
        if req.headers.get("x-emby-token") != "k":
            return self.j({}, 401)
        if path == "/System/Info/Public":
            return self.j({"Version": "4.9.0"})
        if path == "/Users" and m == "GET":
            return self.j(self.emby_users)
        if path == "/Users/New":
            user = {"Id": f"em-{len(self.emby_users) + 1}", "Name": body["Name"], "Policy": {"IsDisabled": False}}
            self.emby_users.append(user)
            return self.j(user)
        match = re.fullmatch(r"/Users/([^/]+)(/Password|/Policy)?", path)
        if match:
            user = next(u for u in self.emby_users if u["Id"] == match.group(1))
            if match.group(2) == "/Password":
                user["password"] = body["NewPw"]
                return httpx.Response(204)
            if match.group(2) == "/Policy":
                user["Policy"] = body
                return httpx.Response(204)
            if m == "GET":
                return self.j(user)
            if m == "DELETE":
                self.emby_users.remove(user)
                return httpx.Response(204)
        return None

    # ------------------------------------------------------------------ Plex (server + plex.tv)
    def _plex(self, req, path, m, body):
        if req.headers.get("x-plex-token") != "plex-token":
            return self.j({}, 401)
        if path == "/identity":
            return self.j({"MediaContainer": {"machineIdentifier": "machine-1", "version": "1.41"}})
        if path == "/api/servers/machine-1":
            return httpx.Response(200, text='<MediaContainer><Server><Section id="11" title="Movies"/>'
                                            '<Section id="12" title="Shows"/></Server></MediaContainer>',
                                  headers={"content-type": "application/xml"})
        if path == "/api/servers/machine-1/shared_servers" and m == "GET":
            items = "".join(f'<SharedServer id="{x["id"]}" email="{x["email"]}"/>' for x in self.plex_shares)
            return httpx.Response(200, text=f"<MediaContainer>{items}</MediaContainer>",
                                  headers={"content-type": "application/xml"})
        if path == "/api/servers/machine-1/shared_servers" and m == "POST":
            self.plex_shares.append({"id": str(100 + len(self.plex_shares)), "email": body["shared_server"]["invited_email"],
                                     "sections": body["shared_server"]["library_section_ids"]})
            return httpx.Response(201, text="<MediaContainer/>", headers={"content-type": "application/xml"})
        if path.startswith("/api/servers/machine-1/shared_servers/") and m == "DELETE":
            self.plex_shares = [x for x in self.plex_shares if x["id"] != path.rsplit("/", 1)[1]]
            return httpx.Response(200, text="")
        return None

    # ------------------------------------------------------------------ Audiobookshelf
    def _audiobookshelf(self, req, path, m, body):
        if path == "/status":
            return self.j({"serverVersion": "2.20.0"})
        if req.headers.get("authorization") != "Bearer abs-key":
            return self.j({}, 401)
        if path == "/api/users" and m == "GET":
            return self.j({"users": self.abs_users})
        if path == "/api/users" and m == "POST":
            user = {"id": f"abs-{len(self.abs_users) + 1}", **body}
            self.abs_users.append(user)
            return self.j({"user": user})
        user = next((u for u in self.abs_users if path == f"/api/users/{u['id']}"), None)
        if user and m == "PATCH":
            user.update(body)
            return self.j({"user": user})
        if user and m == "DELETE":
            self.abs_users.remove(user)
            return self.j({"success": True})
        return None

    # ------------------------------------------------------------------ Navidrome
    def _navidrome(self, req, path, m, body):
        if path == "/auth/login":
            ok = body == {"username": "admin", "password": "nd-pass"}
            return self.j({"token": "nd-jwt"} if ok else {"error": "Invalid"}, 200 if ok else 401)
        if req.headers.get("x-nd-authorization") != "Bearer nd-jwt":
            return self.j({}, 401)
        if path == "/api/user" and m == "GET":
            return self.j(self.nd_users)
        if path == "/api/user" and m == "POST":
            user = {"id": f"nd-{len(self.nd_users)}", **body}
            self.nd_users.append(user)
            return self.j(user)
        user = next((u for u in self.nd_users if path == f"/api/user/{u['id']}"), None)
        if user and m == "GET":
            return self.j(user)
        if user and m == "PUT":
            user.update(body)
            return self.j(user)
        if user and m == "DELETE":
            self.nd_users.remove(user)
            return self.j({})
        return None

    # ------------------------------------------------------------------ Vaultwarden admin
    def _vaultwarden(self, req, path, m, body):
        if path == "/admin" and m == "POST":
            if body != {"token": "vw-admin-token"}:
                return httpx.Response(200, text="<html>login</html>")
            return httpx.Response(303, headers={"set-cookie": "VW_ADMIN=jwt-1; Path=/admin; Secure; HttpOnly"})
        if req.headers.get("cookie") != "VW_ADMIN=jwt-1":
            return self.j({"message": "Unauthorized"}, 401)
        if path == "/admin/users":
            return self.j(self.vw_users)
        if path.startswith("/admin/users/by-mail/"):
            user = next((u for u in self.vw_users if u["email"] == path.rsplit("/", 1)[1]), None)
            return self.j(user) if user else self.j({"message": "User doesn't exist"}, 404)
        if path == "/admin/invite":
            user = {"id": f"vw-{len(self.vw_users) + 1}", "email": body["email"], "userEnabled": True}
            self.vw_users.append(user)
            return self.j(user)
        match = re.fullmatch(r"/admin/users/([^/]+)/(enable|disable|delete)", path)
        if match:
            user = next(u for u in self.vw_users if u["id"] == match.group(1))
            if match.group(2) == "delete":
                self.vw_users.remove(user)
            else:
                user["userEnabled"] = match.group(2) == "enable"
            return httpx.Response(200)
        return None

    # ------------------------------------------------------------------ Home Assistant (REST part)
    def _homeassistant(self, req, path, m, body):
        if req.headers.get("authorization") != "Bearer ha-token":
            return self.j({}, 401)
        if path == "/api/config":
            return self.j({"version": "2026.11.2"})
        return None
    # ------------------------------------------------------------------ Nginx Proxy Manager
    def _npm(self, req, path, m, body):
        if path == "/api/tokens":
            return self.j({"token": "tok"})
        if path == "/api/":
            return self.j({"status": "OK", "version": {"major": 2, "minor": 12, "revision": 3}})
        if req.headers.get("authorization") != "Bearer tok":
            return self.j({"error": {"code": 403, "message": "Permission Denied"}}, 403)
        if path == "/api/nginx/proxy-hosts" and m == "GET":
            return self.j(self.hosts)
        if path.startswith("/api/nginx/proxy-hosts/") and m == "PUT":
            host = next(h for h in self.hosts if h["id"] == int(path.rsplit("/", 1)[1]))
            host["access_list_id"] = body["access_list_id"]
            return self.j(host)
        if path == "/api/nginx/access-lists" and m == "GET":
            return self.j(self.acls)
        if path == "/api/nginx/access-lists" and m == "POST":
            acl = {"id": len(self.acls) + 1, **body}
            self.acls.append(acl)
            return self.j(acl, 201)
        if path.startswith("/api/nginx/access-lists/") and m == "PUT":
            acl = next(a for a in self.acls if a["id"] == int(path.rsplit("/", 1)[1]))
            acl.update(body)
            return self.j(acl)
        return None

    # ------------------------------------------------------------------ Jellyfin
    def _jellyfin(self, req, path, m, body):
        if path == "/System/Info/Public":
            return self.j({"Version": "10.10.7"})
        if path == "/Users" and m == "GET":
            return self.j(self.jf_users)
        if path == "/Users/New":
            user = {"Id": f"jf-{len(self.jf_users)}", "Name": body["Name"], "Policy": {"IsAdministrator": False, "IsDisabled": False}}
            self.jf_users.append(user)
            return self.j(user)
        if path == "/Users/Configuration" and m == "POST":
            next(u for u in self.jf_users if u["Id"] == req.url.params["userId"])["Configuration"] = body
            return httpx.Response(204)
        if path == "/Users/Password":
            return httpx.Response(204)
        match = re.fullmatch(r"/Users/([^/]+)(/Policy)?", path)
        if match:
            user = next((u for u in self.jf_users if u["Id"] == match.group(1)), None)
            if m == "GET":
                return self.j(user)
            if m == "POST" and match.group(2):
                user["Policy"] = body
                return httpx.Response(204)
            if m == "DELETE":
                self.jf_users.remove(user)
                return httpx.Response(204)
        return None

    # ------------------------------------------------------------------ Seerr
    def _seerr(self, req, path, m, body):
        if path == "/api/v1/status":
            return self.j({"version": "2.7.3"})
        if path == "/api/v1/user":
            return self.j({"results": self.seerr_users})
        if path.startswith("/api/v1/user/jellyfin/"):
            user = next((u for u in self.seerr_users if u["jf"] == path.rsplit("/", 1)[1]), None)
            return self.j(user) if user else self.j({"message": "not found"}, 404)
        if path == "/api/v1/user/import-from-jellyfin":
            user = {"id": len(self.seerr_users) + 1, "jf": body["jellyfinUserIds"][0], "email": ""}
            self.seerr_users.append(user)
            return self.j([user], 201)
        if path.endswith("/settings/main"):
            settings = self.seerr_settings.setdefault(path.split("/")[4], {"username": "x", "locale": ""})
            if m == "POST":
                settings.clear()
                settings.update(body)  # Seerr replaces every main setting
            return self.j(settings)
        if m == "DELETE":
            self.seerr_users = [u for u in self.seerr_users if str(u["id"]) != path.rsplit("/", 1)[1]]
            return self.j({})
        return None

    # ------------------------------------------------------------------ Immich
    def _immich(self, req, path, m, body):
        if path == "/api/server/version":
            return self.j({"major": 1, "minor": 132, "patch": 3, "prerelease": None})
        if path == "/api/admin/users" and m == "GET":
            return self.j(self.immich_users)
        if path == "/api/admin/users" and m == "POST":
            user = {"id": f"im-{len(self.immich_users)}", "email": body["email"]}
            self.immich_users.append(user)
            return self.j(user, 201)
        if m in ("PUT", "DELETE"):
            return self.j({})
        return None

    # ------------------------------------------------------------------ Open WebUI
    def _openwebui(self, req, path, m, body):
        if path == "/api/version":
            return self.j({"version": "0.6.5"})
        if path == "/api/v1/users/all":
            return self.j({"users": self.owui_users, "total": len(self.owui_users)})
        if path == "/api/v1/auths/add":
            user = {"id": f"ow-{len(self.owui_users)}", "email": body["email"], "role": body["role"]}
            self.owui_users.append(user)
            return self.j(user)
        if path.endswith("/update"):
            user = next(u for u in self.owui_users if u["id"] == path.split("/")[-2])
            user.update(body)
            return self.j(True)
        if m == "DELETE":
            return self.j(True)
        return None

    # ------------------------------------------------------------------ Nextcloud (declarative REST connector)
    def _nextcloud(self, req, path, m, body):
        if req.headers.get("authorization") != "Basic YWRtaW46c2VjcmV0" or req.headers.get("ocs-apirequest") != "true":
            return self.j({"message": "unauthorised"}, 401)
        ok = lambda data=None: self.j({"ocs": {"meta": {"statuscode": 100, "status": "ok"}, "data": data or {}}})
        if path == "/ocs/v1.php/cloud/capabilities":
            return ok({"version": {"string": "30.0.4"}})
        if path == "/ocs/v1.php/cloud/users" and m == "POST":
            if body["userid"] in self.nc_users:
                return self.j({"ocs": {"meta": {"statuscode": 102, "message": "User already exists"}, "data": []}})
            self.nc_users[body["userid"]] = {**body, "enabled": True}
            return ok({"id": body["userid"]})
        match = re.fullmatch(r"/ocs/v1\.php/cloud/users/([^/]+)(/(enable|disable))?", path)
        if match:
            user = self.nc_users.get(match.group(1))
            if user is None:
                return self.j({"ocs": {"meta": {"statuscode": 404}, "data": []}}, 404)
            if m == "GET":
                return ok({"id": match.group(1), "email": user["email"]})
            if m == "PUT" and match.group(3):
                user["enabled"] = match.group(3) == "enable"
                return ok()
            if m == "PUT":
                user[body["key"]] = body["value"]
                return ok()
            if m == "DELETE":
                del self.nc_users[match.group(1)]
                return ok()
        return None


class FakeHomeAssistantSocket:
    """The commands of Home Assistant's WebSocket API that HomeRoster uses."""

    def __init__(self) -> None:
        self.users: list[dict] = [{"id": "ha-owner", "name": "Owner", "username": "owner", "is_active": True}]
        self.closed = 0

    async def connect(self, url: str, token: str):
        assert token == "ha-token"
        socket = self

        class Connection:
            async def close(self):
                socket.closed += 1

        return Connection(), self.command

    async def command(self, p: dict):
        kind = p["type"]
        if kind == "config/auth/list":
            return self.users
        if kind == "config/auth/create":
            user = {"id": f"ha-{len(self.users)}", "name": p["name"], "username": None, "is_active": True,
                    "group_ids": p["group_ids"], "local_only": p["local_only"]}
            self.users.append(user)
            return {"user": user}
        user = next(u for u in self.users if u["id"] == p["user_id"])
        if kind == "config/auth_provider/homeassistant/create":
            user.update(username=p["username"], password=p["password"])
        elif kind == "config/auth_provider/homeassistant/admin_change_password":
            user["password"] = p["password"]
        elif kind == "config/auth/update":
            user["is_active"] = p["is_active"]
        elif kind == "config/auth/delete":
            self.users.remove(user)
        return None
