# Jarvis Local Assistant — Corrected Build Plan (v2)

Written 2026-09-29 after reviewing the original blueprint against what is actually in this folder
and against this machine (i7-13620H, 24 GB RAM, RTX 5050 Laptop 8 GB, Python 3.11.9, Node 24, Rust 1.96).

---

## 0. Where the project really is today

| Area | Blueprint says | What exists |
|---|---|---|
| Shell | Tauri v2 + Rust in `/shell` | `/shell` is **empty**. Real shell is **Electron** (`frontend/electron/main.cjs`) |
| UI | React 19 + React Three Fiber | **Vanilla Three.js + GSAP** (`frontend/src/*.js`) |
| Avatar | entry anims, roam, bezel sleep | Already written: `animationController.js` (entry + side entries), `roamEngine.js`, `mischiefEngine.js` (peek-a-boo from the bezel), `dragController.js`, `jetpackFire.js`, `eyeVisorSystem.js` |
| Voice | local Silero → Whisper → LLM → Kokoro | `speechRecognizer.js` uses the **Web Speech API**, which sends audio to Google, so it is **not local**. It is also unreliable inside Electron. |
| Backend | full voice brain | Only `backend/mock_server.py` exists |
| Contract | 4 rich events | `contracts/protocol.json` only has 3 thin events, with no request IDs and no `destination_reached` |
| Models | "15k-triangle" GLBs | GLBs are **14–17 MB each** because of **4K PNG textures (16–20 MB each)** |

**Decision 1: keep Electron. Don't restart in Tauri.**
Electron's `setIgnoreMouseEvents(true, { forward: true })` still sends mouse-move events while clicks pass through. Hover-to-grab the avatar depends on that, and it already works in your code. Tauri's `set_ignore_cursor_events` has no forward mode, so you'd have to write a Win32 hook to rebuild that behavior. The avatar layer is also already built in vanilla Three.js. Porting it to R3F is weeks of work and adds nothing.

**Decision 2: two processes, not three.**
- **Body:** Electron + Three.js. Rendering, avatar state machine and cloth proxy.
- **Brain + Hands:** Python. Voice pipeline, LLM, and Win32/UIAutomation OS control through `pywin32`, `comtypes`/`uiautomation` and `ctypes`. Python already has to run for the ML side. Adding a Rust process means a third runtime, a third build and a third place for bugs.
- Rust stays optional. Add it later only if one piece is too slow in Python (e.g. `Windows.Graphics.Capture` at 60 fps). If you do, build it as a native Node addon (napi-rs) loaded by Electron, not as a separate app.

**Decision 3: stop running three agents in parallel.**
`orchestration.py` spawns three agents at once, and that is how the code ended up as Electron + vanilla JS while the contract README still says Tauri + R3F. Build in the phase order below. The contract gets locked first and checked by a test, so any drift fails loudly.

---

## 1. Final architecture

```
┌──────────────────────── Electron (frontend/) ────────────────────────┐
│ main.cjs  : transparent always-on-top click-through overlay,         │
│             per-monitor DPI, WS client, autostart                    │
│ renderer  : Three.js avatar + FSM + cloth proxy + speech bubble      │
└───────────────▲──────────────────────────────────────┬───────────────┘
                │ ws://127.0.0.1:8765  (token + Origin check)
┌───────────────┴──────────────────── Python (backend/) ───────────────┐
│ audio/   mic → Silero VAD ─┬─ openWakeWord ("hey jarvis")            │
│                            └─ follow-up gate (5 s hot mic)           │
│ stt/     faster-whisper (GPU)                                        │
│ brain/   fast intent router ──(miss)──► llama.cpp server (streaming) │
│ tts/     Kokoro-82M ONNX, sentence-chunked streaming playback        │
│ hands/   window finder (EnumWindows + UIAutomation), actions,        │
│          window capture (PrintWindow / WGC) for cloth proxy          │
│ core/    session FSM, event bus, WS server                           │
└──────────────────────────────────────────────────────────────────────┘
```

