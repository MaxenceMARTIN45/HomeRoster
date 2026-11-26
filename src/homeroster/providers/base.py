"""Shared building blocks for every provider: readable errors, health reports and an HTTP client.

Each service has its own module. When an update breaks one app's API, only that module fails: the
Status page shows it in red with the reason, and the rest of HomeRoster keeps working.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

from ..i18n import t

# The tests replace this with an ``httpx.MockTransport`` that simulates every service.
TRANSPORT: httpx.AsyncBaseTransport | None = None
TIMEOUT = httpx.Timeout(20.0, connect=5.0)


class ServiceError(Exception):
    """An error raised by a service, carrying a translation key so it can be shown as is in the UI."""

    def __init__(self, service: str, key: str, **params: Any):
        self.service = service
        self.key = key
        self.params = params
        super().__init__(f"{service}: {key}")

    @property
    def message(self) -> str:
        return t(self.key, **self.params)

    def __str__(self) -> str:
        return f"{self.service}: {self.message}"


class NotConfigured(ServiceError):
    def __init__(self, service: str):
        super().__init__(service, "errors.not_configured")


@dataclass
class Health:
    ok: bool
    message: str
    version: str = ""

    @classmethod
    def from_error(cls, error: ServiceError) -> Health:
        return cls(ok=False, message=error.message)


def error_message(error: Exception) -> str:
    """Readable message for any exception (the UI never shows a raw traceback)."""
    if isinstance(error, ServiceError):
        return error.message
    return t("errors.unexpected", kind=error.__class__.__name__)


class HttpService:
    """A small JSON-over-HTTP client with consistent, translated error messages."""

    def __init__(self, label: str, base_url: str, *, headers: dict[str, str] | None = None,
                 auth: httpx.Auth | tuple[str, str] | None = None):
        self.label = label
        self.base_url = (base_url or "").rstrip("/")
        self.headers = headers or {}
        self.auth = auth

    def client(self, extra_headers: dict[str, str] | None = None) -> httpx.AsyncClient:
        return httpx.AsyncClient(base_url=self.base_url, timeout=TIMEOUT, transport=TRANSPORT, auth=self.auth,
                                 headers={"Accept": "application/json", **self.headers, **(extra_headers or {})})

    async def request(self, method: str, path: str, *, expect: tuple[int, ...] = (200,), json: Any = None,
                      params: dict | None = None, data: Any = None, headers: dict[str, str] | None = None) -> Any:
        if not self.base_url:
            raise NotConfigured(self.label)
        try:
            async with self.client(headers) as client:
                response = await client.request(method, path, json=json, params=params, data=data)
        except httpx.ConnectError as e:
            raise ServiceError(self.label, "errors.unreachable") from e
        except httpx.TimeoutException as e:
            raise ServiceError(self.label, "errors.timeout") from e
        except httpx.HTTPError as e:
            raise ServiceError(self.label, "errors.network", kind=e.__class__.__name__) from e
        if response.status_code not in expect:
            raise ServiceError(self.label, "errors.http", detail=self.explain(response))
        if not response.content:
            return None
        if "json" in response.headers.get("content-type", ""):
            return response.json()
        return response.text

    @staticmethod
    def explain(response: httpx.Response) -> str:
        detail = ""
        try:
            body = response.json()
            if isinstance(body, dict):
                err = body.get("error")
                if isinstance(err, dict):
                    err = err.get("message")
                detail = str(body.get("message") or body.get("detail") or err or "")
        except ValueError:
            detail = (response.text or "")[:160]
        hint_key = {401: "errors.hint_401", 403: "errors.hint_403", 404: "errors.hint_404"}.get(response.status_code)
        parts = [f"HTTP {response.status_code}", t(hint_key) if hint_key else "", detail]
        return " · ".join(p for p in parts if p)
