"""Account connection status and connect/disconnect flows for the app's Settings → Accounts screen.

Each account reports exactly what it still needs ("Needs: …"). Google and Microsoft sign in through the system
browser with a loopback redirect (the existing OAuth flow). Token-based services (Telegram bot, Discord bot, GitHub)
are connected by storing their key in Settings → AI & keys and verified here with one live, read-only call.
Nothing is ever sent to anyone during connect or verify.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import structlog

from scar.core.events import TaskProgress

log = structlog.get_logger("scar.accounts")

SETUP_DOCS = {"google": "docs/integrations/gmail.md", "microsoft": "docs/integrations/outlook.md",
              "imap": "docs/integrations/gmail.md#imap-smtp-alternative", "telegram": "docs/integrations/telegram.md",
              "telegram_user": "docs/integrations/telegram.md", "discord": "docs/integrations/discord.md",
              "whatsapp": "docs/integrations/whatsapp.md", "github": "docs/integrations/github.md"}


def _entry(name: str, label: str, provides: str, connected: bool, needs: list[str], connect: str) -> dict[str, Any]:
    return {"name": name, "label": label, "provides": provides, "connected": connected, "needs": needs,
            "connect": connect, "doc": SETUP_DOCS.get(name, "")}


def account_status(services: Any) -> dict[str, Any]:
    s, sec = services.settings, services.secrets
    from scar.integrations.google.auth import REFRESH_SECRET as G_REFRESH
    from scar.integrations.microsoft.auth import REFRESH_SECRET as MS_REFRESH

    out = []
    g_needs = [] if s.google_oauth_client_file else ["a Google OAuth client file (Desktop app) — set it below"]
    if not sec.has(G_REFRESH):
        g_needs.append("sign-in")
    out.append(_entry("google", "Google", "Gmail, Google Calendar, Contacts", not g_needs, g_needs, "oauth"))
    ms_needs = [] if s.ms_client_id else ["an Azure app registration client ID — set it below"]
    if not sec.has(MS_REFRESH):
        ms_needs.append("sign-in")
    out.append(_entry("microsoft", "Microsoft", "Outlook mail, calendar, contacts", not ms_needs, ms_needs, "oauth"))
    imap_needs = [n for n, ok in (("IMAP server", s.imap_host), ("IMAP user", s.imap_user), ("SMTP server", s.smtp_host),
                                  ("the SCAR_IMAP_PASSWORD key", sec.has("SCAR_IMAP_PASSWORD"))) if not ok]
    out.append(_entry("imap", "Other email (IMAP/SMTP)", "Any mailbox with an app password", not imap_needs, imap_needs,
                      "settings"))
    tg = sec.has("TELEGRAM_BOT_TOKEN")
    out.append(_entry("telegram", "Telegram bot", "Messages to people who started your bot", tg,
                      [] if tg else ["the TELEGRAM_BOT_TOKEN key from @BotFather"], "key"))
    tgu_needs = [n for n, ok in (("telegram_api_id", s.telegram_api_id), ("the TELEGRAM_API_HASH key", sec.has("TELEGRAM_API_HASH")))
                 if not ok]
    session = s.data_path / "telethon" / "scar.session"
    if not session.exists():
        tgu_needs.append("sign-in in a terminal: `uv run scar auth telegram` (needs your phone and a login code)")
    out.append(_entry("telegram_user", "Telegram (your account)", "Messages from your own account", not tgu_needs, tgu_needs,
                      "terminal"))
    dc = sec.has("DISCORD_BOT_TOKEN")
    out.append(_entry("discord", "Discord bot", "Posts to channels your bot was invited to (bots only)", dc,
                      [] if dc else ["the DISCORD_BOT_TOKEN key"], "key"))
    wa_desktop = bool(s.whatsapp_desktop_enabled)
    wa_cloud = bool(s.whatsapp_phone_number_id) and sec.has("WHATSAPP_CLOUD_TOKEN")
    out.append(_entry("whatsapp", "WhatsApp", "Desktop app automation or the Cloud API", wa_desktop or wa_cloud,
                      [] if (wa_desktop or wa_cloud) else ["WhatsApp Desktop signed in + whatsapp_desktop_enabled, or a Cloud API number and token"],
                      "settings"))
    gh = sec.has("GITHUB_TOKEN")
    out.append(_entry("github", "GitHub", "Issues, pull requests and CI", gh, [] if gh else ["the GITHUB_TOKEN key"], "key"))
    return {"accounts": out}


async def start_connect(services: Any, name: str, jobs: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """OAuth accounts: open the system browser and wait for the loopback redirect in the background.
    Key-based accounts: verify the stored key with one read-only call."""
    if name in ("google", "microsoft"):
        job = {"name": name, "state": "waiting_for_browser", "message": "Opening your browser to sign in…", "at": time.time()}
        jobs[name] = job
        asyncio.create_task(_oauth(services, name, job), name=f"connect-{name}")
        return job
    if name in ("telegram", "discord", "github"):
        return await verify(services, name)
    raise_unknown(name)
    return {}


def raise_unknown(name: str) -> None:
    from scar.api.server import ApiError

    raise ApiError(400, f"{name} is connected by filling in its settings/keys, not by a sign-in flow")


async def _oauth(services: Any, name: str, job: dict[str, Any]) -> None:
    from scar.integrations.oauth import run_authorization_flow

    def announce(msg: str) -> None:
        job["message"] = msg
        services.bus.publish(TaskProgress(message=f"{name}: {msg}"))

    try:
        if name == "google":
            from scar.integrations.google.auth import google_oauth_config

            cfg = google_oauth_config(services.settings)
        else:
            from scar.integrations.microsoft.auth import microsoft_oauth_config

            cfg = microsoft_oauth_config(services.settings)
        await run_authorization_flow(cfg, services.secrets, announce=announce)
        job.update(state="connected", message="Signed in.")
    except Exception as exc:  # noqa: BLE001 - every failure becomes a readable message on the Accounts screen
        job.update(state="failed", message=str(exc)[:300])
        log.warning("account_connect_failed", account=name, error=str(exc)[:200])
    services.bus.publish(TaskProgress(message=f"{name}: {job['message']}"))


async def verify(services: Any, name: str) -> dict[str, Any]:
    try:
        if name == "telegram":
            from scar.integrations.telegram.bot import TelegramBot

            me = await TelegramBot(services.settings, services.secrets).get_me()
            who = f"@{me.get('username')}"
        elif name == "discord":
            from scar.integrations.discord.bot import DiscordBot

            me = await DiscordBot(services.settings, services.secrets).me()
            who = str(me.get("username"))
        else:
            gh = services.github
            me = await gh.request("GET", "/user")
            who = str(me.get("login"))
        return {"name": name, "state": "connected", "message": f"Connected as {who}."}
    except Exception as exc:  # noqa: BLE001 - shown to the user as the reason
        return {"name": name, "state": "failed", "message": str(exc)[:300]}


def disconnect(services: Any, name: str) -> dict[str, Any]:
    from scar.integrations.google.auth import REFRESH_SECRET as G_REFRESH
    from scar.integrations.microsoft.auth import REFRESH_SECRET as MS_REFRESH

    names = {"google": [G_REFRESH], "microsoft": [MS_REFRESH], "telegram": ["TELEGRAM_BOT_TOKEN"],
             "discord": ["DISCORD_BOT_TOKEN"], "github": ["GITHUB_TOKEN"], "imap": ["SCAR_IMAP_PASSWORD", "SCAR_SMTP_PASSWORD"]}
    removed = [n for n in names.get(name, []) if services.secrets.delete(n)]
    if name == "telegram_user":
        session = services.settings.data_path / "telethon" / "scar.session"
        if session.exists():
            session.unlink()
            removed.append("telegram session")
    services.audit.append("account_disconnected", {"account": name, "removed": removed, "by": "app"})
    return {"name": name, "removed": removed}
