"""What the Settings screen shows: current non-secret values plus type, choices, group and description per field."""

from __future__ import annotations

import typing
from typing import Any

from scar.config.descriptions import DESCRIPTIONS
from scar.config.settings import SECRET_KEYS, Settings
from scar.config.writer import is_secret_name

GROUPS = {
    "Assistant": ("autonomy_level", "require_approval", "dry_run", "allowed_roots", "protected_paths", "pc_control_enabled",
                  "browser_enabled", "terminal_enabled", "approval_timeout", "max_steps"),
    "AI": ("llm_provider", "llm_model", "llm_fallbacks", "fast_llm_provider", "fast_llm_model", "vision_provider", "vision_model",
           "local_inference_policy", "local_llm_model_path", "local_vlm_model_path", "local_vlm_mmproj_path", "ollama_url",
           "model_idle_timeout", "privacy_mode"),
    "Voice": ("voice_enabled", "wake_word", "wake_word_threshold", "ptt_hotkey", "mic_device", "speaker_device", "tts_provider",
              "tts_voice", "stt_provider", "local_stt_model", "voice_session_timeout", "voice_approval_window"),
    "App": ("theme", "quickbar_hotkey", "start_minimized", "keep_running_in_background", "control_indicator", "game_mode",
            "kill_switch_hotkey", "app_onboarding_step"),
    "Accounts": ("email_provider", "calendar_provider", "google_oauth_client_file", "ms_client_id", "ms_tenant", "imap_host",
                 "imap_port", "imap_user", "smtp_host", "smtp_port", "telegram_api_id", "discord_default_channel",
                 "whatsapp_desktop_enabled", "whatsapp_phone_number_id"),
}


def _kind(annotation: Any) -> tuple[str, list[Any]]:
    origin = typing.get_origin(annotation)
    if origin is typing.Literal:
        return "choice", list(typing.get_args(annotation))
    text = str(annotation)
    for name in ("bool", "int", "float"):
        if text == f"<class '{name}'>" or text == name:
            return name, []
    if "list" in text:
        return "list", []
    return "text", []


def settings_view(settings: Settings) -> dict[str, Any]:
    grouped = {f: g for g, fields in GROUPS.items() for f in fields}
    fields = []
    for name, info in Settings.model_fields.items():
        if name.upper() in SECRET_KEYS or is_secret_name(name) or name in ("live_tests", "debug", "verbose"):
            continue
        kind, choices = _kind(info.annotation)
        fields.append({"name": name, "kind": kind, "choices": choices, "group": grouped.get(name, "Advanced"),
                       "description": DESCRIPTIONS.get(name, ""), "value": getattr(settings, name, None),
                       "default": info.default if not callable(info.default) else None})
    return {"fields": fields}
