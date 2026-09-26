"""Shared HTTP plumbing and error taxonomy for the communication integrations.

Every REST integration (Gmail, Graph, Telegram, Discord, WhatsApp) maps
provider responses onto the same small set of typed errors:

* ``IntegrationAuthError`` (a ``CapabilityUnavailable``) for 401/403 and
  revoked/expired credentials - the agent gets a clean "run `scar auth ...`"
  prerequisite instead of a stack trace.
* ``RateLimited`` (a ``ToolError``) for 429 after at most one short automatic
  retry that honours ``Retry-After``.
* ``ProviderError`` (a ``ToolError``) for every other non-success response.
"""

from __future__ import annotations

import asyncio
import email.utils
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol

import httpx

from scar.core.errors import CapabilityUnavailable, ToolError

DEFAULT_TIMEOUT = httpx.Timeout(30.0, connect=10.0)
MAX_AUTO_RETRY_AFTER = 10.0  # seconds we are willing to wait inline before surfacing RateLimited
USER_AGENT = "SCAR/1.0 (+https://github.com/scar-operator/scar)"


class IntegrationAuthError(CapabilityUnavailable):
    """Credentials missing, expired, revoked or lacking a scope."""

    def __init__(self, prerequisite: str, setup_doc: str, detail: str = "", status_code: int | None = None) -> None:
        super().__init__(prerequisite, setup_doc, detail)
        self.status_code = status_code


class RateLimited(ToolError):
    def __init__(self, service: str, retry_after: float | None, detail: str = "") -> None:
        self.service = service
        self.retry_after = retry_after
        wait = f"; retry after {retry_after:.0f}s" if retry_after is not None else ""
        super().__init__(f"{service} rate limit reached{wait}{': ' + detail if detail else ''}", "RateLimited")


class ProviderError(ToolError):
    def __init__(self, service: str, status_code: int, detail: str, error_type: str = "ProviderError") -> None:
        self.service = service
        self.status_code = status_code
        self.detail = detail
        super().__init__(f"{service} error {status_code}: {detail}", error_type)


def parse_retry_after(value: str | None, body: Any = None) -> float | None:
    """Seconds from a Retry-After header (delta-seconds or HTTP date) or a JSON ``retry_after`` field."""
    if value:
        value = value.strip()
        try:
            return max(0.0, float(value))
        except ValueError:
            try:
                when = email.utils.parsedate_to_datetime(value)
            except (TypeError, ValueError):
                when = None
            if when is not None:
                return max(0.0, when.timestamp() - time.time())
    if isinstance(body, Mapping):
        for key in ("retry_after",):
            raw = body.get(key)
            if isinstance(raw, int | float):
                return float(raw)
        params = body.get("parameters")
        if isinstance(params, Mapping) and isinstance(params.get("retry_after"), int | float):
            return float(params["retry_after"])
    return None


def error_detail(resp: httpx.Response) -> str:
    """Best-effort human message from a provider error body (never the raw request)."""
    try:
        body = resp.json()
    except ValueError:
        return resp.text[:300]
    if isinstance(body, Mapping):
        err = body.get("error")
        if isinstance(err, Mapping):
            msg = err.get("message") or err.get("error_user_msg") or err.get("code")
            return str(msg)[:300]
        if isinstance(err, str):
            desc = body.get("error_description") or body.get("message") or ""
            return f"{err}: {desc}"[:300] if desc else err[:300]
        for key in ("description", "message"):
            if body.get(key):
                return str(body[key])[:300]
    return resp.text[:300]


def safe_json(resp: httpx.Response) -> Any:
    try:
        return resp.json()
    except ValueError:
        return None


class TokenSource(Protocol):
    async def access_token(self) -> str: ...

    def invalidate(self) -> None: ...


@dataclass
class ServiceInfo:
    """How to describe a service in errors."""

    name: str  # "GMAIL", "Microsoft Graph", "Telegram Bot API"
    setup_doc: str
    reauth_hint: str  # e.g. "run `scar auth google`"


