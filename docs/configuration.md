# Configuration

SCAR reads configuration from, highest precedence first:

1. **CLI flags** (`--autonomy 2`, `--dry-run`, `--verbose`, `--debug`)
2. **Environment variables** (`SCAR_*`)
3. **`.env`** in the current folder
4. **User config file** `%APPDATA%\SCAR\config.toml` (`scar config path` shows it)
5. **Built-in defaults**

Values are validated when loaded; an invalid value stops SCAR with a message naming the variable.

```
scar config show            # effective settings (secrets never shown, only where each is set)
scar config set KEY VALUE   # writes config.toml, validated first (e.g. scar config set autonomy_level 2)
scar config validate        # settings + policy.yaml + providers.yaml
scar config path            # where config, policy, providers, data and logs live
scar config set-secret NAME # store an API key/token in Windows Credential Manager
```

**Secrets never go in `config.toml`.** SCAR refuses to start if the file contains a secret-looking key.
Secrets come from environment variables, `.env`, or Windows Credential Manager (`scar config set-secret`).
The full list with comments is in [`.env.example`](../.env.example).

## Other files

| File | Purpose |
|---|---|
| `%APPDATA%\SCAR\policy.yaml` | Your permission rules, merged over `src/scar/config/policy_default.yaml` (see [security.md](security.md)). The only way to disable a hard-deny protection. |
| `%APPDATA%\SCAR\providers.yaml` | Provider catalog overrides: add providers or reorder fallback chains (see [providers.md](providers.md)). |
| `%LOCALAPPDATA%\SCAR\` | Data: `scar.db` (SQLite, WAL), `artifacts/`, `backups/`, `browser-profile/`, `downloads/`, `models/`, `logs/`, `ipc.json` (daemon token, user-only ACL). |

## All settings

<!-- settings-table -->
| Variable | Default | Meaning |
|---|---|---|
| `SCAR_LLM_PROVIDER` | `'auto'` | Preferred reasoning provider id (groq, gemini, cerebras, openrouter, mistral, nvidia, cloudflare, huggingface, anthropic, openai, ollama, llamacpp) or `auto`. |
| `SCAR_LLM_MODEL` | `''` | Model for SCAR_LLM_PROVIDER (otherwise the catalog's candidates). |
| `SCAR_LLM_FALLBACKS` | `[]` | Ordered extra candidates, `provider[:model]`, comma-separated. |
| `SCAR_FAST_LLM_PROVIDER` | `'auto'` | Provider for quick/small tasks (chat replies). |
| `SCAR_FAST_LLM_MODEL` | `''` | Model for the fast provider. |
| `SCAR_VISION_PROVIDER` | `'auto'` | Preferred vision provider or `auto`. |
| `SCAR_VISION_MODEL` | `''` | Vision model override. |
| `SCAR_STT_PROVIDER` | `'auto'` | `auto`, `groq` or `faster-whisper`. |
| `SCAR_STT_MODEL` | `''` | STT model override. |
| `SCAR_LOCAL_STT_MODEL` | `'base'` | faster-whisper size (tiny, base, small, medium). |
| `SCAR_TTS_PROVIDER` | `'auto'` | `auto`, `azure-speech`, `edge-tts`, `sapi`, `kokoro` or `none`. |
| `SCAR_TTS_VOICE` | `''` | Voice name (e.g. en-US-AriaNeural, or part of a SAPI voice name). |
| `SCAR_VOICE_ENABLED` | `False` | Enable hands-free wake-word mode in voice sessions. |
| `SCAR_WAKE_WORD` | `'hey_jarvis'` | openWakeWord model: hey_jarvis, alexa, hey_mycroft, hey_rhasspy. |
| `SCAR_WAKE_WORD_THRESHOLD` | `0.5` | Wake-word score threshold (0-1). |
| `SCAR_PTT_HOTKEY` | `'<ctrl>+<alt>+<space>'` | Push-to-talk global hotkey (pynput syntax). |
| `SCAR_MIC_DEVICE` | `''` | Microphone: index or name substring (empty = default). |
| `SCAR_SPEAKER_DEVICE` | `''` | Speaker: index or name substring (empty = default). |
| `SCAR_VOICE_SESSION_TIMEOUT` | `30.0` | Seconds of follow-up listening after a reply. |
| `SCAR_MAX_UTTERANCE_SECONDS` | `20.0` | Longest single utterance. |
| `SCAR_VOICE_APPROVAL_WINDOW` | `8.0` | Seconds SCAR listens for an approval answer. |
| `SCAR_EMBEDDINGS_PROVIDER` | `'fastembed'` | `fastembed` (local) or `none`. |
| `SCAR_EMBEDDINGS_MODEL` | `'BAAI/bge-small-en-v1.5'` | fastembed model id. |
| `SCAR_SEARCH_PROVIDER` | `'auto'` | `auto`, `brave`, `tavily`, `searxng`, `ddgs`. |
| `SCAR_SEARXNG_URL` | `''` | Base URL of a SearXNG instance. |
| `SCAR_MEMORY_BACKEND` | `'faiss'` | `faiss` (hybrid) or `fts_only` (keyword only, no embeddings). |
| `SCAR_LOCAL_LLM_URL` | `'http://127.0.0.1:8080'` | llama.cpp server URL to reuse or start. |
| `SCAR_LOCAL_LLM_SERVER_BIN` | `''` | Path to llama-server.exe (default: PATH). |
| `SCAR_LOCAL_LLM_MODEL_PATH` | `''` | GGUF model SCAR starts llama-server with. |
| `SCAR_LOCAL_LLM_CTX` | `16384` | Context size for the managed server. |
| `SCAR_LOCAL_VLM_MODEL_PATH` | `''` | GGUF vision model. |
| `SCAR_LOCAL_VLM_MMPROJ_PATH` | `''` | Vision projector (mmproj) for the VLM. |
| `SCAR_OLLAMA_URL` | `'http://127.0.0.1:11434'` | Ollama server URL (used if running). |
| `SCAR_REQUIRE_APPROVAL` | `False` | Ask before every action with side effects. |
| `SCAR_AUTONOMY_LEVEL` | `3` | 0 chat only · 1 suggest · 2 auto LOW · 3 auto LOW+MEDIUM (default) · 4 HIGH via explicit rules/grants. |
| `SCAR_PC_CONTROL_ENABLED` | `True` | Allow window/input/app control tools. |
| `SCAR_BROWSER_ENABLED` | `True` | Allow browser tools. |
| `SCAR_TERMINAL_ENABLED` | `True` | Allow terminal/dev tools. |
| `SCAR_ALLOWED_ROOTS` | `[]` | Folders SCAR may work in (`;`-separated). Default: your user folder. |
| `SCAR_PROTECTED_PATHS` | `[]` | Extra protected folders (writes CRITICAL, reads HIGH). |
| `SCAR_DRY_RUN` | `False` | Report side-effecting actions instead of doing them. |
| `SCAR_KILL_SWITCH_HOTKEY` | `'<ctrl>+<alt>+<shift>+k'` | Emergency-stop hotkey. |
| `SCAR_APPROVAL_TIMEOUT` | `300.0` | Seconds before an unanswered approval is denied. |
| `SCAR_BULK_THRESHOLD` | `20` | File count above which bulk operations escalate one risk level. |
| `SCAR_MAX_LOCAL_VRAM` | `6144` | MiB of VRAM local models may use. |
| `SCAR_MAX_LOCAL_RAM` | `8192` | MiB of RAM local models may use. |
| `SCAR_GPU_USAGE_THRESHOLD` | `70` | Don't start GPU inference above this GPU utilisation (%). |
| `SCAR_BACKGROUND_TASK_POLICY` | `'balanced'` | `minimal`, `balanced`, `performance`: concurrency and monitor limits. |
| `SCAR_MODEL_IDLE_TIMEOUT` | `300.0` | Seconds before idle local models/components are unloaded. |
| `SCAR_LOCAL_INFERENCE_POLICY` | `'fallback_only'` | `never`, `fallback_only` (default), `prefer`, `always`. |
| `SCAR_BATTERY_SAVER_THRESHOLD` | `25` | On battery below this %, no GPU inference. |
| `SCAR_PRIVACY_MODE` | `'balanced'` | `open`, `balanced` (default: redact before cloud), `strict` (sensitive classes local only). |
| `SCAR_PRIVACY_OVERRIDES` | `{}` | Per data class: `screen=local_only,email=cloud_redacted` (classes: general, screen, audio, email, messages, files, contacts, memory, clipboard). |
| `SCAR_MAX_STEPS` | `40` | Max agent steps per task. |
| `SCAR_MAX_RETRIES` | `3` | Max retries per step. |
| `SCAR_TASK_TIMEOUT` | `900.0` | Wall-clock limit per task (s). |
| `SCAR_TASK_TOKEN_BUDGET` | `400000` | Token budget per task. |
| `SCAR_COMMAND_TIMEOUT` | `120.0` | Default command timeout (s). |
| `SCAR_COMMAND_TIMEOUT_MAX` | `1800.0` | Hard ceiling for commands (s). |
| `SCAR_OUTPUT_CAP_BYTES` | `65536` | Captured output kept in memory per stream; the rest goes to an artifact. |
| `SCAR_MAX_CONCURRENT_TASKS` | `4` | Concurrent tasks. |
| `SCAR_MAX_SUBAGENTS` | `3` | Concurrent sub-agents per task. |
| `SCAR_BROWSER_CHANNEL` | `'auto'` | `auto` (Opera GX if installed, else Edge, Chrome, Chromium), `opera`, `chrome`, `msedge`, `chromium`. Always a separate SCAR profile. |
| `SCAR_BROWSER_HEADLESS` | `False` | Run the SCAR browser headless. |
| `SCAR_DOWNLOADS_DIR` | `''` | Where browser downloads go (never executed). |
| `SCAR_GOOGLE_OAUTH_CLIENT_FILE` | `''` | Path to the Google OAuth client JSON (Desktop app). |
| `SCAR_MS_CLIENT_ID` | `''` | Azure app (client) id for Microsoft Graph. |
| `SCAR_MS_TENANT` | `'common'` | `common`, `consumers`, `organizations` or a tenant id. |
| `SCAR_EMAIL_PROVIDER` | `'auto'` | `auto`, `gmail`, `outlook`, `imap`. |
| `SCAR_IMAP_HOST` | `''` | IMAP server. |
| `SCAR_IMAP_PORT` | `993` | IMAP port. |
| `SCAR_IMAP_USER` | `''` | IMAP/SMTP user name. |
| `SCAR_SMTP_HOST` | `''` | SMTP server. |
| `SCAR_SMTP_PORT` | `587` | SMTP port. |
| `SCAR_CALENDAR_PROVIDER` | `'auto'` | `auto`, `google`, `outlook`, `local`. |
| `SCAR_TELEGRAM_API_ID` | `''` | Telegram API id (user client). |
| `SCAR_DISCORD_DEFAULT_CHANNEL` | `''` | Default Discord channel id. |
| `SCAR_WHATSAPP_DESKTOP_ENABLED` | `False` | Opt-in WhatsApp Desktop provider (UI automation). |
| `SCAR_WHATSAPP_PHONE_NUMBER_ID` | `''` | WhatsApp Cloud API phone number id. |
| `SCAR_TEST_TELEGRAM_CHAT` | `''` | Only chat live tests may send to. |
| `SCAR_TEST_EMAIL_TO` | `''` | Only address live tests may email. |
| `SCAR_TEST_DISCORD_CHANNEL` | `''` | Only channel live tests may post to. |
| `SCAR_QUICKBAR_HOTKEY` | `'Ctrl+Alt+Space'` | Desktop app: global shortcut that opens the Quick Bar (Tauri accelerator syntax). |
| `SCAR_THEME` | `'system'` | Desktop app theme: `system`, `light` or `dark`. |
| `SCAR_START_MINIMIZED` | `False` | Desktop app: start hidden in the tray. |
| `SCAR_KEEP_RUNNING_IN_BACKGROUND` | `False` | Desktop app: "Quit" leaves the runtime running for reminders and monitors. |
| `SCAR_APP_ONBOARDING_STEP` | `0` | Desktop app: first-run setup progress (0 = not started, 99 = done). |
| `SCAR_CONTROL_INDICATOR` | `True` | Show the on-screen indicator while SCAR controls the mouse, keyboard or windows. |
| `SCAR_GAME_MODE` | `'auto'` | `auto`: while a full-screen game or presentation runs, defer toasts/speech and don't start live automation or local GPU inference unless asked; `off`: ignore. |
| `SCAR_LOG_LEVEL` | `'INFO'` | DEBUG/INFO/WARNING/ERROR. |
| `SCAR_DATA_DIR` | `''` | Data folder (default %LOCALAPPDATA%\SCAR). |
| `SCAR_MODELS_DIR` | `''` | Downloaded speech, embedding and wake-word models (default <data folder>\models); can live on another drive. |
| `SCAR_LIVE_TESTS` | `False` | Enable live desktop tests (tests only). |
| `SCAR_DEBUG` | `False` | Show internals. |
| `SCAR_VERBOSE` | `False` | Show tools and timings. |
| `SCAR_TIMEZONE` | `''` | IANA zone for reminders (default: system). |
<!-- /settings-table -->
