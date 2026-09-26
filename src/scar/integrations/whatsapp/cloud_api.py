"""WhatsApp Business Cloud API (Meta Graph API) sender.

Constraints (see docs/integrations/whatsapp.md):

* Sends come from a WhatsApp *Business* phone number registered in Meta's
  developer console (``settings.whatsapp_phone_number_id``), authenticated with
  ``WHATSAPP_CLOUD_TOKEN`` (a System User permanent token; the temporary
  console token expires after 24 h).
* Free-form text can only be sent inside the **24-hour customer service
  window** that opens when the recipient messages your business number.
  Outside it you must send an approved **message template**
  (:meth:`WhatsAppCloud.send_template`). Error 131047 signals this.
* While the app is in development mode the recipient must be on the number's
  allowed test-recipient list.
* The Cloud API has no endpoint to read message history; incoming messages
  are only delivered to a webhook, so ``recent`` is unavailable.
"""

from __future__ import annotations

import re
from typing import Any

import httpx

from scar.core.errors import CapabilityUnavailable, ToolError
from scar.integrations.common import (
    ApiClient,
    IntegrationAuthError,
    ProviderError,
    RateLimited,
    ServiceInfo,
    parse_retry_after,
)
from scar.integrations.live_guard import ensure_live_send_allowed

SETUP_DOC = "docs/integrations/whatsapp.md"
GRAPH_VERSION = "v23.0"
GRAPH_BASE = f"https://graph.facebook.com/{GRAPH_VERSION}"
TOKEN_SECRET = "WHATSAPP_CLOUD_TOKEN"
NOT_CONFIGURED = "WHATSAPP Cloud API not configured: set SCAR_WHATSAPP_PHONE_NUMBER_ID and store WHATSAPP_CLOUD_TOKEN"
MAX_TEXT = 4096
WINDOW_CLOSED = 131047
NOT_ALLOWED_RECIPIENT = 131030


def normalise_phone(raw: str) -> str:
    """E.164 digits without '+', spaces or punctuation (what the Cloud API expects in ``to``)."""
    digits = re.sub(r"[^\d]", "", raw)
    if raw.strip().startswith("00"):
        digits = digits[2:]
    if not 8 <= len(digits) <= 15:
        raise ToolError(f"{raw!r} is not an international phone number (include the country code)", "InvalidInput")
    return digits


class WhatsAppCloud:
    def __init__(self, settings: Any, secrets: Any, http: httpx.AsyncClient | None = None) -> None:
        self.settings = settings
        self.secrets = secrets
        self._http = http

    def configured(self) -> bool:
        return bool(getattr(self.settings, "whatsapp_phone_number_id", "")) and bool(self.secrets.has(TOKEN_SECRET))

    def _api(self) -> tuple[ApiClient, str]:
        phone_id = str(getattr(self.settings, "whatsapp_phone_number_id", "") or "")
        token = self.secrets.get(TOKEN_SECRET)
        if not phone_id or token is None:
            raise CapabilityUnavailable(NOT_CONFIGURED, SETUP_DOC)
        value = token.get_secret_value()
        api = ApiClient(
            ServiceInfo("WhatsApp Cloud API", SETUP_DOC, "renew WHATSAPP_CLOUD_TOKEN"),
            auth_header=lambda: f"Bearer {value}",
            http=self._http,
            base_url=GRAPH_BASE,
        )
        return api, phone_id

    async def _post(self, payload: dict[str, Any]) -> dict[str, Any]:
        api, phone_id = self._api()
        resp = await api.request("POST", f"{phone_id}/messages", json=payload, expected=tuple(range(200, 600)))
        try:
            body = resp.json()
        except ValueError:
            body = {}
        if resp.status_code in (200, 201) and isinstance(body, dict):
            return body
        err = (body.get("error") or {}) if isinstance(body, dict) else {}
        code = int(err.get("code") or 0)
        message = str(err.get("message") or resp.text[:200])
        details = str((err.get("error_data") or {}).get("details") or "")
        if resp.status_code == 401 or code == 190:
            raise IntegrationAuthError(
                "WHATSAPP_CLOUD_TOKEN is invalid or expired (temporary console tokens last 24 h; create a System User token)",
                SETUP_DOC,
                message,
                resp.status_code,
            )
        if code == WINDOW_CLOSED:
            raise ToolError(
                "WhatsApp: more than 24 hours since this person last messaged your business number; "
                "free-form text is not allowed - send an approved template instead",
                "WindowClosed",
            )
        if code == NOT_ALLOWED_RECIPIENT:
            raise ToolError(
                "WhatsApp: recipient is not on the test number's allowed list (development mode)", "RecipientNotAllowed"
            )
        if resp.status_code == 429 or code in (4, 80007, 130429, 131048, 131056):
            raise RateLimited("WhatsApp Cloud API", parse_retry_after(resp.headers.get("Retry-After")), message)
        if code == 0 and resp.status_code >= 400:
            api.raise_for(resp)
        raise ProviderError("WhatsApp Cloud API", resp.status_code, f"{message} {details}".strip())

    @staticmethod
    def _message_id(body: dict[str, Any]) -> str:
        messages = body.get("messages") or []
        mid = str(messages[0].get("id") or "") if messages else ""
        if not mid:
            raise ToolError("WhatsApp accepted the request but returned no message id", "ProviderError")
        return mid

    async def send_text(self, phone: str, text: str) -> dict[str, Any]:
        if not text.strip():
            raise ToolError("message text is empty", "InvalidInput")
        if len(text) > MAX_TEXT:
            raise ToolError(f"WhatsApp text messages are limited to {MAX_TEXT} characters", "TooLarge")
        to = normalise_phone(phone)
        self._api()
        ensure_live_send_allowed(self.settings, "whatsapp", to)
        body = await self._post(
            {
                "messaging_product": "whatsapp",
                "recipient_type": "individual",
                "to": to,
                "type": "text",
                "text": {"preview_url": False, "body": text},
            }
        )
        contacts = body.get("contacts") or []
        return {
            "message_id": self._message_id(body),
            "to": to,
            "wa_id": str(contacts[0].get("wa_id", "")) if contacts else "",
            "provider": "cloud_api",
        }

    async def send_template(
        self, phone: str, template: str, language: str = "en_US", body_parameters: list[str] | None = None
    ) -> dict[str, Any]:
        to = normalise_phone(phone)
        self._api()
        ensure_live_send_allowed(self.settings, "whatsapp", to)
        tpl: dict[str, Any] = {"name": template, "language": {"code": language}}
        if body_parameters:
            tpl["components"] = [{"type": "body", "parameters": [{"type": "text", "text": p} for p in body_parameters]}]
        body = await self._post({"messaging_product": "whatsapp", "to": to, "type": "template", "template": tpl})
        return {"message_id": self._message_id(body), "to": to, "provider": "cloud_api", "template": template}