---

## 2. Honest performance targets

The blueprint promises "<200 ms". Nothing in a speech loop gets there. Alexa itself takes about 1 s. These are the targets to aim for:

| Stage | Realistic target on this laptop |
|---|---|
| End-of-speech detection (silence wait) | 400–600 ms. This is the single biggest cost, so tune it. |
| STT (faster-whisper `small.en` / `distil-small.en`, GPU, 2–3 s utterance) | 80–200 ms |
| Intent router hit (e.g. "minimize chrome") | < 5 ms, with no LLM |
| LLM first token (4–8B Q4 via llama.cpp, GPU) | 100–250 ms |
| TTS first audio (Kokoro, first sentence) | 100–250 ms |
| **Voice command → avatar starts moving** | **≈ 0.6–0.9 s** |
| **Question → first spoken word** | **≈ 1.0–1.4 s** |
| Avatar render | 60 fps while active, ≤ 10 fps asleep. **Not a locked 120 fps** (see §5.4). |

**8 GB VRAM budget:** LLM Q4 7B ≈ 4.7 GB + KV cache 0.5 GB + Whisper small ≈ 0.6 GB + Electron/Three ≈ 0.3 GB. That fits, but tightly. Start with a **~4B instruct model (Qwen3-4B-Instruct class, Q4_K_M ≈ 2.5 GB)**. It's faster and leaves headroom. Move to 7–8B only if answers are too weak.

**RTX 50-series (Blackwell) gotcha:** you need CUDA ≥ 12.8 builds. That means PyTorch `cu128`, a llama.cpp release built for CUDA 12.8+/sm_120, and a CTranslate2 build that supports your GPU. If faster-whisper won't run on the GPU, fall back to `whisper.cpp` with CUDA, or to faster-whisper `int8` on CPU (≈ 300–500 ms). Check this in Phase 1 before building anything else.

---

## 3. Contract v2 (lock this first — `contracts/protocol.json`)

Every message follows this shape: `{ "v": 2, "id": "<uuid>", "event": "...", ...payload }`.
Replies carry `"reply_to": "<id>"`. This is what lets "destination_reached" match up with the right command.

**Backend → Frontend**
```jsonc
{ "event": "voice_state", "status": "idle|wake|listening|thinking|speaking|follow_up|sleeping",
  "transcript": "minimize chrome", "partial": false, "follow_up_ms_left": 3800 }
{ "event": "say", "text": "Minimizing Chrome.", "final": true }          // speech bubble
{ "event": "summon", "entry": "random|0|1|2|3", "from": "boot|wake" }
{ "event": "fly_to", "target": {"x":1820,"y":12}, "monitor": 0, "dpi_scale": 1.25,
  "purpose": "minimize|close|maximize|snap|click", "duration_ms": 550 }
{ "event": "proxy_start", "window_id": "0x1A2B", "bounds": {...}, "texture_url": "http://127.0.0.1:8765/cap/abc.webp" }
{ "event": "proxy_move",  "bounds": {...} }                               // animated/voice-driven move
{ "event": "proxy_end",   "final_bounds": {...} }
{ "event": "theme", "mode": "dark|light" }
```

**Frontend → Backend**
```jsonc
{ "event": "hello", "token": "<from %APPDATA%\\jarvis\\token>", "screens": [ {bounds, scale} ] }
{ "event": "destination_reached", "reply_to": "<fly_to id>" }
{ "event": "avatar_clicked" }                  // user poked it = acts like wake word
{ "event": "user_drag", "phase": "start|move|end", "cursor": {...}, "vel": {...} }
{ "event": "ui_ready" }
```

Coordinates are always **physical screen pixels**. The frontend converts them to CSS pixels using `screen.getDisplayMatching().scaleFactor`. This is the #1 source of "the avatar flies to the wrong spot" bugs on a 125% or 150% scaled laptop.

