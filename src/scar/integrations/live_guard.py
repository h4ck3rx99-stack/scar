"""Live-send safety guard (mandatory for every real send path).

When the process runs under the test suite (``SCAR_TESTING=1``) no provider
may deliver anything to a real person. A send is only allowed when live tests
are explicitly enabled (``SCAR_LIVE_TESTS=1``) *and* the recipient exactly
equals the configured test recipient for that channel:

=========  ==============================================================
channel    test recipient
=========  ==============================================================
email      ``settings.test_email_to``
telegram   ``settings.test_telegram_chat``
discord    ``settings.test_discord_channel``
whatsapp   environment variable ``SCAR_TEST_WHATSAPP_TO`` (digits only; there
           is no Settings field for it). Unset -> always blocked under test.
=========  ==============================================================

Every provider's ``send`` calls :func:`ensure_live_send_allowed` before any
network or UI action.
"""

from __future__ import annotations

import os
from collections.abc import Iterable
from typing import Any

from scar.core.errors import PolicyDenied

_TEST_RECIPIENT_SETTING: dict[str, str] = {
    "email": "test_email_to",
    "telegram": "test_telegram_chat",
    "telegram_user": "test_telegram_chat",
    "discord": "test_discord_channel",
}


class LiveSendBlocked(PolicyDenied):
    """Raised instead of sending while running under the test suite."""


def is_testing() -> bool:
    return os.environ.get("SCAR_TESTING") == "1"


def live_tests_enabled(settings: Any) -> bool:
    return os.environ.get("SCAR_LIVE_TESTS") == "1" or bool(getattr(settings, "live_tests", False))


def _normalise(channel: str, value: str) -> str:
    value = value.strip()
    return value.casefold() if channel == "email" else value


def ensure_live_send_allowed(settings: Any, channel: str, recipients: str | Iterable[str]) -> None:
    """Raise ``LiveSendBlocked`` if this send must not happen under test."""
    if not is_testing():
        return
    targets = [recipients] if isinstance(recipients, str) else list(recipients)
    if not live_tests_enabled(settings):
        raise LiveSendBlocked(f"live send on {channel} blocked: SCAR_TESTING=1 and SCAR_LIVE_TESTS is not 1")
    if channel == "whatsapp":
        expected = os.environ.get("SCAR_TEST_WHATSAPP_TO", "")
    else:
        attr = _TEST_RECIPIENT_SETTING.get(channel)
        expected = str(getattr(settings, attr, "") or "") if attr else ""
    if not expected:
        raise LiveSendBlocked(f"live send on {channel} blocked under test: no test recipient is configured for this channel")
    if not targets:
        raise LiveSendBlocked(f"live send on {channel} blocked under test: no recipient")
    for target in targets:
        if _normalise(channel, target) != _normalise(channel, expected):
            raise LiveSendBlocked(
                f"live send on {channel} blocked under test: recipient {target!r} is not the configured test recipient"
            )
