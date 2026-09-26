"""Interactive sign-in helpers for ``scar auth google|microsoft|telegram``.

The CLI calls these with the runtime ``Services``; each runs the provider's
interactive flow, stores long-lived credentials only in the Windows Credential
Manager (or, for Telegram user sessions, the Telethon session file under the
data dir) and prints what it did.
"""

from __future__ import annotations

import getpass
from collections.abc import Awaitable, Callable
from typing import Any

from scar.core.errors import CapabilityUnavailable

AuthFn = Callable[[Any], Awaitable[None]]


def _print(msg: str) -> None:
    print(msg, flush=True)  # CLI output


async def login_google(services: Any) -> None:
    from scar.integrations.google.auth import EXPIRY_HINT, google_oauth_config
    from scar.integrations.google.gmail import GmailClient
    from scar.integrations.oauth import OAuthSession, run_authorization_flow

    cfg = google_oauth_config(services.settings)
    _print(f"Signing in to Google with client {cfg.client_id[:24]}… (scopes: {', '.join(cfg.scopes)})")
    await run_authorization_flow(cfg, services.secrets, announce=_print)
    _print(f"Stored the Google refresh token in Windows Credential Manager as {cfg.refresh_secret_name!r}.")
    email = await GmailClient(OAuthSession(cfg, services.secrets), services.settings).profile_email()
    _print(f"Signed in as {email}. Gmail, Google Calendar and Google Contacts are ready.")
    _print(f"Note: {EXPIRY_HINT}")


async def login_microsoft(services: Any) -> None:
    from scar.integrations.microsoft.auth import microsoft_oauth_config
    from scar.integrations.microsoft.graph import GraphClient
    from scar.integrations.oauth import OAuthSession, run_authorization_flow

    cfg = microsoft_oauth_config(services.settings)
    _print(f"Signing in to Microsoft (tenant {services.settings.ms_tenant!r}) with scopes: {', '.join(cfg.scopes)}")
    await run_authorization_flow(cfg, services.secrets, announce=_print)
    _print(f"Stored the Microsoft refresh token in Windows Credential Manager as {cfg.refresh_secret_name!r}.")
    me = await GraphClient(OAuthSession(cfg, services.secrets), services.settings).me_address()
    _print(f"Signed in as {me}. Outlook mail, calendar and contacts are ready.")


async def login_telegram_user(services: Any) -> None:
    from scar.integrations.telegram.user_client import SETUP_DOC, TelegramUserClient

    client = TelegramUserClient(services.settings, services.secrets)
    _print("Signing in to your Telegram account with Telethon. Telegram will send a login code to your Telegram app (or SMS).")
    _print("Reminder: Telegram's Terms of Service forbid spam and bulk messaging from user accounts.")
    try:
        me = await client.login(
            phone=lambda: input("Phone number (international format, e.g. +15551234567): ").strip(),
            code=lambda: input("Login code: ").strip(),
            password=lambda: getpass.getpass("Two-step verification password (if enabled): "),
        )
    except CapabilityUnavailable:
        _print(f"Telegram API id/hash are missing; see {SETUP_DOC}.")
        raise
    finally:
        await client.close()
    who = f"@{me['username']}" if me["username"] else me["name"]
    _print(
        f"Signed in to Telegram as {who} (id {me['id']}). Session saved to {client.session_path}.session "
        "- keep this file private."
    )


def auth_commands() -> dict[str, AuthFn]:
    """Map of ``scar auth <name>`` sub-commands to their async handlers."""
    return {"google": login_google, "microsoft": login_microsoft, "telegram": login_telegram_user}
