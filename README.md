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
| **"Scar"**, **"Hey Scar"** or **"OK Scar"** | it listens (cyan ring). "Hey Scar" wakes it instantly |
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

## Coding practice (learning.ccbp.in / NxtWave, LeetCode, HackerRank…)

Open a problem in your browser, then:

| Say | What happens |
|---|---|
| "Solve this problem" / "solve" / "write the code" / "write the code only HTML" / "solve this question in Python" | reads the problem off the screen, writes a solution in the page's language (Python, HTML, JavaScript, SQL…) with the exact input/output format, and **types** it into the page's code editor, replacing the starter code. learning.ccbp.in blocks paste, so Scar types line by line and undoes the editor's auto-indent, auto-closed brackets / tags and autocomplete as it goes. Keep your hands off the keyboard for those few seconds |
| Accuracy (Python) | before typing anything, Scar runs its solution **on this PC** against every sample (read exactly from the screen by the vision model). If the output differs, the model is told the exact differing lines and tries again (up to 4 times). Only a solution that reproduces the samples is typed and submitted; otherwise Scar types its best attempt, says so, and doesn't submit. The test run is sandboxed (isolated interpreter, empty temp folder, 5-second limit; code touching files, the network or the system is never run) |
| Web tasks with a design image | the vision model describes the expected design (texts, layout, colours) and that goes into the prompt with the instructions |
| (after solving, automatically) | presses **Submit** straight away and tells you the verdict ("4 of 5 test cases passed"). It finds the button by its text, or asks the vision model to point at it when the text can't be read. "Turn off auto submit" makes it stop after typing (then say "submit", which asks first). "Turn on run check" adds a Run step before submitting: it compares the page's output with the sample and fixes the code once if needed (off by default, since every site's output panel looks different) |
| Long problems | Scar scrolls the problem side to read all of it, then scrolls back to the top |
| HTML / CSS / JS tabs | the solution is written as three parts and each is typed into its own tab |
| "Give me a hint" / "I'm stuck" | one hint, no full solution |
| "Explain this problem" | what the input is, what to compute, what to print |
| "Check my code" / "why is my code not working?" | explains the bug, then offers to put the fixed code in |
| "Run the code" / "submit" | presses Run; Submit asks first |
| "Open my course" / "open NxtWave" | learning.ccbp.in |

## What it can do

- **Windows:** minimize / maximize / close / restore / switch to any app ("close it"); snap left/right/corners/thirds/other screen; resize to 16:9, 4:3, bigger, smaller; "put windows side by side"; "minimize everything". It flies to the real button, presses it, and the window reacts like rubber.
- **Open things:** apps including Microsoft Store apps ("open WhatsApp"), websites ("open YouTube", "open ccbp.in in a new tab"), documents by name ("open the document named project report"), folders ("open downloads"), "open my latest download". If there's no such app, it opens the file or website with that name (and asks first when it's only a close match).
- **Music & video:** "play In the End" / "play lofi on YouTube" starts the top video; "play the first video" plays the top result of your last YouTube search; "search Linkin Park on Spotify", "play Numb on Spotify".
- **Inside apps:** "search lofi beats here" / "…on this search bar" types into the open app's own search box; "click reload" / "click the sign in button" presses it. If it isn't a real button, Scar finds the words on screen; if there are no words, it asks the vision model to point at it. That also covers descriptions: "click the second video", "click the red button", "double click the recycle bin", "right click the file", and "click that" after "what's on my screen?".
- **WhatsApp:** "send a WhatsApp to Rahul saying I'll be late", "message mom saying call me". Scar always reads the message back and waits for "yes". "Save Rahul's number as 98765 43210" makes it instant and exact; without a saved number it searches your chats and checks the chat name before typing.
- **Clipboard history:** "what did I copy earlier?", "paste what I copied before", "paste the third thing I copied", "paste the link I copied", "paste what I copied about the invoice", "clear my clipboard history". Kept in memory only (never saved to disk), and anything that looks like a password or API key is skipped.
- **Macros ("watch me do this"):** say "watch me do this" (or "start recording a macro called morning setup"), do the task, then "stop recording and call it morning setup". Later: "run morning setup". Press Escape to stop a replay. "List my macros", "delete the morning setup macro". Saved in `%APPDATA%\jarvis\macros`. Note: recording captures your keystrokes, so don't type passwords while it's recording.
- **Evening recap:** "how was my day?" / "evening recap": what you worked on, open to-dos, tomorrow's schedule. At 9 PM (changeable: "set your recap time to 8 pm") Scar offers it once.
- **Browser tabs:** "close this tab", "new tab", "switch to the Gmail tab".
- **Eyes:** "what's on my screen?", "explain this error", "what is this guy doing on YouTube on my screen?" (it looks at a screenshot and answers your actual question); "read this"; "summarize my clipboard", "explain the selection".
- **Typing:** "type Hello, I am Scar." (keeps your capitals and punctuation), "type dir slash s on command prompt" (opens it if needed; "slash", "dot", "backslash", "dash" become symbols in terminals and addresses), "type ls and press enter", "clear command prompt".
- **PC:** "how's my system?", "battery", "what's using my memory?", "volume 40", "brightness 70" / "maximum brightness", "mute", "next song", "take a screenshot", "lock my PC". It warns you when the battery hits 20% and 10%.
- **About Scar:** "what can you do?", "run a self check" (mic, AI model, internet, voice, errors in its log), "what went wrong?" (explains the last failed command).
- **Time:** "set a timer for 5 minutes", "remind me to drink water in 20 minutes", "wake me up at 7 am", "what are my reminders?", "cancel timers".
- **Notes & lists:** "take a note: …", "read my notes", "add eggs to my shopping list", "what's on my list?", "check off eggs".
- **Memory:** "remember that my name is Avi", "forget that…". Remembered facts are used in every conversation.
- **Chat:** "how are you?", "who are you?", "hello" answer instantly. General-knowledge questions ("why is the sky blue?", "tell me a fun fact about octopuses") use a light, tool-free path and start answering in about half a second. The local 4B model can still get details wrong; for sharper facts, add a free cloud model in `.env` (see below).
- **Info:** anything general (local model), "what's the weather?", "weather in Mumbai", "who was Nikola Tesla?", exact maths ("17 percent of 2340"), "good morning" briefing, time and date.
- **Where it sits:** drag it anywhere (even mid-dance or while napping) and it stays exactly there. "Stay there", "come here" (to your mouse), "go to the top right corner" / "left side" / "centre", and "roam around" (or right-click → **Roam**) to free it again. It never wanders off-screen or behind the taskbar.
- **Unpredictable:** 15 entrances (the 8 originals: quantum warp, orbital drop, diagonal dash, holo unfold, side sprints, top dive, rocket up; plus portal, bouncy drop, spiral, glitch teleport, peekaboo, cartwheel, zoom past) and 5 exits, never the same few in a row. It also has idle moves (looks around, spins, hops, waves, stretches, nods, glances at your cursor, yawns, dances, floats) and 10 playful pranks every few minutes. Turn the pranks off in **⚙ Settings** or with "stop pranking me".
- **Extras:** "start a pomodoro", "start / check / stop the stopwatch", "flip a coin", "roll a dice", "pick a number between 1 and 50", "convert 5 km to miles" / "100 dollars in rupees", "what's the news" / "news about cricket", "define serendipity", "am I online?".
- **Look:** dark/light theme follows Windows ("light mode" to switch); size slider and ⚙ All settings in the top-left dock; expressive face with lip-sync.

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
JARVIS_KWS_THRESHOLD=0.1            # "Scar" keyword spotter sensitivity (lower = hears the name more easily)
JARVIS_HEY_JARVIS=false             # also wake on "Hey Jarvis" (off: it false-fires on videos and lectures)

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
- "Scar" wake word: sherpa-onnx keyword spotter, model in `backend/models/kws/` (download
  `sherpa-onnx-kws-zipformer-gigaspeech-3.3M-2024-01-01.tar.bz2` from the sherpa-onnx GitHub releases, tag `kws-models`,
  and extract it there). Without it Scar falls back to Whisper name spotting.
- llama.cpp runs with 2 slots: slot 0 keeps the big prompt cached for real turns (primed after every turn), slot 1 takes
  side calls. Follow-up questions start answering in ~0.2–0.7 s.

Rebuild the robot models after editing `assets/`: `cd frontend && npm run build:avatar`.
Run tests: `cd backend && .venv\Scripts\python -m pytest -q`.
Logs: `backend/logs/jarvis.log`, per-turn timings in `backend/logs/latency.csv`.