**Security, which the blueprint skips:** any web page open in Chrome can connect to `ws://127.0.0.1:8765`. That means a malicious site could send commands that minimize or close your windows. The WS server must:
1. reject any `Origin` header that isn't the Electron app (`file://` or `http://localhost:5173`),
2. require the random token in `hello`,
3. accept OS actions **only from its own brain**, never from a client message.

**Test:** `contracts/test_contract.py` validates the JSON Schema against sample messages. The frontend and backend each run it in CI or before a commit.

---

## 4. Phase plan

### Phase 0 — Clean-up & asset prep (½–1 day)
1. **Compress the models.** This is a big win: 4K textures × 4 maps × 2 themes costs roughly **600+ MB of VRAM** if both themes are loaded.
   ```bash
   npx @gltf-transform/cli optimize model.glb model.opt.glb --texture-compress webp --texture-size 1024 --compress meshopt
   ```
   Or use KTX2/UASTC to cut VRAM even further. The target is **≤ 2 MB per GLB**. Load only the active theme and dispose the other one on switch.
2. Confirm the real triangle count after cleanup. `model.obj` is 2 MB, which is more than 15k triangles. If it's > 30k, decimate in Blender (Decimate modifier, keep UVs).
3. Delete the debug PNGs in `frontend/public/` (`view_*.png`, `mesh_*.png`) and the `.blend1` backups from `public/`. Don't ship `.blend` files in `public/`.
4. `reference/` holds the commercial **DesktopMate** build. Use it only to study behavior. Don't copy its assets or decompiled code into this project.
5. Delete `/shell` and fix `contracts/README.md` and `.antigravity/agents.json` so they describe Electron + Python.
6. `git init` and make the first commit. Right now there's no version control at all.

### Phase 1 — Toolchain smoke tests on the GPU (1 day, do before writing features)
Create `backend/.venv` on Python 3.11. Write one script per item and record its latency in `backend/bench/RESULTS.md`:
- `bench_vad.py`: Silero VAD on 512-sample chunks. Expect < 1 ms per chunk on CPU.
- `bench_wake.py`: openWakeWord `hey_jarvis` pretrained model. Check false triggers during 10 min of YouTube audio.
- `bench_stt.py`: faster-whisper `small.en` on CUDA with `float16`. Measure a 3 s clip.
- `bench_llm.py`: start `llama-server -m qwen3-4b-instruct-q4_k_m.gguf -ngl 99 -c 4096 --port 8080`. Measure time-to-first-token and tokens/sec.
- `bench_tts.py`: `kokoro-onnx` time to first audio chunk for a 6-word sentence.
- **Exit criterion:** all five run on this laptop, and the numbers are within the §2 table (or a fallback is chosen).

### Phase 2 — Voice loop without any animation (3–4 days)
Files: `backend/audio/stream.py`, `backend/audio/vad.py`, `backend/audio/wake.py`, `backend/stt/whisper.py`, `backend/tts/kokoro.py`, `backend/core/session.py`, `backend/server.py`

1. **Audio capture:** `sounddevice` InputStream, 16 kHz mono, 512-sample blocks, pushed into an `asyncio.Queue`. Keep a 1.5 s pre-roll ring buffer so the first syllable after the wake word isn't lost.
2. **Session FSM** (the heart of the "Alexa" feel):
   ```
   IDLE ──wake word / avatar_clicked──► LISTENING
   LISTENING ──VAD silence 500 ms──► THINKING      (max utterance 15 s)
   LISTENING ──no speech within 4 s──► IDLE
   THINKING ──first TTS audio──► SPEAKING
   SPEAKING ──playback truly finished + 250 ms──► FOLLOW_UP (5000 ms)
   FOLLOW_UP ──VAD speech onset (p>0.6 for ≥ 150 ms)──► LISTENING   (no wake word)
   FOLLOW_UP ──5000 ms, no speech──► IDLE
   any ──idle 180 s──► SLEEPING (frontend walks to the bezel)
   SLEEPING ──wake word──► summon + LISTENING
   SPEAKING ──wake word (barge-in)──► stop TTS → LISTENING
   ```
