"""OAuth installed-app flow: PKCE + loopback redirect + token exchange (token endpoint mocked with respx)."""

from __future__ import annotations

import base64
import hashlib
import threading
import urllib.request
from urllib.parse import parse_qs, urlencode, urlparse

import httpx
import pytest
import respx
from pydantic import SecretStr

from scar.core.errors import ToolError
from scar.integrations.oauth import OAuthClientConfig, OAuthSession, make_pkce_pair, run_authorization_flow

TOKEN_URL = "https://login.example.test/token"


class FakeSecrets:
    def __init__(self, **values: str) -> None:
        self.values = dict(values)

    def get(self, name: str) -> SecretStr | None:
        v = self.values.get(name)
        return SecretStr(v) if v else None

    def has(self, name: str) -> bool:
        return bool(self.values.get(name))

    def set(self, name: str, value: str) -> None:
        self.values[name] = value


CFG = OAuthClientConfig(
    provider="Example",
    client_id="cid-1",
    auth_url="https://login.example.test/authorize",
    token_url=TOKEN_URL,
    scopes=("offline_access", "Mail.Send"),
    refresh_secret_name="SCAR_EXAMPLE_REFRESH_TOKEN",
    setup_doc="docs/x.md",
    login_command="scar auth example",
    extra_auth_params={"prompt": "consent"},
    refresh_with_scope=True,
)


def _browser(tamper_state: bool = False):
    seen: dict[str, str] = {}

    def open_browser(url: str) -> bool:
        q = {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}
        seen.update(q)
        state = "evil" if tamper_state else q["state"]
        redirect = q["redirect_uri"] + "?" + urlencode({"code": "auth-code-42", "state": state})

        def hit() -> None:
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # loopback only, never a proxy
            with opener.open(redirect, timeout=5) as resp:
                resp.read()

        threading.Thread(target=hit, daemon=True).start()
        return True

    return open_browser, seen


def test_pkce_pair_is_s256() -> None:
    verifier, challenge = make_pkce_pair()
    assert 43 <= len(verifier) <= 128
    expected = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    assert challenge == expected


async def test_authorization_code_flow_stores_refresh_token_only_in_secret_store() -> None:
    secrets = FakeSecrets()
    open_browser, seen = _browser()
    with respx.mock(assert_all_mocked=True, assert_all_called=False) as mock:
        token = mock.post(TOKEN_URL).mock(
            return_value=httpx.Response(
                200, json={"access_token": "at-1", "refresh_token": "rt-1", "expires_in": 3600, "scope": "Mail.Send"}
            )
        )
        tokens = await run_authorization_flow(CFG, secrets, open_browser=open_browser, timeout=10)
    assert tokens.access_token == "at-1"
    assert secrets.values["SCAR_EXAMPLE_REFRESH_TOKEN"] == "rt-1"
    assert seen["code_challenge_method"] == "S256" and seen["response_type"] == "code"
    assert seen["scope"] == "offline_access Mail.Send" and seen["prompt"] == "consent"
    redirect = urlparse(seen["redirect_uri"])
    assert redirect.hostname == "127.0.0.1" and redirect.port and redirect.port > 0
    form = parse_qs(token.calls.last.request.content.decode())
    assert form["grant_type"] == ["authorization_code"] and form["code"] == ["auth-code-42"]
    assert form["redirect_uri"] == [seen["redirect_uri"]]
    verifier = form["code_verifier"][0]
    assert base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode() == seen["code_challenge"]


async def test_state_mismatch_is_rejected() -> None:
    open_browser, _ = _browser(tamper_state=True)
    with respx.mock(assert_all_mocked=True, assert_all_called=False) as mock:
        token = mock.post(TOKEN_URL)
        with pytest.raises(ToolError, match="mismatched state"):
            await run_authorization_flow(CFG, FakeSecrets(), open_browser=open_browser, timeout=10)
    assert not token.called


async def test_session_refresh_rotates_refresh_token() -> None:
    secrets = FakeSecrets(SCAR_EXAMPLE_REFRESH_TOKEN="rt-old")
    with respx.mock(assert_all_mocked=True, assert_all_called=False) as mock:
        token = mock.post(TOKEN_URL).mock(
            return_value=httpx.Response(200, json={"access_token": "at-2", "refresh_token": "rt-new", "expires_in": 3600})
        )
        session = OAuthSession(CFG, secrets)
        assert await session.access_token() == "at-2"
        assert await session.access_token() == "at-2"  # cached
    assert token.call_count == 1
    form = parse_qs(token.calls.last.request.content.decode())
    assert form["scope"] == ["offline_access Mail.Send"] and form["refresh_token"] == ["rt-old"]
    assert secrets.values["SCAR_EXAMPLE_REFRESH_TOKEN"] == "rt-new"
