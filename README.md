# Scar — local 3D desktop assistant

A flying robot that lives on your Windows desktop, talks like Alexa and acts like Jarvis.
Everything runs on this PC (speech, brain, voice, vision). Only weather and Wikipedia look things up online,
using free public services that need no account or key.

## Start

- **`Start_Scar.bat`**: double-click to start. It stops any old copy, rebuilds the UI, and launches. After about 10 s the robot flies in and says "Scar online".
- **`Stop_Scar.bat`**: stops everything. The tray icon (yellow dot) or right-click on the robot → Quit also work.
- **`Start_Scar_Debug.bat`**: the same, but the brain runs in a visible window, so you can see exactly what it heard and did.

Weather uses your internet location. On slow Wi-Fi you can pin a city in `backend/.env`: `JARVIS_CITY=Bengaluru`.

## Talking to it

| How | What happens |
|---|---|
| **"Scar"** or **"Hey Scar"** (also "Hey Jarvis") | it listens (cyan ring) |
| **"Scar, open YouTube"** in one breath | runs straight away |
| Click the robot | same as saying its name |
| Right-click → type in the box | typed command |
| Within **5 s** after it answers | just talk, no name needed (background chatter is ignored) |
| **"Go on standby"** | flies off-screen and stays silent until you say "Scar" |
| "Go to sleep" / 3 min idle | naps peeking from the screen edge |
| "Stop" / "never mind" | stops talking |

## What it can do

- **Windows:** minimize / maximize / close / restore / switch to any app ("close it"); snap left/right/corners/thirds/other screen; resize to 16:9, 4:3, bigger, smaller; "put windows side by side"; "minimize everything". It flies to the real button, presses it, and the window reacts like rubber.
- **Open things:** apps, websites ("open YouTube", "go to github.com"), searches ("search YouTube for lofi", "play interstellar on YouTube"), folders ("open downloads"), files ("find file budget", "open my latest download").
- **Browser tabs:** "close this tab", "new tab", "switch to the Gmail tab".
- **Eyes:** "what's on my screen?", "explain this error", "what does this code do?" (it looks at a screenshot); "read this"; "summarize my clipboard", "explain the selection".
- **Typing:** "type hello world".
- **PC:** "how's my system?", "battery", "what's using my memory?", "volume 40", "brightness 70", "mute", "next song", "take a screenshot", "lock my PC". It warns you when the battery hits 20% and 10%.
- **Time:** "set a timer for 5 minutes", "remind me to drink water in 20 minutes", "wake me up at 7 am", "what are my reminders?", "cancel timers".
- **Notes & lists:** "take a note: …", "read my notes", "add eggs to my shopping list", "what's on my list?", "check off eggs".
- **Memory:** "remember that my name is Avi", "forget that…". Remembered facts are used in every conversation.
- **Info:** anything general (local model), "what's the weather?", "weather in Mumbai", "who was Nikola Tesla?", exact maths ("17 percent of 2340"), "good morning" briefing, time and date.
- **Look:** dark/light theme follows Windows ("light mode" to switch); size slider in the top-left dock; expressive face with lip-sync.

## Settings (`backend/.env`, all optional)

```
JARVIS_NAME=Scar                    # assistant's name = wake name
JARVIS_FOLLOW_UP_MS=5000            # hands-free window after each answer
JARVIS_SLEEP_AFTER_S=180            # idle time before napping at the screen edge
JARVIS_INPUT_DEVICE=                # mic name (default: Windows default mic)
JARVIS_VOICE=am_michael             # Kokoro voice, e.g. af_heart, bm_george
JARVIS_WHISPER_MODEL=               # default large-v3-turbo; backend/models/whisper/small.en is lighter
JARVIS_REAL_CLICK=false             # true = move the real mouse and click, instead of a direct Windows call
JARVIS_OFFLINE=false                # true = no weather/Wikipedia lookups at all

# Optional cloud brain (OpenAI-compatible, e.g. Groq / Gemini / OpenRouter free tiers).
# Falls back to the local model if it fails. Your speech text is sent to that provider.
JARVIS_CLOUD_LLM_URL=
JARVIS_CLOUD_LLM_KEY=
JARVIS_CLOUD_LLM_MODEL=
```

Data lives in `%APPDATA%\jarvis` (notes, to-dos, reminders, remembered facts). Screenshots go to `Pictures\Jarvis`.

## Under the hood

- `frontend/` — Electron overlay + Three.js robot (`src/main.js`, `assistantController.js`, `scarFace.js`, `clothProxy.js`)
- `backend/jarvis/` — Python brain: `audio/` (mic, Silero VAD, wake word), `stt/` (faster-whisper large-v3-turbo),
  `tts/` (Kokoro), `brain/` (router → Qwen3.5-4B via llama.cpp with vision), `hands/` (window control), `skills/`
- `contracts/protocol.json` — the message contract between the two; `backend/tests` checks both sides use it

Rebuild the robot models after editing `assets/`: `cd frontend && npm run build:avatar`.
Run tests: `cd backend && .venv\Scripts\python -m pytest -q`.
Logs: `backend/logs/jarvis.log`, per-turn timings in `backend/logs/latency.csv`.
