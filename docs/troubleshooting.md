# Troubleshooting

Start with `scar doctor`. Every ⚠/✗ line has a hint. `scar doctor --deep` also sends a tiny prompt to each configured provider.

| Symptom | Likely cause | Fix |
|---|---|---|
| "no language model is reachable" | no cloud key and no local model | add a free key (`scar config set-secret GROQ_API_KEY`), or start Ollama, or set `SCAR_LOCAL_LLM_MODEL_PATH` to a GGUF. Quick commands (open apps/folders, screenshots, reminders…) keep working. |
| "request was too large for the language model" | small local context | raise `SCAR_LOCAL_LLM_CTX` (default 16384), or use a cloud provider |
| ⚠ "provider cooling down (rate limited, retry in 42s)" | free-tier limits | wait, or add a second free provider; SCAR fails over automatically |
| local model slow or not on GPU | GPU busy, low VRAM, or on battery | `scar status` shows the admission reason; see `SCAR_GPU_USAGE_THRESHOLD`, `SCAR_MAX_LOCAL_VRAM`, `SCAR_BATTERY_SAVER_THRESHOLD`. The llama-server log is `<logs>/llama-server-llm.log`. |
| "Not approved: no interactive approval channel" | the action needed approval but nobody could answer (one-shot without a terminal, or the daemon without a client) | run it in the REPL, or allow it via `policy.yaml` / a grant |
| "Not allowed: …hard-denied…" | a protected category (Defender, firewall, credentials, secret files…) | this is intentional; see [security.md](security.md) |
| typing/clicking aborted "focus moved away" | the target window lost focus | re-run; SCAR never types into another window |
| "runs as administrator … UIPI" | the target app is elevated | run the app non-elevated; SCAR never self-elevates |
| OCR unavailable {#ocr} | no Windows OCR language pack | Settings → Time & language → Language → add the language's optional features |
| screen questions don't use vision {#screen} | no vision provider | set `GEMINI_API_KEY`, or `SCAR_LOCAL_VLM_MODEL_PATH` + `SCAR_LOCAL_VLM_MMPROJ_PATH` |
| Windows automation errors {#windows-automation} | UI Automation or COM failure, or DPI issues | `scar doctor` → Windows section; SCAR sets per-monitor-v2 DPI awareness at start |
| browser won't start | channel missing | `uv run playwright install chromium`, or `SCAR_BROWSER_CHANNEL=msedge` |
| voice: nothing heard | wrong microphone | `scar voice devices`, `SCAR_MIC_DEVICE=<name>`, `scar voice test` |
| voice: hotkey unavailable | another app owns the combination | change `SCAR_PTT_HOTKEY` / `SCAR_KILL_SWITCH_HOTKEY` |
| "Another SCAR runtime is already running" | a daemon or another REPL holds the lock | `scar daemon status` / `scar daemon stop` |
| database corrupt | disk error or crash | SCAR backs it up as `scar.corrupt-<time>.db`, recreates it, and reports this at startup |
| reminder didn't fire while the PC was off | the daemon wasn't running | missed reminders are reported on the next start; `scar daemon install` starts SCAR at logon |
| config error on start | invalid value | the message names the variable; `scar config validate` |
| email/messages "unavailable" | integration not set up | the message names the missing credential and the setup page in `docs/integrations/` |

Logs: `scar logs [-f] [--level warning] [--task <id>]` (JSON lines in `%LOCALAPPDATA%\SCAR\logs`, secrets redacted).
Per-task traces: `scar logs --task <task_id>`, or `scar tasks show <task_id>` for every tool call with its risk,
decision, status and verification.