3. **Echo problem.** The blueprint misses this, and it will break follow-up mode. The mic hears Jarvis's own voice and triggers itself. Fix it in this order:
   - (a) While SPEAKING, feed only the wake-word detector. Ignore VAD speech onset.
   - (b) Start FOLLOW_UP only after the output device has actually drained, not when synthesis finishes.
   - (c) Optionally add echo cancellation (`speexdsp`/WebRTC AEC with the TTS output as the reference signal), so real barge-in works on laptop speakers.
   - Headphones make everything work. Test on speakers anyway.
4. **STT:** faster-whisper on the buffered utterance with `vad_filter=False` (you already did VAD), `beam_size=1`, `language="en"`. Use an `initial_prompt` listing your app names ("Chrome, VS Code, Spotify, YouTube") so they get spelled correctly.
5. **TTS streaming:** split LLM output on sentence boundaries. Synthesize sentence 1 while the LLM is still producing sentence 2. Play through one continuous output stream.
6. `server.py` broadcasts `voice_state` on every FSM transition.
7. **Exit criterion:** from a terminal (no UI), you say "hey jarvis, what's 12 times 8", it answers, and you ask "and times 3?" within 5 s with no wake word. It still works on speakers without triggering itself.

### Phase 3 — Brain: router + LLM tools (2–3 days)
Files: `backend/brain/router.py`, `backend/brain/llm.py`, `backend/brain/tools.py`, `backend/brain/memory.py`

1. **Fast intent router** (the regex/fuzzy layer, which gives the "precise Jarvis" feel). It handles the ~20 commands you'll say most, without calling the LLM:
   `minimize|close|maximize|restore <app>`, `snap <app> left|right`, `make <app> half|third|<16:9>`, `open <site/app>`, `move <app> to the other screen`, `dark|light mode`, `go to sleep`, `volume up/down`.
   App names resolve through an alias table (`"code"|"vs code"|"editor"` → `Code.exe`) plus `rapidfuzz` matching against the titles of currently open windows.
2. **LLM** for everything else: llama.cpp **server** with its OpenAI-compatible `/v1/chat/completions`, `stream: true`.
   - Use native tool calling (`tools=[...]`), or a JSON schema via `response_format` when a tool is expected. For normal questions, stream plain text straight into TTS. Don't wrap every answer in JSON, because that stops TTS from starting until the JSON closes.
   - Tools: `window_action(app, action)`, `resize_window(app, preset)`, `open_url(url)`, `open_app(name)`, `list_windows()`, `move_window_animated(app, where)`, `set_theme(mode)`.
   - Rolling memory: the last 6 turns go in the prompt, so follow-ups like "and times 3?" or "close it too" work. "It" resolves to the last target window.
3. **"Answer anything":** a local 4–8B model is fine for general knowledge and chat, but knows nothing current. You can add an **opt-in** `web_search` tool later (e.g. a self-hosted SearXNG). Keep it off by default so the assistant stays 100% local.
4. **Safety allowlist:** the LLM can only call the tools above. It cannot run shell commands. `close` on a window whose title shows unsaved changes (`*` or `●`) asks "It has unsaved changes. Close anyway?" and waits for a yes during the follow-up window.
5. **Exit criterion:** 30 scripted test phrases (`backend/tests/test_router.py`) map to the right tool calls, and router hits take < 5 ms.

### Phase 4 — Hands: finding and acting on windows (3 days)
Files: `backend/hands/windows.py`, `backend/hands/actions.py`, `backend/hands/capture.py`

