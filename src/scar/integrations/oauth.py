"""Generic OAuth 2.0 installed-app flow (authorization code + PKCE, loopback redirect).

Used by Google and Microsoft. The flow:

1. Start a one-shot HTTP server on ``127.0.0.1`` with a random free port.
2. Open the system browser at the provider's authorization endpoint with a
   PKCE S256 challenge, a random ``state`` and ``redirect_uri`` pointing at the
   loopback server.
3. The provider redirects back with ``code`` (and ``state``); the server
   validates ``state`` and hands the code to the flow.
4. Exchange the code (plus the PKCE verifier) for tokens with httpx.

Refresh tokens are stored **only** in the Windows Credential Manager through
``SecretStore.set`` - never in plain files. Access tokens live in memory.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import http.server
import secrets as pysecrets
import threading
import time
import webbrowser
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse

import httpx

from scar.core.errors import CapabilityUnavailable, ToolError
from scar.integrations.common import DEFAULT_TIMEOUT, IntegrationAuthError, error_detail, safe_json

REFRESH_MARGIN_S = 60.0


@dataclass(frozen=True)
class OAuthClientConfig:
    provider: str  # label used in messages, e.g. "Google"
    client_id: str
    auth_url: str
    token_url: str
    scopes: tuple[str, ...]
    refresh_secret_name: str  # keyring entry holding the refresh token
    setup_doc: str
    login_command: str  # e.g. "scar auth google"
    client_secret: str | None = None  # Google "Desktop app" clients ship a non-confidential secret
    extra_auth_params: Mapping[str, str] = field(default_factory=dict)
    redirect_host: str = "127.0.0.1"  # host written into redirect_uri (server always binds 127.0.0.1)
    refresh_expiry_hint: str = ""  # appended to "refresh token rejected" errors
    refresh_with_scope: bool = False  # Microsoft identity platform expects `scope` on refresh; Google does not


@dataclass
class OAuthTokens:
    access_token: str
    expires_at: float
    refresh_token: str | None = None
    scope: str = ""
    token_type: str = "Bearer"

    @classmethod
    def from_response(cls, body: Mapping[str, Any]) -> OAuthTokens:
        access = body.get("access_token")
        if not isinstance(access, str) or not access:
            raise ToolError("token endpoint returned no access_token", "AuthError")
        expires_in = float(body.get("expires_in") or 3600)
        refresh = body.get("refresh_token")
        return cls(
            access_token=access,
            expires_at=time.time() + expires_in,
            refresh_token=refresh if isinstance(refresh, str) and refresh else None,
            scope=str(body.get("scope") or ""),
            token_type=str(body.get("token_type") or "Bearer"),
        )


# ------------------------------------------------------------------ PKCE
def make_pkce_pair() -> tuple[str, str]:
    """(verifier, S256 challenge) per RFC 7636."""
    verifier = pysecrets.token_urlsafe(64)[:128]
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


# ------------------------------------------------------------------ loopback server
@dataclass
class _CallbackResult:
    params: dict[str, str] = field(default_factory=dict)
    event: threading.Event = field(default_factory=threading.Event)


_SUCCESS_PAGE = (
    b"<html><head><title>SCAR</title></head><body style='font-family:sans-serif'>"
    b"<h2>SCAR is signed in.</h2><p>You can close this tab and return to SCAR.</p></body></html>"
)
_ERROR_PAGE = (
    b"<html><head><title>SCAR</title></head><body style='font-family:sans-serif'>"
    b"<h2>Sign-in did not complete.</h2><p>Return to SCAR for details.</p></body></html>"
)


class LoopbackServer:
    """One-shot redirect receiver bound to 127.0.0.1 on a random free port."""

    def __init__(self, expected_state: str) -> None:
        self.expected_state = expected_state
        self.result = _CallbackResult()
        outer = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                query = parse_qs(urlparse(self.path).query)
                params = {k: v[0] for k, v in query.items() if v}
                if "code" not in params and "error" not in params:
                    self.send_response(404)
                    self.end_headers()
                    return
                ok = "code" in params and params.get("state") == outer.expected_state
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(_SUCCESS_PAGE if ok else _ERROR_PAGE)
                if not outer.result.event.is_set():
                    outer.result.params = params
                    outer.result.event.set()

            def log_message(self, format: str, *args: Any) -> None:  # silence stderr access logs
                return

        self._server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, name="scar-oauth-loopback", daemon=True)

    @property
    def port(self) -> int:
        return int(self._server.server_address[1])

    def start(self) -> None:
        self._thread.start()

    def wait(self, timeout: float) -> dict[str, str] | None:
        if not self.result.event.wait(timeout):
            return None
        return self.result.params

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()


# ------------------------------------------------------------------ flow
def build_authorize_url(cfg: OAuthClientConfig, redirect_uri: str, state: str, challenge: str) -> str:
    params = {
        "client_id": cfg.client_id,
        "response_type": "code",
        "redirect_uri": redirect_uri,
        "scope": " ".join(cfg.scopes),
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        **dict(cfg.extra_auth_params),
    }
    return f"{cfg.auth_url}?{urlencode(params)}"


async def _token_request(cfg: OAuthClientConfig, form: dict[str, str], http: httpx.AsyncClient | None) -> dict[str, Any]:
    if cfg.client_secret:
        form = {**form, "client_secret": cfg.client_secret}
    client = http or httpx.AsyncClient(timeout=DEFAULT_TIMEOUT)
    try:
        try:
            resp = await client.post(cfg.token_url, data=form, headers={"Accept": "application/json"})
        except httpx.TransportError as exc:
            raise ToolError(f"{cfg.provider} token endpoint unreachable: {exc}", "NetworkError") from exc
    finally:
        if http is None:
            await client.aclose()
    body = safe_json(resp)
    if resp.status_code != 200 or not isinstance(body, dict):
        detail = error_detail(resp)
        code = body.get("error") if isinstance(body, dict) else None
        if code in ("invalid_grant", "invalid_client", "unauthorized_client", "interaction_required"):
            hint = f" {cfg.refresh_expiry_hint}" if code == "invalid_grant" and cfg.refresh_expiry_hint else ""
            raise IntegrationAuthError(
                f"{cfg.provider} sign-in expired or was revoked: run `{cfg.login_command}`.{hint}",
                cfg.setup_doc,
                detail,
                resp.status_code,
            )
        raise ToolError(f"{cfg.provider} token request failed ({resp.status_code}): {detail}", "AuthError")
    return body


async def run_authorization_flow(
    cfg: OAuthClientConfig,
    secret_store: Any,
    *,
    open_browser: Callable[[str], Any] = webbrowser.open,
    timeout: float = 300.0,
    http: httpx.AsyncClient | None = None,
    announce: Callable[[str], None] | None = None,
) -> OAuthTokens:
    """Interactive login. Stores the refresh token in the credential manager and returns the tokens."""
    if not cfg.client_id:
        raise CapabilityUnavailable(f"{cfg.provider} OAuth client id is not configured", cfg.setup_doc)
    verifier, challenge = make_pkce_pair()
    state = pysecrets.token_urlsafe(24)
    server = LoopbackServer(state)
    server.start()
    try:
        redirect_uri = f"http://{cfg.redirect_host}:{server.port}/"
        url = build_authorize_url(cfg, redirect_uri, state, challenge)
        if announce is not None:
            announce(f"Opening your browser to sign in to {cfg.provider}. If it does not open, visit:\n{url}")
        await asyncio.to_thread(open_browser, url)
        params = await asyncio.to_thread(server.wait, timeout)
    finally:
        await asyncio.to_thread(server.close)
    if params is None:
        raise ToolError(f"{cfg.provider} sign-in timed out after {timeout:.0f}s", "AuthTimeout")
    if params.get("state") != state:
        raise ToolError(f"{cfg.provider} sign-in returned a mismatched state (possible CSRF); aborted", "AuthError")
    if "error" in params:
        raise ToolError(
            f"{cfg.provider} sign-in failed: {params['error']} {params.get('error_description', '')}".strip(), "AuthError"
        )
    body = await _token_request(
        cfg,
        {
            "grant_type": "authorization_code",
            "code": params["code"],
            "redirect_uri": redirect_uri,
            "client_id": cfg.client_id,
            "code_verifier": verifier,
        },
        http,
    )
    tokens = OAuthTokens.from_response(body)
    if not tokens.refresh_token:
        raise ToolError(f"{cfg.provider} did not return a refresh token; check that offline access was granted", "AuthError")
    secret_store.set(cfg.refresh_secret_name, tokens.refresh_token)
    return tokens


async def refresh_access_token(cfg: OAuthClientConfig, refresh_token: str, http: httpx.AsyncClient | None = None) -> OAuthTokens:
    form = {"grant_type": "refresh_token", "refresh_token": refresh_token, "client_id": cfg.client_id}
    if cfg.refresh_with_scope:
        form["scope"] = " ".join(cfg.scopes)
    body = await _token_request(cfg, form, http)
    return OAuthTokens.from_response(body)


class OAuthSession:
    """Access-token provider backed by a refresh token in the credential manager."""

    def __init__(self, cfg: OAuthClientConfig, secret_store: Any, http: httpx.AsyncClient | None = None) -> None:
        self.cfg = cfg
        self.secrets = secret_store
        self._http = http
        self._tokens: OAuthTokens | None = None
        self._lock = asyncio.Lock()

    def has_refresh_token(self) -> bool:
        return bool(self.secrets.has(self.cfg.refresh_secret_name))

    def invalidate(self) -> None:
        self._tokens = None

    async def access_token(self) -> str:
        async with self._lock:
            if self._tokens is not None and self._tokens.expires_at - REFRESH_MARGIN_S > time.time():
                return self._tokens.access_token
            secret = self.secrets.get(self.cfg.refresh_secret_name)
            if secret is None:
                raise IntegrationAuthError(
                    f"{self.cfg.provider} is not signed in: run `{self.cfg.login_command}`", self.cfg.setup_doc
                )
            tokens = await refresh_access_token(self.cfg, secret.get_secret_value(), self._http)
            if tokens.refresh_token and tokens.refresh_token != secret.get_secret_value():
                # rotating refresh tokens (Microsoft): persist the new one, still only in the credential manager
                self.secrets.set(self.cfg.refresh_secret_name, tokens.refresh_token)
            self._tokens = tokens
            return tokens.access_token
