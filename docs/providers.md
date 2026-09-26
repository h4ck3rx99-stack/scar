# Providers

SCAR is **cloud-first, free-first, local fallback, degraded last**. Paid providers are optional and never required.
Users never need to know which provider served a request; `--debug` shows fallbacks and `scar status` shows health
and where data went.

## Free-tier evaluation (checked 2026-09-26)

| Provider | Free offer (as published on the date checked) | Tool calling | Vision | Data terms (free tier) | Key |
|---|---|---|---|---|---|
| **Groq** | Free plan, org-level limits. `openai/gpt-oss-120b` and `gpt-oss-20b`: 30 RPM, 1K RPD, 8K TPM, 200K TPD. `qwen/qwen3.8-27b`: same limits. `whisper-large-v3(-turbo)`: 20 RPM, 2K RPD | yes | qwen3.8-27b (unconfirmed) | Groq states inputs are not used for training | `GROQ_API_KEY` |
| **Google Gemini** | Free tier via AI Studio; limits shown per project in AI Studio. Current models: `gemini-3.8-flash`, `3.7-flash`, `3.6-flash`, `3.5-flash`, `3.5-flash-lite`, `3.1-flash-lite` | yes | yes | **Unpaid services: Google uses submitted content and responses to improve its products.** Use `SCAR_PRIVACY_MODE=strict` or overrides for sensitive data | `GEMINI_API_KEY` |
| **Cerebras** | Free trial: `gpt-oss-120b`, `qwen-3.8-27b`, 5 RPM, 1M TPD; $5 credits after adding a verified card, expiring after 30 days | yes | qwen (2 images/request) | trial terms | `CEREBRAS_API_KEY` |
| **OpenRouter** | `:free` models: 20 RPM; 50 requests/day (1000/day after buying ≥$10 credit) | model-dependent | model-dependent | free endpoints may log prompts | `OPENROUTER_API_KEY` |
| **Mistral** | "Experiment" free plan for evaluation; low rate limits shown in the admin console | yes | pixtral / medium | may be used for training unless opted out | `MISTRAL_API_KEY` |
| **NVIDIA API catalog** | developer access with trial credits | yes | some models | trial terms | `NVIDIA_API_KEY` |
| **Cloudflare Workers AI** | daily free allocation (neurons); OpenAI-compatible endpoint | yes | some models | Cloudflare terms | `CLOUDFLARE_API_TOKEN` + `CLOUDFLARE_ACCOUNT_ID` |
| **Hugging Face Inference Providers** | small monthly free credit, routed to third-party providers | provider-dependent | provider-dependent | third-party terms | `HF_TOKEN` |
| **GitHub Models** | **Retired 2026-07-30** (GitHub docs), so not included | — | — | — | — |
| **Tavily** (search) | 1,000 API credits/month, no card; basic search = 1 credit | — | — | — | `TAVILY_API_KEY` |
| **Brave Search** | $5 monthly credit (≈1,000 queries); card required for identity only | — | — | — | `BRAVE_API_KEY` |
| **Azure Speech** (TTS) | F0 free tier | — | — | Microsoft terms | `AZURE_SPEECH_KEY` + `AZURE_SPEECH_REGION` |
| **edge-tts** (TTS) | keyless, **unofficial** interface to Edge's read-aloud voices; may break without notice | — | — | Microsoft service | none |
| **ddgs** (search) | keyless, **unofficial** DuckDuckGo scraper; may rate-limit | — | — | — | none |

Model names change often. SCAR resolves every configured candidate against each provider's live `/models` list
(cached 6 h) and skips models that are no longer offered. The example `llama-3.2-90b-vision-preview` from earlier
planning is not used.

## Local providers