1. Call `SetProcessDpiAwarenessContext(PER_MONITOR_AWARE_V2)` at Python startup, otherwise every coordinate is off on a scaled display.
2. **Enumerate:** `EnumWindows` → keep windows that are visible, not cloaked (`DwmGetWindowAttribute(DWMWA_CLOAKED)`), and have a title. Map each to its process exe with `GetWindowThreadProcessId` + `QueryFullProcessImageName`.
3. **Find the real button position.** Don't use the blueprint's `rect.right - 140`. Chrome, VS Code, Spotify and Discord draw their own title bars, so fixed offsets are wrong. Try these in order:
   - (a) **UIAutomation:** find a `Button` named "Minimize"/"Maximize"/"Restore"/"Close" under the window and read its `BoundingRectangle`. This works for Chrome, Edge, VS Code and File Explorer.
   - (b) `DwmGetWindowAttribute(DWMWA_CAPTION_BUTTON_BOUNDS)` for standard frames.
   - (c) Last resort: the offsets, scaled by the window's DPI.
4. **Perform the action reliably, and keep the visual separate.** The avatar flies there and plays its "tap". Then Python calls `ShowWindow(SW_MINIMIZE)`, `PostMessage(WM_SYSCOMMAND, SC_CLOSE)` or `SetWindowPos`. That never misses. A synthetic `SendInput` click is optional (a setting called "real click"), because it can hit the wrong thing if a window moves, and it **can't** click elevated/admin windows (UIPI).
5. **Snap/resize presets:** use `MonitorFromWindow` → `GetMonitorInfo(rcWork)` and compute half/third/quarter/16:9 rects inside the work area. Account for the invisible resize borders (`DWMWA_EXTENDED_FRAME_BOUNDS` vs `GetWindowRect`) so windows sit flush.
6. **Open things:** `os.startfile("https://youtube.com")` opens a URL in the default browser. Apps open from an alias table or a Start-menu `.lnk` search.
7. **Browser tabs** (close a tab, switch to the YouTube tab): Win32 can't see individual tabs. UIAutomation can list Chrome's `TabItem` elements and `Invoke`/`Select` them. That's good enough for "switch to YouTube tab" and "close this tab". Keyboard shortcuts (`Ctrl+W`) are the fallback.
8. **Exit criterion:** `python -m hands.cli minimize chrome` / `snap code left` / `close notepad` all work at 100%, 125% and 150% display scaling, and on a second monitor if you have one.

### Phase 5 — Wire the Body to the Brain (2–3 days)
Files: `frontend/src/bridge.js` (new), `frontend/src/avatarFSM.js` (new, wraps your existing controllers), `frontend/electron/main.cjs`, and `speechRecognizer.js` gets removed.

1. **Remove the Web Speech API** (`speechRecognizer.js`). Voice now comes only from the backend. Keep the context-menu buttons for testing.
2. `bridge.js`: WS client with auto-reconnect (backoff 250 ms → 5 s), the `hello` token, and a typed event emitter.
3. `avatarFSM.js` ties together the modules you already have:
   | Backend event | Avatar behaviour (existing module) |
   |---|---|
   | `summon` | random pick from `animationController` entries (4 center + 4 side), never the same twice in a row |
   | `voice_state: listening` | stop roaming, turn to face the user, visor glow (`eyeVisorSystem`) |
   | `thinking` | visor pulse/scan |
   | `speaking` | mouth/visor bars driven by TTS amplitude (backend sends an `rms` value every 50 ms) + `speechBubble` |
   | `follow_up` | soft breathing glow ring that shrinks over the 5 s |
   | `fly_to` | `jetpackFire` on, GSAP path (arc, not a straight line, `power2.inOut`, ~550 ms), bank into the turn, land and tap → send `destination_reached` |
   | `sleeping` / idle 180 s | `mischiefEngine` peek-a-boo: walk to the nearest bezel, hide 85%, breathe ±5% |
   | wake while sleeping | dash-out entry from that same bezel |
