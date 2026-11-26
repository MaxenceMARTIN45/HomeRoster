"""An in-memory homelab: fake WGDashboard, Nginx Proxy Manager, Jellyfin, Seerr, Immich, Open WebUI and Nextcloud.

Served through ``httpx.MockTransport``, so the tests (and the local demo) never touch the network.
"""

from __future__ import annotations

import json
import re
from typing import Any

import httpx

DOMAIN = "example.com"

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
            return self.j({})
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
