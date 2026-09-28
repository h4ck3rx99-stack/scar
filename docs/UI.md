# The SCAR desktop app

The desktop app is a thin client of the SCAR runtime. Agent logic, permissions, providers, tools, memory and voice
all live in the Python runtime. The app talks to it over the authenticated local app API
([architecture.md](architecture.md#desktop-app-and-app-api), [security.md](security.md#desktop-app-and-app-api)).
The CLI (`uv run scar …`) and the app can run at the same time against the same runtime.

Shell: Tauri 2 on WebView2 ([ADR 0014](adr/0014-desktop-app-tauri.md)). Frontend: React + TypeScript + Vite in `app/`.

## Starting and stopping

* Launching the app attaches to a runtime that is already running (the daemon, or another client's runtime). If
  none is running, the app starts one as a managed sidecar. The window appears immediately and shows
  "SCAR is starting…" until the runtime answers.
* **First run of the installed app:** the runtime's private Python environment is created under
  `%LOCALAPPDATA%\SCAR\runtime` (3 steps, progress shown in the window). This needs internet once. If it fails, the
  window says so; the log is in `%LOCALAPPDATA%\SCAR\logs`.
* **Closing the window** hides SCAR to the tray, so reminders and monitors keep running. The first time, a
  notification explains this.
* **Quit SCAR** (tray menu) stops the runtime, unless *Keep running in the background* is on in
  Settings → App (`keep_running_in_background`).
* **Single instance:** launching SCAR again focuses the existing window.
* **Runtime crash:** a banner appears and the app restarts the runtime (at most 3 times in 10 minutes). After that,
  the banner offers *Open diagnostics*.

## Tray

Left-click opens the main window. The menu has **Open SCAR**, **Quick Bar**, **Pause listening**, **Stop All**,
**Status** and **Quit SCAR**. The tray icon changes with the state: idle, working, listening, needs approval, error.

## Hotkeys

| Key | What it does | Setting |
|---|---|---|
| Ctrl+Alt+Space | Open the Quick Bar from anywhere | Settings → App → Quick Bar (`quickbar_hotkey`) |
| Ctrl+Alt+Shift+K | Emergency stop: cancels all running work (kill switch) | `kill_switch_hotkey` |
| Enter / Shift+Enter | Send / new line in the message box | — |
| Esc | Close the Quick Bar | — |

If the Quick Bar shortcut is taken by another app, SCAR shows a "Quick Bar shortcut unavailable" notification and
Settings → App shows the error. Pick another combination there.

The terminal voice mode (`uv run scar --voice`) uses its own push-to-talk hotkey (`ptt_hotkey`, default also
Ctrl+Alt+Space). If you run the terminal voice mode while the app is open, change one of the two.

In the app, push-to-talk is the mic button (main window and Quick Bar). There is no separate global push-to-talk
hotkey in the app: press the Quick Bar hotkey, then the mic button.

## Surfaces

### Quick Bar

A compact bar for quick requests. Type and press Enter (or click the mic). Progress lines appear under the bar,
then a compact result card with *Open in SCAR*. It stays open while a task runs or an approval waits; otherwise it
closes when it loses focus, like Spotlight.

### Conversation

Your requests and SCAR's answers, streamed as they arrive. Each task shows a plain-language activity timeline
("Opening VS Code", "Running tests"…); expand it for step details. Every result carries an outcome badge:

* **Done** — SCAR checked the result (for example, the file exists with the expected content).
* **Done — couldn't verify** — the action ran, but SCAR had no way to confirm the effect. The reason is shown.
* **Failed** — with the reason and what to do next.
* **Answered** — a plain answer, no action taken.

Each running task has a Cancel button. **Stop All** (top bar) triggers the kill switch.

### Approvals

When an action needs your permission, an approval card appears in the conversation (queued if several wait). It
shows exactly what will happen (recipient, message, command and folder, file paths and counts), why approval is
needed, the risk level, and a countdown. When the countdown runs out, the request is denied.

Buttons: **Allow once**, **Allow for this task**, **Deny**. *Always allow this exact action* is in the secondary
menu; it saves a permission you can revoke under Permissions.

CRITICAL actions need the confirmation word typed before *Allow once* is enabled.

An approval decision is only accepted from a real click or keypress on the card. The app sends the request ID and
the exact arguments hash the card showed; if the action changed, the runtime refuses the decision.

When the window is hidden, a Windows notification says "SCAR needs your approval". The notification only brings
SCAR forward: nothing can be approved from a notification.

### Status

The status bar shows the state in words (Ready, Working, Listening, Waiting for you, Offline) and the AI connection
("Cloud AI connected", "Using local AI, answers will be slower", "No AI available: only basic commands work",
"Cloud AI rate-limited, retrying in 45s"). Click it for the status panel: local model, CPU, RAM, GPU, VRAM,
battery, voice and microphone, daemon.

### Voice

Mic button in the message box and in the Quick Bar, a live transcript, and a state indicator (idle, listening,
thinking, speaking). Settings → Voice has devices, a microphone test, the wake word, the speech voice and the
push-to-talk key for the terminal voice mode. While the microphone is on, a mic indicator stays visible in the
status bar with a one-click mute. *Pause listening* in the tray does the same.

### Computer-control indicator

While SCAR moves the mouse, types or controls windows, an always-on-top pill appears: "SCAR is controlling your
computer · Ctrl+Alt+Shift+K stops it", with a Stop button. When SCAR captures the screen, the pill briefly says
"SCAR is looking at your screen".

### Tasks & Monitors

Running, recent and scheduled tasks, reminders and monitors (process, folder, dev server, download), each with
status, details and cancel.

### Permissions

Autonomy level (with a plain explanation of each level), saved permissions with revoke, allowed folders, and the
audit log.

### Memory

Browse, search, edit and forget what SCAR remembers, grouped by category. *Forget everything* asks for
confirmation.

### Settings

* **AI** — add a key (with a link to get one), *Test* makes a real call. Keys go to Windows Credential Manager
  through the runtime and are never shown again.
* **Accounts** — connect and disconnect Google, Microsoft, Telegram, Discord, WhatsApp and GitHub. Each shows its
  state, or exactly what it still needs ("Needs: …"). Google and Microsoft sign-in opens your default browser.
* **Voice**, **App** (hotkeys, appearance, startup, game mode, privacy), export/import of settings (never secrets).

### Onboarding

Shown on first start; skippable and resumable: welcome, allowed folders, a free AI key with a live test,
microphone test, autonomy level, accounts, health check summary, done (with the Quick Bar hotkey).

### Diagnostics

The only screen that shows internals: the health check (the same checks as `scar doctor`) with hints, logs with
level and task filters, providers and models, and *Export diagnostic bundle* (redacted, never includes secrets).

## Games and presentations

With `game_mode = auto` (Settings → App), when a full-screen game or presentation is in the foreground SCAR defers
non-urgent notifications and speech, and does not start live automation or local GPU inference unless you
explicitly asked for it. Set it to `off` to disable this.

## Themes and accessibility

Light, dark or follow Windows (Settings → App → Appearance). Full keyboard navigation with visible focus, screen
reader labels, and reduced motion when Windows asks for it. Window size and position are remembered.