4. **Sizing** ("not too small, not too big"): use an orthographic camera in CSS pixels. Target avatar height = `clamp(0.14 × workArea.height, 150 px, 240 px)`. At 1080p that's ~150 px, at 1440p ~200 px. Also add a setting slider (S/M/L) stored in `%APPDATA%`.
5. **Theme:** follow Windows through `nativeTheme.shouldUseDarkColors` + `nativeTheme.on('updated')` in `main.cjs`, sent to the renderer. Voice ("dark mode") overrides it until the next OS theme change.
6. **Timeout:** if `destination_reached` doesn't arrive within 1.5 s (renderer hung or minimized), the backend does the action anyway. The OS action must never depend on the animation finishing.
7. **Exit criterion:** you say "hey jarvis, minimize chrome". The avatar flies to Chrome's real minimize button, taps, Chrome minimizes, it says "Done". Then you say "now snap code left" within 5 s, and that works too.

### Phase 6 — Cloth / rubber window proxy (4–6 days, the hardest feature)
Split it into two scopes. Ship scope A first.

**Scope A: voice-driven moves (reliable).** "Move Chrome to the right half" / "drag YouTube over here":
1. Python captures the window with `PrintWindow(hwnd, PW_RENDERFULLCONTENT)`. That's fine for 1 frame. `Windows.Graphics.Capture` is only needed for live frames. It saves a WebP and serves it at `http://127.0.0.1:8765/cap/<id>.webp`.
2. Python hides the real window visually by applying `WS_EX_LAYERED` + `SetLayeredWindowAttributes(alpha=0)`. That's instant and keeps its state. Then it sends `proxy_start` with the exact bounds.
3. The frontend spawns the proxy plane at those bounds, **pixel-exact**, so the swap is invisible. The avatar grabs a corner and flies it to the destination while the cloth physics run.
4. `proxy_end`: Python calls `SetWindowPos` to the final rect, restores alpha to 255, and removes `WS_EX_LAYERED` if it wasn't there originally. The proxy fades out over 80 ms.
5. Safety net: if anything throws, **always restore alpha** (try/finally, plus a watchdog after 3 s). Leaving a user's window invisible is the worst possible bug here.

**Scope B: user-driven drags (stretch goal).** You grab a title bar yourself:
- Use `SetWinEventHook(EVENT_SYSTEM_MOVESIZESTART / END)`, not a raw mouse hook. Windows tells you exactly when a move starts and ends, and which window it is.
- On start: capture, hide as above, render the proxy under the cursor, and let Windows keep moving the invisible real window. On end: re-show it.
- **Chrome tab tear-out** (pulling a tab out of the strip) happens inside Chrome's own drag loop. Hiding windows during it breaks Chrome. Only handle the new window *after* Chrome creates it (`EVENT_OBJECT_SHOW` on a new Chrome HWND during a drag). Treat this as experimental.

**Physics:** a pure shader with a velocity uniform goes flat the instant you stop moving, so there's no jiggle. Put a small simulation on the CPU instead:
- Plane `PlaneGeometry(w, h, 24, 16)`, ~425 vertices. That's trivial for JS every frame.
- The grab point follows the cursor/avatar exactly. Every other vertex is pulled toward its rest position relative to the grab point by a **damped spring** (`k ≈ 120`, `damping ≈ 14`). The farther from the grab point, the more lag. This gives stretch while moving, and wobble and settle when stopped.
- Add bend along the drag direction: `z = -sin(π·u) · |v| · 0.02`. Clamp stretch to ≤ 12% so text on the card stays readable.
- **Lift:** scale 1.0 → 1.04 on grab (GSAP, 120 ms), plus a blurred shadow plane underneath offset by (8, 14) px, whose opacity follows the lift. That's cheaper and nicer than a real shadow map on a transparent window.
- Release: a spring overshoot into the final rect (`elastic.out(1, 0.5)`, 350 ms), then the swap back to the real window.

**Exit criterion:** 20 voice-driven moves in a row with no window left invisible, and the proxy swap is not noticeable at rest.