class ApiClient:
    """Small authenticated JSON client used by the REST integrations.

    ``tokens`` is a ``TokenSource`` (OAuth bearer tokens, refreshed and retried
    once on 401); ``auth_header`` is a callable returning the full
    Authorization header value (static bot tokens). Neither means the URL
    itself carries the credential (Telegram Bot API).
    """

    def __init__(
        self,
        info: ServiceInfo,
        *,
        tokens: TokenSource | None = None,
        auth_header: Callable[[], str] | None = None,
        http: httpx.AsyncClient | None = None,
        base_url: str = "",
        default_headers: Mapping[str, str] | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.info = info
        self._tokens = tokens
        self._auth_header_fn = auth_header
        self._http = http
        self.base_url = base_url.rstrip("/")
        self.default_headers = {"User-Agent": USER_AGENT, **(default_headers or {})}
        self._sleep = sleep

    def _client(self) -> tuple[httpx.AsyncClient, bool]:
        if self._http is not None:
            return self._http, False
        return httpx.AsyncClient(timeout=DEFAULT_TIMEOUT), True

    async def _auth_header(self) -> dict[str, str]:
        if self._tokens is not None:
            return {"Authorization": f"Bearer {await self._tokens.access_token()}"}
        if self._auth_header_fn is not None:
            return {"Authorization": self._auth_header_fn()}
        return {}

    def _url(self, path_or_url: str) -> str:
        if path_or_url.startswith(("http://", "https://")):
            return path_or_url
        return f"{self.base_url}/{path_or_url.lstrip('/')}"

    async def request(
        self,
        method: str,
        path_or_url: str,
        *,
        params: Any = None,
        json: Any = None,
        data: Any = None,
        headers: Mapping[str, str] | None = None,
        expected: tuple[int, ...] = (200, 201, 202, 204),
    ) -> httpx.Response:
        client, owned = self._client()
        try:
            auth_retried = False
            rate_retried = False
            while True:
                hdrs = {**self.default_headers, **(await self._auth_header()), **(headers or {})}
                try:
                    resp = await client.request(method, self._url(path_or_url), params=params, json=json, data=data, headers=hdrs)
                except httpx.TimeoutException as exc:
                    raise ToolError(f"{self.info.name} timed out: {exc}", "Timeout") from exc
                except httpx.TransportError as exc:
                    raise ToolError(f"{self.info.name} is unreachable: {exc}", "NetworkError") from exc
                if resp.status_code in expected:
                    return resp
                if resp.status_code == 401 and not auth_retried and self._tokens is not None:
                    auth_retried = True
                    self._tokens.invalidate()
                    continue
                if resp.status_code == 429:
                    wait = parse_retry_after(resp.headers.get("Retry-After"), safe_json(resp))
                    if not rate_retried and wait is not None and wait <= MAX_AUTO_RETRY_AFTER:
                        rate_retried = True
                        await self._sleep(wait)
                        continue
                    raise RateLimited(self.info.name, wait, error_detail(resp))
                self.raise_for(resp)
        finally:
            if owned:
                await client.aclose()

    def raise_for(self, resp: httpx.Response) -> None:
        detail = error_detail(resp)
        if resp.status_code == 401:
            raise IntegrationAuthError(
                f"{self.info.name} credentials were rejected: {self.info.reauth_hint}",
                self.info.setup_doc,
                detail,
                resp.status_code,
            )
        if resp.status_code == 403:
            raise IntegrationAuthError(
                f"{self.info.name} denied access (missing permission/scope): {self.info.reauth_hint}",
                self.info.setup_doc,
                detail,
                resp.status_code,
            )
        if resp.status_code == 404:
            raise ProviderError(self.info.name, 404, detail, "NotFound")
        raise ProviderError(self.info.name, resp.status_code, detail)

    async def get_json(self, path: str, **kw: Any) -> Any:
        resp = await self.request("GET", path, **kw)
        return safe_json(resp)

    async def post_json(self, path: str, body: Any = None, **kw: Any) -> Any:
        resp = await self.request("POST", path, json=body, **kw)
        return safe_json(resp)
