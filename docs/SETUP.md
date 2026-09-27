# SCAR setup checklist

Everything you need to do, in order. `scar doctor` checks each item and tells you what is missing.
Secrets always go in Windows Credential Manager via `uv run scar config set-secret NAME` (it prompts; nothing is
written to disk in clear). Settings go in `%APPDATA%\SCAR\config.toml` via `uv run scar config set name value`, or
as `SCAR_*` environment variables. The full list is in [configuration.md](configuration.md).

## 1. Required

| What | Where to get it | How to check |
|---|---|---|
| Windows 11 | — | `uv run scar doctor` → System |
| Python 3.11 | `winget install Python.Python.3.11` | `python --version` |
| uv | `winget install astral-sh.uv` | `uv --version` |
| SCAR's dependencies | in the repository folder: `uv sync` | `uv run scar doctor` → "Python dependencies installed" |
| **One model source** (step 2) | cloud key *or* a local model | `uv run scar providers test <name>` |

SCAR does **not** need administrator rights, and should not be run elevated (doctor flags it).

## 2. A model: at least one of these

**Free cloud (recommended; fastest).** Any one key is enough. With several, SCAR falls back between them.

| Provider | Get a key | Store it | Check |
|---|---|---|---|
| Groq | https://console.groq.com/keys | `uv run scar config set-secret GROQ_API_KEY` | `uv run scar providers test groq` |
| Gemini | https://aistudio.google.com/apikey | `uv run scar config set-secret GEMINI_API_KEY` | `uv run scar providers test gemini` |
| Cerebras, OpenRouter, Mistral, NVIDIA, Cloudflare, Hugging Face | see [providers.md](providers.md) | `set-secret` with the key name shown by `scar doctor` | `providers test <name>` |

Gemini's unpaid tier may use your prompts to improve Google's products. [providers.md](providers.md) lists each
provider's data terms, and `scar status` shows which cloud providers received data in the current session.

**Local (private, offline, slower).** Pick one:

* **llama.cpp managed by SCAR** (what this machine uses):
  1. `winget install ggml.llamacpp` (provides `llama-server`).
  2. Download a GGUF model, e.g. `Qwen3-8B-Q4_K_M.gguf` from https://huggingface.co/Qwen/Qwen3-8B-GGUF.
  3. `uv run scar config set local_llm_model_path C:\models\Qwen3-8B-Q4_K_M.gguf`
  4. Optional, for screen questions without a cloud key: a vision GGUF with its projector, set as
     `local_vlm_model_path` and `local_vlm_mmproj_path`.
  5. Check: `uv run scar doctor` → Local Models. SCAR starts the server on demand, sizes the GPU offload to free
     VRAM, and unloads it after 5 idle minutes.
* **Ollama**: install from https://ollama.com, then `ollama pull qwen3:8b` (optional: `ollama pull llava` for
  vision). Ollama must be running; doctor shows "Ollama reachable".

Without any model SCAR runs in degraded mode. Quick commands still work: open apps and folders, screenshots,
system info, reminders, memory, running tests.

## 3. Recommended

| What | Why | How |
|---|---|---|
| Background daemon | reminders, monitors and schedules keep running; the model stays warm between commands | `uv run scar daemon start`; at logon: `uv run scar daemon install` |
| Windows OCR language pack | reading the screen | Settings → Time & language → Language → your language → Optical character recognition. Check: doctor → "Windows OCR available" |
| Chrome or Edge | browser automation uses your installed browser | already installed here. Otherwise: `uv run playwright install chromium` |

## 4. Voice (optional)

1. `uv run scar voice devices`: confirm your microphone and speakers. Set others with
   `uv run scar config set mic_device "<name>"` and `speaker_device`.
2. `uv run scar voice test`: records 4 s, transcribes, and speaks the text back. The first run downloads
   faster-whisper `base` (~150 MB) into the models folder.
3. `uv run scar --voice`: push-to-talk with **Ctrl+Alt+Space**. For the wake word ("hey jarvis"), also run
   `uv run scar config set voice_enabled true`; its model downloads on first use.
   Speech output uses edge-tts (online), falling back to Windows SAPI (offline).

## 5. Communication and calendars (optional; each needs your own account)

SCAR never sends anything without asking. Sending is HIGH risk and needs your approval every time, and recipients
must be people you named.

| Service | You need | Put it | Guide | Check |
|---|---|---|---|---|
| Gmail + Google Calendar | a Google Cloud OAuth client (Desktop app) JSON file | `uv run scar config set google_oauth_client_file C:\path\client.json`, then `uv run scar auth google` | [gmail.md](integrations/gmail.md) | "check my email" |
| Outlook + Outlook Calendar | an Azure app registration (public client) ID | `uv run scar config set ms_client_id <id>`, then `uv run scar auth microsoft` | [outlook.md](integrations/outlook.md) | "check my email" |
| Other email (IMAP/SMTP) | server names, user, app password | `config set imap_host / imap_user / smtp_host`; `set-secret SCAR_IMAP_PASSWORD` (and `SCAR_SMTP_PASSWORD` if different) | [gmail.md](integrations/gmail.md#imap-smtp-alternative) | "check my email" |
| Telegram (bot) | a bot token from @BotFather | `set-secret TELEGRAM_BOT_TOKEN`; press Start in your bot's chat | [telegram.md](integrations/telegram.md) | "show recent telegram messages" |
| Telegram (your account) | api_id/api_hash from https://my.telegram.org | `config set telegram_api_id <id>`; `set-secret TELEGRAM_API_HASH`; `uv run scar auth telegram` | [telegram.md](integrations/telegram.md) | same |
| Discord | a **bot** token (user-account automation is not supported) | `set-secret DISCORD_BOT_TOKEN`; invite the bot to your server | [discord.md](integrations/discord.md) | "post … to #channel" (asks first) |
| WhatsApp | the WhatsApp desktop app signed in, **or** a Cloud API number | `config set whatsapp_desktop_enabled true`, or `whatsapp_phone_number_id` + `set-secret WHATSAPP_CLOUD_TOKEN` | [whatsapp.md](integrations/whatsapp.md) | — |
| Calendar without an account | an .ics file | "import calendar.ics" | [calendar.md](integrations/calendar.md) | "what's on my calendar" |
| GitHub | a fine-grained personal access token | `set-secret GITHUB_TOKEN` | [github.md](integrations/github.md) | "list open issues in owner/repo" |

Without any of these, SCAR says which one is missing and how to connect it. It never pretends to have read your
calendar or email.

## 6. Optional extras

* Better web search: `set-secret BRAVE_API_KEY` or `TAVILY_API_KEY`, or `config set searxng_url <url>`. Without
  them SCAR uses the unofficial `ddgs` search.
* Faster file search: Windows Search indexing (on by default) or Everything's `es.exe` on PATH.
* Models on another drive: `uv run scar config set models_dir D:\scar-models`.

## Verify everything

```powershell
uv run scar doctor           # every check, with the fix for each problem
uv run scar providers list   # which model providers are configured
uv run scar "what's using my RAM"
```
