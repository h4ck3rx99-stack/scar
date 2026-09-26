"""Microsoft identity platform OAuth (public client, authorization code + PKCE, no MSAL).

App registration: Azure portal -> Microsoft Entra ID -> App registrations ->
New registration; platform "Mobile and desktop applications" with redirect
URI ``http://localhost`` (the identity platform ignores the port for loopback
redirects); "Allow public client flows" = Yes. No client secret is used.
Microsoft rotates refresh tokens; each new one replaces the old one in the
Windows Credential Manager. See docs/integrations/outlook.md.
"""

from __future__ import annotations

from typing import Any

import httpx

from scar.core.errors import CapabilityUnavailable
from scar.integrations.oauth import OAuthClientConfig, OAuthSession

SETUP_DOC = "docs/integrations/outlook.md"
REFRESH_SECRET = "SCAR_MS_REFRESH_TOKEN"
LOGIN_COMMAND = "scar auth microsoft"
NOT_CONFIGURED = "OUTLOOK not configured: set SCAR_MS_CLIENT_ID and run `scar auth microsoft`"

SCOPES: tuple[str, ...] = (
    "offline_access",  # refresh token
    "User.Read",  # own address (reply-all exclusion)
    "Mail.ReadWrite",  # search/read, drafts, verify Sent Items
    "Mail.Send",
    "Calendars.ReadWrite",
    "Contacts.Read",
)


def microsoft_oauth_config(settings: Any) -> OAuthClientConfig:
    client_id = str(getattr(settings, "ms_client_id", "") or "")
    if not client_id:
        raise CapabilityUnavailable(NOT_CONFIGURED, SETUP_DOC)
    tenant = str(getattr(settings, "ms_tenant", "") or "common")
    base = f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0"
    return OAuthClientConfig(
        provider="Microsoft",
        client_id=client_id,
        auth_url=f"{base}/authorize",
        token_url=f"{base}/token",
        scopes=SCOPES,
        refresh_secret_name=REFRESH_SECRET,
        setup_doc=SETUP_DOC,
        login_command=LOGIN_COMMAND,
        extra_auth_params={"prompt": "select_account"},
        redirect_host="localhost",
        refresh_with_scope=True,
    )


def microsoft_configured(settings: Any) -> bool:
    return bool(getattr(settings, "ms_client_id", ""))


def microsoft_signed_in(settings: Any, secrets: Any) -> bool:
    return microsoft_configured(settings) and bool(secrets.has(REFRESH_SECRET))


def microsoft_session(settings: Any, secrets: Any, http: httpx.AsyncClient | None = None) -> OAuthSession:
    cfg = microsoft_oauth_config(settings)
    if not secrets.has(REFRESH_SECRET):
        raise CapabilityUnavailable(f"Microsoft is not signed in: run `{LOGIN_COMMAND}`", SETUP_DOC)
    return OAuthSession(cfg, secrets, http)
