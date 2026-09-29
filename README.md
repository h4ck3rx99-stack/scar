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
| Just talk (mic always on) | when it isn't on standby, a clear command said to it runs even without the name. It checks that the command was meant for it, loud enough, and not its own voice |
| "Always listen" / "only respond when I call you" | switches that on or off |
| "Mute the mic" / right-click → **Mic** | ignores everything until "turn on the microphone" or a click |
| "Do not disturb for 30 minutes" | standby that ends by itself ("I'm back!") |
| Pause mid-sentence ("open Chrome and search about…") | it waits for the rest instead of acting on half a sentence |
| "Say that again" / "do that again" | repeats its last answer / re-runs your last command |

## Scar's own settings

Say them, or right-click the robot → **⚙ Settings** (also "open your settings"). Saved in `%APPDATA%\jarvis\settings.json`.

| Setting | Say for example |
|---|---|
| Size, roaming speed | "make yourself bigger", "shrink", "set your roaming speed to 1.5" |
| Pranks, idle moves, sound effects, speech bubbles | "stop pranking me", "stop fidgeting", "turn off sound effects", "disable speech bubbles" |
| Voice volume and speed | "speak louder", "set your voice volume to 150 percent", "talk faster" |
| Personality | "be funnier", "be formal", "keep your answers short", "give me detailed answers", "sarcastic mode" |
| Listening | "listen longer", "set your follow-up time to 10 seconds", "set your nap time to 10 minutes", "always listen" |
| Other | "set my city to Bangalore", "what are your settings", "reset your settings" |

## What it can do

- **Windows:** minimize / maximize / close / restore / switch to any app ("close it"); snap left/right/corners/thirds/other screen; resize to 16:9, 4:3, bigger, smaller; "put windows side by side"; "minimize everything". It flies to the real button, presses it, and the window reacts like rubber.
- **Open things:** apps including Microsoft Store apps ("open WhatsApp"), websites ("open YouTube", "open ccbp.in in a new tab"), documents by name ("open the document named project report"), folders ("open downloads"), "open my latest download". If there's no such app, it opens the file or website with that name (and asks first when it's only a close match).
- **Music & video:** "play In the End" / "play lofi on YouTube" starts the top video; "play the first video" plays the top result of your last YouTube search; "search Linkin Park on Spotify", "play Numb on Spotify".
- **Inside apps:** "search lofi beats here" / "…on this search bar" types into the open app's own search box; "click reload" / "click the sign in button" presses it (if it isn't a real button, Scar finds the words on screen and clicks them).
- **Browser tabs:** "close this tab", "new tab", "switch to the Gmail tab".
- **Eyes:** "what's on my screen?", "explain this error", "what is this guy doing on YouTube on my screen?" (it looks at a screenshot and answers your actual question); "read this"; "summarize my clipboard", "explain the selection".
- **Typing:** "type Hello, I am Scar." (keeps your capitals and punctuation), "type dir slash s on command prompt" (opens it if needed; "slash", "dot", "backslash", "dash" become symbols in terminals and addresses), "type ls and press enter", "clear command prompt".
- **PC:** "how's my system?", "battery", "what's using my memory?", "volume 40", "brightness 70" / "maximum brightness", "mute", "next song", "take a screenshot", "lock my PC". It warns you when the battery hits 20% and 10%.
- **About Scar:** "what can you do?", "run a self check" (mic, AI model, internet, voice, errors in its log), "what went wrong?" (explains the last failed command).
- **Time:** "set a timer for 5 minutes", "remind me to drink water in 20 minutes", "wake me up at 7 am", "what are my reminders?", "cancel timers".
- **Notes & lists:** "take a note: …", "read my notes", "add eggs to my shopping list", "what's on my list?", "check off eggs".
- **Memory:** "remember that my name is Avi", "forget that…". Remembered facts are used in every conversation.
- **Info:** anything general (local model), "what's the weather?", "weather in Mumbai", "who was Nikola Tesla?", exact maths ("17 percent of 2340"), "good morning" briefing, time and date.
- **Where it sits:** drag it anywhere (even mid-dance or while napping) and it stays exactly there. "Stay there", "come here" (to your mouse), "go to the top right corner" / "left side" / "centre", and "roam around" (or right-click → **Roam**) to free it again. It never wanders off-screen or behind the taskbar.
- **Unpredictable:** 15 entrances (the 8 originals: quantum warp, orbital drop, diagonal dash, holo unfold, side sprints, top dive, rocket up; plus portal, bouncy drop, spiral, glitch teleport, peekaboo, cartwheel, zoom past) and 5 exits, never the same few in a row. It also has idle moves (looks around, spins, hops, waves, stretches, nods, glances at your cursor, yawns, dances, floats) and 10 playful pranks every few minutes. Turn the pranks off with the **Playful** button or "stop pranking me".
- **Extras:** "start a pomodoro", "start / check / stop the stopwatch", "flip a coin", "roll a dice", "pick a number between 1 and 50", "convert 5 km to miles" / "100 dollars in rupees", "what's the news" / "news about cricket", "define serendipity", "am I online?".
- **Look:** dark/light theme follows Windows ("light mode" to switch); size slider in the top-left dock; expressive face with lip-sync.

