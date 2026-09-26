"""The live-send guard: under SCAR_TESTING=1 nothing is sent except to the configured test recipient."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from scar.core.errors import PolicyDenied
from scar.integrations.live_guard import LiveSendBlocked, ensure_live_send_allowed


def _settings(**kw: str) -> SimpleNamespace:
    base = {"test_email_to": "", "test_telegram_chat": "", "test_discord_channel": "", "live_tests": False}
    base.update(kw)
    return SimpleNamespace(**base)


def test_blocked_under_testing_without_live_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SCAR_TESTING", "1")
    monkeypatch.delenv("SCAR_LIVE_TESTS", raising=False)
    with pytest.raises(LiveSendBlocked) as info:
        ensure_live_send_allowed(_settings(test_email_to="me@example.com"), "email", ["me@example.com"])
    assert isinstance(info.value, PolicyDenied)


@pytest.mark.parametrize(
    ("channel", "attr", "value", "other"),
    [
        ("email", "test_email_to", "Me@Example.com", "you@example.com"),
        ("telegram", "test_telegram_chat", "424242", "424243"),
        ("discord", "test_discord_channel", "112233445566778899", "112233445566778800"),
    ],
)
def test_only_exact_test_recipient_allowed(monkeypatch, channel, attr, value, other) -> None:
    monkeypatch.setenv("SCAR_TESTING", "1")
    monkeypatch.setenv("SCAR_LIVE_TESTS", "1")
    s = _settings(**{attr: value})
    ensure_live_send_allowed(s, channel, value.lower() if channel == "email" else value)
    with pytest.raises(LiveSendBlocked):
        ensure_live_send_allowed(s, channel, other)
    with pytest.raises(LiveSendBlocked):  # every recipient must match
        ensure_live_send_allowed(s, channel, [value, other])
    with pytest.raises(LiveSendBlocked):
        ensure_live_send_allowed(_settings(), channel, value)  # no test recipient configured


def test_whatsapp_uses_env_recipient(monkeypatch) -> None:
    monkeypatch.setenv("SCAR_TESTING", "1")
    monkeypatch.setenv("SCAR_LIVE_TESTS", "1")
    monkeypatch.delenv("SCAR_TEST_WHATSAPP_TO", raising=False)
    with pytest.raises(LiveSendBlocked):
        ensure_live_send_allowed(_settings(), "whatsapp", "15551234567")
    monkeypatch.setenv("SCAR_TEST_WHATSAPP_TO", "15551234567")
    ensure_live_send_allowed(_settings(), "whatsapp", "15551234567")


def test_not_testing_is_unrestricted(monkeypatch) -> None:
    monkeypatch.delenv("SCAR_TESTING", raising=False)
    ensure_live_send_allowed(_settings(), "email", ["anyone@example.com"])
