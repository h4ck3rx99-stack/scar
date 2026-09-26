"""Google OAuth configuration (installed "Desktop app" client, loopback + PKCE).

Refresh-token lifetime: while the OAuth consent screen's *publishing status*
is **Testing**, Google expires refresh tokens after **7 days** (the user sees
"Sign-in expired" and must run ``scar auth google`` again). To avoid this,
open Google Cloud Console -> APIs & Services -> OAuth consent screen and
click **Publish app** (status "In production"; for personal use with
sensitive scopes you will see an "unverified app" warning you can accept for
your own account), or use an **Internal** user type on a Google Workspace
domain. See docs/integrations/gmail.md.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx

from scar.core.errors import CapabilityUnavailable
from scar.integrations.oauth import OAuthClientConfig, OAuthSession

SETUP_DOC = "docs/integrations/gmail.md"
CALENDAR_DOC = "docs/integrations/calendar.md"
REFRESH_SECRET = "SCAR_GOOGLE_REFRESH_TOKEN"
LOGIN_COMMAND = "scar auth google"

# Minimal scopes for what SCAR does:
SCOPES: tuple[str, ...] = (
    "https://www.googleapis.com/auth/gmail.readonly",  # search/read, check SENT label
    "https://www.googleapis.com/auth/gmail.compose",  # create drafts and send
    "https://www.googleapis.com/auth/calendar.events",  # events CRUD on the user's calendars
    "https://www.googleapis.com/auth/contacts.readonly",  # People API connections search
)

EXPIRY_HINT = (
    "If your OAuth consent screen is in 'Testing' status Google expires refresh tokens after 7 days; "
    "publish the app (In production) or use an Internal Workspace app to avoid this."
)

NOT_CONFIGURED = "GMAIL not configured: set SCAR_GOOGLE_OAUTH_CLIENT_FILE and run `scar auth google`"


def load_client_file(path_str: str) -> dict[str, Any]:
    if not path_str:
        raise CapabilityUnavailable(NOT_CONFIGURED, SETUP_DOC)
    path = Path(path_str).expanduser()
    if not path.is_file():
        raise CapabilityUnavailable(
            f"Google OAuth client file not found at {path}: download it from Google Cloud "
            "Console (Desktop app client) and set SCAR_GOOGLE_OAUTH_CLIENT_FILE",
            SETUP_DOC,
        )
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise CapabilityUnavailable(f"Google OAuth client file {path} is not valid JSON", SETUP_DOC, str(exc)) from exc
    section = raw.get("installed") if isinstance(raw, dict) else None
    if not isinstance(section, dict):
        kind = next(iter(raw), "unknown") if isinstance(raw, dict) and raw else "unknown"
        raise CapabilityUnavailable(f"Google OAuth client file must be a 'Desktop app' client (found {kind!r})", SETUP_DOC)
    if not section.get("client_id"):
        raise CapabilityUnavailable("Google OAuth client file has no client_id", SETUP_DOC)
    return section


def google_oauth_config(settings: Any) -> OAuthClientConfig:
    section = load_client_file(str(getattr(settings, "google_oauth_client_file", "") or ""))
    return OAuthClientConfig(
        provider="Google",
        client_id=str(section["client_id"]),
        client_secret=str(section.get("client_secret") or "") or None,
        auth_url=str(section.get("auth_uri") or "https://accounts.google.com/o/oauth2/v2/auth"),
        token_url=str(section.get("token_uri") or "https://oauth2.googleapis.com/token"),
        scopes=SCOPES,
        refresh_secret_name=REFRESH_SECRET,
        setup_doc=SETUP_DOC,
        login_command=LOGIN_COMMAND,
        extra_auth_params={"access_type": "offline", "prompt": "consent"},
        redirect_host="127.0.0.1",
        refresh_expiry_hint=EXPIRY_HINT,
    )


def google_configured(settings: Any) -> bool:
    return bool(getattr(settings, "google_oauth_client_file", ""))


def google_signed_in(settings: Any, secrets: Any) -> bool:
    return google_configured(settings) and bool(secrets.has(REFRESH_SECRET))


def google_session(settings: Any, secrets: Any, http: httpx.AsyncClient | None = None) -> OAuthSession:
    cfg = google_oauth_config(settings)
    if not secrets.has(REFRESH_SECRET):
        raise CapabilityUnavailable(f"Google is not signed in: run `{LOGIN_COMMAND}`", SETUP_DOC)
    return OAuthSession(cfg, secrets, http)
