"""Opt-in WhatsApp Desktop provider (``SCAR_WHATSAPP_DESKTOP_ENABLED=true``, default off).

Drives the user's own logged-in WhatsApp Desktop app:

1. ``os.startfile("whatsapp://send?phone=<digits>&text=<text>")`` opens the chat
   with the text pre-filled;
2. UI Automation finds the WhatsApp window and invokes its **Send** button;
3. verification looks for the sent text in the chat's message list and checks
   the compose box emptied.

Caveats: this depends on WhatsApp Desktop's accessibility tree (English UI
names; Microsoft Store app), needs the desktop to be unlocked, and cannot
report a message id. When verification is not possible the result is
``verified=None`` ("sent but couldn't confirm"). Using automation on a
personal WhatsApp account is at your own risk under WhatsApp's Terms of
Service; keep it to low-volume, personal messages.
"""

from __future__ import annotations

import asyncio
import importlib.util
import os
import sys
import time
from typing import Any
from urllib.parse import quote

from scar.core.errors import CapabilityUnavailable, ToolError
from scar.integrations.live_guard import ensure_live_send_allowed
from scar.integrations.whatsapp.cloud_api import normalise_phone

SETUP_DOC = "docs/integrations/whatsapp.md#desktop-provider-opt-in"
WINDOW_NAMES = ("WhatsApp", "WhatsApp Beta")
SEND_BUTTON_NAMES = ("Send", "Send message")
WINDOW_TIMEOUT = 20.0
BUTTON_TIMEOUT = 10.0


def build_send_uri(phone_digits: str, text: str) -> str:
    return f"whatsapp://send?phone={phone_digits}&text={quote(text, safe='')}"


class WhatsAppDesktop:
    def __init__(self, settings: Any) -> None:
        self.settings = settings

    def enabled(self) -> bool:
        return bool(getattr(self.settings, "whatsapp_desktop_enabled", False))

    def _require(self) -> None:
        if not self.enabled():
            raise CapabilityUnavailable(
                "WhatsApp Desktop provider is disabled: set SCAR_WHATSAPP_DESKTOP_ENABLED=true "
                "(opt-in) or configure the WhatsApp Cloud API",
                SETUP_DOC,
            )
        if sys.platform != "win32":
            raise CapabilityUnavailable("WhatsApp Desktop automation requires Windows", SETUP_DOC)
        if importlib.util.find_spec("uiautomation") is None:
            raise CapabilityUnavailable("Python package 'uiautomation' is not installed", SETUP_DOC)

    async def send_text(self, phone: str, text: str) -> dict[str, Any]:
        self._require()
        if not text.strip():
            raise ToolError("message text is empty", "InvalidInput")
        digits = normalise_phone(phone)
        ensure_live_send_allowed(self.settings, "whatsapp", digits)
        uri = build_send_uri(digits, text)
        try:
            os.startfile(uri)  # type: ignore[attr-defined]  # Windows only; checked in _require
        except OSError as exc:
            raise CapabilityUnavailable(
                "WhatsApp Desktop is not installed (whatsapp:// is not registered)", SETUP_DOC, str(exc)
            ) from exc
        return await asyncio.to_thread(_press_send_and_verify, text)


def _press_send_and_verify(text: str) -> dict[str, Any]:
    import uiautomation as auto

    with auto.UIAutomationInitializerInThread():
        window = None
        deadline = time.monotonic() + WINDOW_TIMEOUT
        while window is None and time.monotonic() < deadline:
            for name in WINDOW_NAMES:
                candidate = auto.WindowControl(searchDepth=1, Name=name)
                if candidate.Exists(maxSearchSeconds=1.0, searchIntervalSeconds=0.25):
                    window = candidate
                    break
        if window is None:
            raise ToolError("WhatsApp Desktop did not open; the message was NOT sent", "UiNotFound")
        button = None
        for name in SEND_BUTTON_NAMES:
            candidate = window.ButtonControl(Name=name)
            if candidate.Exists(maxSearchSeconds=BUTTON_TIMEOUT / len(SEND_BUTTON_NAMES), searchIntervalSeconds=0.25):
                button = candidate
                break
        if button is None:
            raise ToolError(
                "could not find WhatsApp's Send button; the text is pre-filled but NOT sent - check the WhatsApp window",
                "UiNotFound",
            )
        pattern: Any = button.GetInvokePattern()  # None when the control lacks the Invoke pattern
        if pattern is not None:
            pattern.Invoke()
        else:
            button.Click(simulateMove=False)
        time.sleep(1.5)
        return {"message_id": None, "provider": "desktop", **_verify_in_chat(auto, window, text)}


def _verify_in_chat(auto: Any, window: Any, text: str) -> dict[str, Any]:
    snippet = " ".join(text.split())[:40]
    if not snippet:
        return {"verified": None, "verification_note": "empty text"}
    compose_empty: bool | None = None
    found_in_list = False
    try:
        for control, _depth in auto.WalkControl(window, maxDepth=30):
            name = " ".join(str(control.Name or "").split())
            ctype = control.ControlTypeName
            if ctype == "EditControl":
                pattern = control.GetValuePattern()
                value = pattern.Value if pattern is not None else name
                if snippet in " ".join(str(value or "").split()):
                    compose_empty = False
                elif compose_empty is None:
                    compose_empty = True
            elif snippet in name:
                found_in_list = True
    except Exception as exc:  # noqa: BLE001 - COM/UIA errors (comtypes.COMError etc.) have no common base
        return {"verified": None, "verification_note": f"sent but couldn't confirm: UI walk failed ({exc})"}
    if found_in_list and compose_empty is not False:
        return {"verified": True, "verification_note": "message text visible in the chat"}
    if compose_empty is False:
        return {"verified": False, "verification_note": "the text is still in the compose box; send did not happen"}
    return {"verified": None, "verification_note": "sent but couldn't confirm the message in the chat"}