### Phase 7 — Boot, polish, performance (2–3 days)
1. **Autostart:** add a Task Scheduler "At log on" task (delay 10 s) that runs `Launch_Desktop_Assistant.bat`. Start the backend first. The avatar plays its entry right away while the models warm up in the background, and a "warming up…" bubble covers the first ~5–10 s.
2. **Launcher:** replace `npx vite` + `npx electron` with a built app (`vite build` + `electron-builder`) and a single `jarvis.exe` that spawns `backend/.venv/Scripts/pythonw.exe server.py` and kills it on quit. Also stop using `taskkill /IM electron.exe`, because that kills *every* Electron app you have open (VS Code, Discord, …).
3. **Frame budget** (important on a laptop):
   - A full-screen transparent always-on-top window is composited every frame. Render **on demand**: 60 fps while moving, animating or speaking, 30 fps while idle-breathing, ≤ 10 fps asleep. Don't lock 120 fps. Nobody sees the difference on a 15 cm avatar, but your battery will.
   - `renderer.setPixelRatio(Math.min(devicePixelRatio, 1.5))`, one light plus an env map, no real-time shadows on the avatar.
   - Hide the overlay while a fullscreen game or video is in the foreground (check `SHQueryUserNotificationState`).
4. **Settings panel** (right-click avatar): size, wake word sensitivity, follow-up length (3/5/8 s), "real mouse click" on/off, voice pick, startup on/off.
5. **Logs:** a per-turn latency line `wake→end_speech→stt→first_token→first_audio` written to `backend/logs/latency.csv`.

### Phase 8 — Verification checklist
| # | Test | Pass condition |
|---|---|---|
| 1 | Boot 5 times | 4 different entries seen, no repeat twice in a row, ≤ 2 s after login |
| 2 | Leave idle 3 min | walks to the nearest edge, peeks, breathes. Wake word → dashes out from that edge |
| 3 | "Hey Jarvis, minimize Chrome" ×10 at 125% scaling | avatar lands on the real button every time, Chrome minimizes every time |
| 4 | Follow-up | a second command within 5 s works without the wake word. After 5 s it needs the wake word again |
| 5 | Speakers, no headphones | Jarvis never triggers itself from its own speech |
| 6 | 10 min of YouTube playing | ≤ 1 false wake |
| 7 | Q&A latency | median question → first word ≤ 1.4 s (from `latency.csv`) |
| 8 | Voice-driven window move ×20 | cloth effect visible, window always restored |
| 9 | Idle resource use | < 3% CPU, < 5% GPU when asleep. VRAM under 7 GB total with the LLM loaded |
| 10 | Security | a test HTML page in Chrome trying `new WebSocket('ws://127.0.0.1:8765')` gets rejected |

---

## 5. Timeline (realistic, one developer)

| Phase | Days |
|---|---|
| 0 Cleanup & assets | 1 |
| 1 GPU toolchain benchmarks | 1 |
| 2 Voice loop + follow-up | 4 |
| 3 Router + LLM tools | 3 |
| 4 Window hands | 3 |
| 5 Body ↔ brain wiring | 3 |
| 6 Cloth proxy (scope A) | 5 |
| 7 Boot / perf / settings | 3 |
| **Total** | **≈ 23 working days (~4–5 weeks)**. Scope B tab tear-out is extra. |

The blueprint's Gantt chart gives this ~3 weeks, with the cloth shader alone in 3 days. Plan for the longer version.

---

## 6. Backend dependency list (`backend/requirements.txt`)
```
websockets>=13
sounddevice
numpy
silero-vad            # pulls torch; install torch cu128 first
openwakeword
faster-whisper        # verify CTranslate2 build supports RTX 50xx
kokoro-onnx
onnxruntime-gpu       # or onnxruntime (CPU) — Kokoro is fast enough on CPU
httpx                 # talk to llama-server
rapidfuzz
pywin32
comtypes
uiautomation
pillow
jsonschema
```
Run `llama-server.exe` (a llama.cpp CUDA release) as a separate process on port 8080. Don't use `llama-cpp-python`, because its Windows CUDA wheels are the most fragile part of this whole stack.
