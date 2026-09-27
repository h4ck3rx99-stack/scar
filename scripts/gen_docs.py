"""Regenerate docs that are derived from code: docs/tools.md catalog section, docs/configuration.md table, .env.example.

    uv run python scripts/gen_docs.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from scar.config.descriptions import DESCRIPTIONS  # noqa: E402
from scar.config.settings import SECRET_KEYS, Settings  # noqa: E402
from scar.runtime.registration import build_registry  # noqa: E402

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