| Provider | Role | Notes |
|---|---|---|
| **llama.cpp `llama-server`** | reasoning, fast, vision (with `--mmproj`) | SCAR reuses a healthy server at `SCAR_LOCAL_LLM_URL` and never kills it. Otherwise it starts one on demand with `SCAR_LOCAL_LLM_MODEL_PATH` (and the VLM paths), with `-ngl` from free VRAM, `--jinja` tool templates, a bounded context, `/health` checks, and a stop after `SCAR_MODEL_IDLE_TIMEOUT`. The installed winget build uses Vulkan. Verified on this machine with Ollama's `qwen3:8b` GGUF (`-ngl 99`) and `llava` + projector. |
| **Ollama** | reasoning/fast/vision | Used only if it is already running (SCAR never starts or stops the user's Ollama). Uses native `/api/chat` with `num_ctx`, and unloads models SCAR used when idle (`keep_alive: 0`). |
| **LM Studio** | optional | OpenAI-compatible at `127.0.0.1:1234` if configured in `providers.yaml`. |
| **faster-whisper** | STT | CPU int8, `SCAR_LOCAL_STT_MODEL` (default `base`), downloaded on first use to `<data>/models/whisper`. |
| **Windows SAPI** | TTS | built in, offline. |
| **Kokoro ONNX** | TTS | optional extra (`uv sync --extra kokoro`), GPL phonemizer dependency (ADR 0007). |
| **fastembed** | embeddings | `BAAI/bge-small-en-v1.5`, CPU. |

Local inference obeys `SCAR_LOCAL_INFERENCE_POLICY` (`never`, `fallback_only` [default], `prefer`, `always`) and
resource admission. There is no GPU inference above `SCAR_GPU_USAGE_THRESHOLD`, with less than 1 GiB of free VRAM,
or on battery below `SCAR_BATTERY_SAVER_THRESHOLD`. There is no CPU inference when RAM or CPU is short.

## Fallback chains (defaults in `src/scar/config/providers_default.yaml`)

* **reasoning**: groq → gemini → cerebras → openrouter → mistral → nvidia → cloudflare → huggingface → (paid: anthropic, openai) → ollama → llamacpp
* **fast**: groq gpt-oss-20b → gemini flash-lite → cerebras → openrouter → ollama → llamacpp
* **vision**: gemini → groq qwen3.8 → openrouter free VLMs → mistral → ollama llava → llamacpp VLM → OCR + UIA only
* **stt**: groq whisper-large-v3-turbo → faster-whisper (local)
* **tts**: azure → edge-tts → SAPI → kokoro → text only
* **search**: brave → tavily → searxng → ddgs → cached results → "search unavailable"
* **embeddings**: fastembed (local)

Providers without credentials are skipped immediately. `SCAR_LLM_PROVIDER`, `SCAR_LLM_MODEL` and `SCAR_LLM_FALLBACKS`
put your choices first.

## Error handling

| Error | What the router does |
|---|---|
| missing credentials / auth failure | marks the provider unavailable until configuration changes; falls through immediately |
| 429 rate limit | parses `Retry-After`, `retry-after-ms`, `x-ratelimit-reset-*` or Gemini `retryDelay`; sets a cooldown; falls over immediately if an alternative exists; if the only candidates are briefly rate-limited (≤15 s), waits once |
| quota exhausted | long cooldown (≥1 h); falls through |
| timeout / network / 5xx | one retry with jitter, then falls through; the circuit breaker opens after 3 failures (60 s) |
| model unavailable | removed for the session; tries the provider's next model |
| malformed response / invalid tool call | the agent re-prompts once with the error; repeated malformed output penalises the provider so the next one is used |
| context overflow | the context is compacted once and retried, then the next candidate is tried |
| privacy class local-only | cloud candidates are skipped |

Health (cooldowns, circuit state, latency EWMA) persists in SQLite across restarts. When nothing is reachable,
SCAR says so plainly, keeps the fast path working, and `scar doctor` explains how to fix it.

## Setup

1. Pick at least one free cloud provider, for example Groq: create a key at console.groq.com → API Keys.
2. `scar config set-secret GROQ_API_KEY` (stored in Windows Credential Manager), or put it in `.env`.
3. `scar providers test groq` (sends a tiny prompt) and `scar doctor` (reachability plus which configured models are live).

To add a provider, add it to `%APPDATA%\SCAR\providers.yaml`:

```yaml
providers:
  mylocal:
    kind: openai_compat          # openai_compat | gemini | anthropic | ollama | llamacpp
    base_url: http://127.0.0.1:5000/v1
    tier: local
    privacy: local
categories:
  reasoning:                      # replaces the default chain for this category
    - {provider: mylocal, models: [my-model], local: true, size_mb: 4000}
    - {provider: groq, models: [openai/gpt-oss-120b]}
```

### Vision

Cloud: set `GEMINI_API_KEY` (or OpenRouter/Mistral). Local: set `SCAR_LOCAL_VLM_MODEL_PATH` and
`SCAR_LOCAL_VLM_MMPROJ_PATH` to a GGUF VLM and its projector. For example, Ollama's llava blobs work; `scar doctor`
lists them. Without either, screen questions are answered from UI Automation and OCR only.

### Paid providers

`ANTHROPIC_API_KEY` or `OPENAI_API_KEY` add paid candidates after the free cloud ones. They are never required.