## Advanced

- **Multi-step tasks:** "Open YouTube and search lofi music", "research the best laptops under 60000 and put the top 3 in a note". Scar plans the steps, checks the results and continues. Anything it writes down after research must be backed by what it actually found. If a search fails, it says so instead of making something up.
- **Web research without keys:** "research …", "search the web for …". Uses DuckDuckGo, then Bing, and reads the top pages.
- **Writing for you:** "write an email to my professor asking for an extension". Scar drafts the text and types it into the active window.
- **Dictation:** "start dictation", then just talk; say "comma", "full stop" or "new line" for punctuation. "Stop dictation" ends it.
- **Memory:** "what was that error yesterday?", "what did I work on today?", "do you remember …". Covers conversations, what Scar saw on screen, and an activity journal.
- **Your files:** "search my files for thermodynamics", "summarize syllabus.pdf", "what does my timetable say about Friday?". Documents, Downloads and Desktop are indexed in the background: PDF, Word, PowerPoint, text and code.
- **Routines:** built in are "study mode", "focus mode", "break time" and "good night". Make your own with "create a routine called gaming mode: close VS Code, open Steam, volume 80".
- **Schedule:** "add maths class every Monday at 9 am", "what's my schedule tomorrow?". It reminds you 10 minutes before.
- **Proactive help:** offers to explain error pop-ups, suggests a break after 90 minutes, announces your schedule. "Stop the suggestions" turns this off.
- **Knows your voice:** "learn my voice" (read three lines). After that it responds only to you; "listen to everyone" turns that off. It also recognizes and ignores its own voice.
- **Echo cancellation:** you can say "Scar, stop" while it's talking, even on laptop speakers. It learns the speakers' echo during the first few answers and remembers it after that.
- **Voice:** "change your voice to British / female / deep / butler", "talk faster / slower".
- **"What's this song?"** works for Spotify, YouTube, or anything playing.
- **Multiple monitors:** it follows your mouse to the screen you're on, and hops to another screen to press a button there.
- **Phone remote:** "enable phone control" shows an address and a one-time PIN. Open it on your phone on the same Wi-Fi to type commands, use quick buttons, or see your screen. "Disable phone control" turns it off. Windows Firewall may ask once to allow Python on private networks.
- **Interrupt anytime:** say "Scar" or "stop", click the robot, or press **Ctrl+Shift+Space**.

## Settings (`backend/.env`, all optional)

```
JARVIS_NAME=Scar                    # assistant's name = wake name
JARVIS_FOLLOW_UP_MS=5000            # hands-free window after each answer
JARVIS_SLEEP_AFTER_S=180            # idle time before napping at the screen edge
JARVIS_INPUT_DEVICE=                # mic name (default: Windows default mic)
JARVIS_VOICE=am_michael             # Kokoro voice, e.g. af_heart, bm_george
JARVIS_WHISPER_MODEL=               # default large-v3-turbo; backend/models/whisper/small.en is lighter
JARVIS_REAL_CLICK=false             # true = move the real mouse and click, instead of a direct Windows call
JARVIS_OFFLINE=false                # true = no weather/Wikipedia/web lookups at all
JARVIS_VOICE_MATCH=0.45             # how closely a voice must match yours (after "learn my voice")
JARVIS_ONLY_MY_VOICE=true           # after enrolling, ignore other people
JARVIS_ECHO_CANCEL=true             # remove Scar's own voice from the microphone
JARVIS_FOLLOW_UP_MIN_LEVEL=0.5      # follow-up speech quieter than this share of your voice is ignored
JARVIS_ALWAYS_LISTEN=true           # act on clear commands without the name (never while on standby)

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
