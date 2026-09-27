"""Regenerate docs that are derived from code: docs/tools.md catalog section, docs/configuration.md table, .env.example.

    uv run python scripts/gen_docs.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from scar.config.settings import SECRET_KEYS, Settings  # noqa: E402
from scar.runtime.registration import build_registry  # noqa: E402

DESCRIPTIONS: dict[str, str] = {
    "llm_provider": "Preferred reasoning provider id (groq, gemini, cerebras, openrouter, mistral, nvidia, cloudflare, huggingface, anthropic, openai, ollama, llamacpp) or `auto`.",
    "llm_model": "Model for SCAR_LLM_PROVIDER (otherwise the catalog's candidates).",
    "llm_fallbacks": "Ordered extra candidates, `provider[:model]`, comma-separated.",
    "fast_llm_provider": "Provider for quick/small tasks (chat replies).", "fast_llm_model": "Model for the fast provider.",
    "vision_provider": "Preferred vision provider or `auto`.", "vision_model": "Vision model override.",
    "stt_provider": "`auto`, `groq` or `faster-whisper`.", "stt_model": "STT model override.",
    "local_stt_model": "faster-whisper size (tiny, base, small, medium).", "tts_provider": "`auto`, `azure-speech`, `edge-tts`, `sapi`, `kokoro` or `none`.",
    "tts_voice": "Voice name (e.g. en-US-AriaNeural, or part of a SAPI voice name).", "voice_enabled": "Enable hands-free wake-word mode in voice sessions.",
    "wake_word": "openWakeWord model: hey_jarvis, alexa, hey_mycroft, hey_rhasspy.", "wake_word_threshold": "Wake-word score threshold (0-1).",
    "ptt_hotkey": "Push-to-talk global hotkey (pynput syntax).", "mic_device": "Microphone: index or name substring (empty = default).",
    "speaker_device": "Speaker: index or name substring (empty = default).", "voice_session_timeout": "Seconds of follow-up listening after a reply.",
    "max_utterance_seconds": "Longest single utterance.", "voice_approval_window": "Seconds SCAR listens for an approval answer.",
    "embeddings_provider": "`fastembed` (local) or `none`.", "embeddings_model": "fastembed model id.",
    "search_provider": "`auto`, `brave`, `tavily`, `searxng`, `ddgs`.", "searxng_url": "Base URL of a SearXNG instance.",
    "memory_backend": "`faiss` (hybrid) or `fts_only` (keyword only, no embeddings).",
    "local_llm_url": "llama.cpp server URL to reuse or start.", "local_llm_server_bin": "Path to llama-server.exe (default: PATH).",
    "local_llm_model_path": "GGUF model SCAR starts llama-server with.", "local_llm_ctx": "Context size for the managed server.",
    "local_vlm_model_path": "GGUF vision model.", "local_vlm_mmproj_path": "Vision projector (mmproj) for the VLM.",
    "ollama_url": "Ollama server URL (used if running).", "require_approval": "Ask before every action with side effects.",
    "autonomy_level": "0 chat only · 1 suggest · 2 auto LOW · 3 auto LOW+MEDIUM (default) · 4 HIGH via explicit rules/grants.",
    "pc_control_enabled": "Allow window/input/app control tools.", "browser_enabled": "Allow browser tools.", "terminal_enabled": "Allow terminal/dev tools.",
    "allowed_roots": "Folders SCAR may work in (`;`-separated). Default: your user folder.",
    "protected_paths": "Extra protected folders (writes CRITICAL, reads HIGH).", "dry_run": "Report side-effecting actions instead of doing them.",
    "kill_switch_hotkey": "Emergency-stop hotkey.", "approval_timeout": "Seconds before an unanswered approval is denied.",
    "bulk_threshold": "File count above which bulk operations escalate one risk level.",
    "max_local_vram": "MiB of VRAM local models may use.", "max_local_ram": "MiB of RAM local models may use.",
    "gpu_usage_threshold": "Don't start GPU inference above this GPU utilisation (%).",
    "background_task_policy": "`minimal`, `balanced`, `performance`: concurrency and monitor limits.",
    "model_idle_timeout": "Seconds before idle local models/components are unloaded.",
    "local_inference_policy": "`never`, `fallback_only` (default), `prefer`, `always`.",
    "battery_saver_threshold": "On battery below this %, no GPU inference.",
    "privacy_mode": "`open`, `balanced` (default: redact before cloud), `strict` (sensitive classes local only).",
    "privacy_overrides": "Per data class: `screen=local_only,email=cloud_redacted` (classes: general, screen, audio, email, messages, files, contacts, memory, clipboard).",
    "max_steps": "Max agent steps per task.", "max_retries": "Max retries per step.", "task_timeout": "Wall-clock limit per task (s).",
    "task_token_budget": "Token budget per task.", "command_timeout": "Default command timeout (s).", "command_timeout_max": "Hard ceiling for commands (s).",
    "output_cap_bytes": "Captured output kept in memory per stream; the rest goes to an artifact.",
    "max_concurrent_tasks": "Concurrent tasks.", "max_subagents": "Concurrent sub-agents per task.",
    "browser_channel": "`auto`, `chrome`, `msedge`, `chromium`.", "browser_headless": "Run the SCAR browser headless.",
    "downloads_dir": "Where browser downloads go (never executed).",
    "google_oauth_client_file": "Path to the Google OAuth client JSON (Desktop app).", "ms_client_id": "Azure app (client) id for Microsoft Graph.",
    "ms_tenant": "`common`, `consumers`, `organizations` or a tenant id.", "email_provider": "`auto`, `gmail`, `outlook`, `imap`.",
    "imap_host": "IMAP server.", "imap_port": "IMAP port.", "imap_user": "IMAP/SMTP user name.", "smtp_host": "SMTP server.", "smtp_port": "SMTP port.",
    "calendar_provider": "`auto`, `google`, `outlook`, `local`.", "telegram_api_id": "Telegram API id (user client).",
    "discord_default_channel": "Default Discord channel id.", "whatsapp_desktop_enabled": "Opt-in WhatsApp Desktop provider (UI automation).",
    "whatsapp_phone_number_id": "WhatsApp Cloud API phone number id.",
    "test_telegram_chat": "Only chat live tests may send to.", "test_email_to": "Only address live tests may email.",
    "test_discord_channel": "Only channel live tests may post to.", "log_level": "DEBUG/INFO/WARNING/ERROR.",
    "data_dir": "Data folder (default %LOCALAPPDATA%\\SCAR).",
    "models_dir": "Downloaded speech, embedding and wake-word models (default <data folder>\\models); can live on another drive.",
    "live_tests": "Enable live desktop tests (tests only).",
    "debug": "Show internals.", "verbose": "Show tools and timings.", "timezone": "IANA zone for reminders (default: system).",
}

SECRET_DOCS = {
    "GROQ_API_KEY": "Groq (free tier: reasoning + Whisper STT)", "GEMINI_API_KEY": "Google AI Studio key (free tier; see data terms)",
    "GOOGLE_API_KEY": "Alternative name for the Gemini key", "OPENROUTER_API_KEY": "OpenRouter (:free models)",
    "CEREBRAS_API_KEY": "Cerebras", "MISTRAL_API_KEY": "Mistral La Plateforme", "NVIDIA_API_KEY": "NVIDIA API catalog",
    "CLOUDFLARE_API_TOKEN": "Cloudflare Workers AI (also set CLOUDFLARE_ACCOUNT_ID)", "HF_TOKEN": "Hugging Face Inference Providers",
    "GITHUB_TOKEN": "GitHub fine-grained token for issues/PRs/CI", "AZURE_SPEECH_KEY": "Azure Speech (also AZURE_SPEECH_REGION)",
    "TELEGRAM_BOT_TOKEN": "Telegram bot", "TELEGRAM_API_HASH": "Telegram user client (with SCAR_TELEGRAM_API_ID)",
    "DISCORD_BOT_TOKEN": "Discord bot", "BRAVE_API_KEY": "Brave Search", "TAVILY_API_KEY": "Tavily search",
    "WHATSAPP_CLOUD_TOKEN": "WhatsApp Cloud API", "ANTHROPIC_API_KEY": "Optional paid provider", "OPENAI_API_KEY": "Optional paid provider",
    "SCAR_IMAP_PASSWORD": "IMAP app password", "SCAR_SMTP_PASSWORD": "SMTP app password",
}


def main() -> None:
    fields = Settings.model_fields
    missing = [f for f in fields if f not in DESCRIPTIONS]
    if missing:
        raise SystemExit(f"undocumented settings: {missing}")
    rows = []
    env_lines = ["# SCAR configuration. Copy to .env (never commit it) or set these as environment variables.",
                 "# Non-secret settings can also live in %APPDATA%\\SCAR\\config.toml (`scar config set KEY VALUE`).",
                 "# Precedence: CLI flags > environment > .env > config.toml > defaults.", ""]
    for name, f in fields.items():
        default = f.default_factory() if f.default_factory is not None else f.default  # type: ignore[call-arg]
        rows.append(f"| `SCAR_{name.upper()}` | `{default!r}` | {DESCRIPTIONS[name]} |")
        env_lines.append(f"# {DESCRIPTIONS[name]}")
        shown = ",".join(default) if isinstance(default, list) else ("" if default in (None, {}) else str(default).lower() if isinstance(default, bool) else default)
        env_lines.append(f"# SCAR_{name.upper()}={shown}")
    env_lines += ["", "# ---- Secrets (prefer `scar config set-secret NAME`, stored in Windows Credential Manager) ----"]
    for key in sorted(SECRET_KEYS | {"CLOUDFLARE_ACCOUNT_ID", "AZURE_SPEECH_REGION"}):
        env_lines.append(f"# {SECRET_DOCS.get(key, key)}")
        env_lines.append(f"{key}=")
    (ROOT / ".env.example").write_text("\n".join(env_lines) + "\n", encoding="utf-8")
    cfg = ROOT / "docs" / "configuration.md"
    text = cfg.read_text(encoding="utf-8") if cfg.exists() else ""
    marker = "<!-- settings-table -->"
    table = "\n".join(["| Variable | Default | Meaning |", "|---|---|---|", *rows])
    if marker in text:
        head, _, rest = text.partition(marker)
        _, _, tail = rest.partition("<!-- /settings-table -->")
        text = f"{head}{marker}\n{table}\n<!-- /settings-table -->{tail}"
        cfg.write_text(text, encoding="utf-8")
    reg = build_registry(None)
    lines = ["| Tool | Risk (base) | Side effects | Capabilities | Description |", "|---|---|---|---|---|"]
    for t in sorted(reg.all(), key=lambda t: t.name):
        lines.append(f"| `{t.name}` | {t.base_risk.name} | {t.side_effects.value} | {', '.join(t.capabilities)} | "
                     f"{t.description.replace('|', '/')} |")
    tools = ROOT / "docs" / "tools.md"
    ttext = tools.read_text(encoding="utf-8") if tools.exists() else ""
    tm = "<!-- tool-catalog -->"
    if tm in ttext:
        head, _, rest = ttext.partition(tm)
        _, _, tail = rest.partition("<!-- /tool-catalog -->")
        tools.write_text(f"{head}{tm}\n" + "\n".join(lines) + f"\n<!-- /tool-catalog -->{tail}", encoding="utf-8")
    print(f"{len(rows)} settings, {len(reg.all())} tools")


if __name__ == "__main__":
    main()
