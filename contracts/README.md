# System Contract & Inter-Module Communication Protocol

## Module Boundaries

1. **`/backend` (Agent 1 - The Brain)**
   - **Role**: Python WebSocket Server (`ws://127.0.0.1:8765`), Silero VAD, streaming STT (Whisper), LLM tool caller (Llama-3/Qwen-2.5), Kokoro/Piper TTS, and 5000ms Follow-Up hot-mic timer.
   - **Boundary**: Emits `voice_state` and parsed `os_command` events. Does not directly manipulate OS windows or render 3D meshes.

2. **`/shell` (Agent 2 - The Hands)**
   - **Role**: Tauri (Rust) desktop shell, transparent borderless click-through overlay window, global mouse hooks (`rdev`), Win32/UIAutomation window position finder, and synthetic inputs (`enigo`).
   - **Boundary**: Receives `os_command` from `/backend`, resolves target window coordinates `(x, y)`, notifies `/frontend` via IPC, awaits `destination_reached` signal from `/frontend`, and executes physical window actions.

3. **`/frontend` (Agent 3 - The Body)**
   - **Role**: React Three Fiber (Three.js) running inside Tauri canvas. Renders 15k triangle 3D avatar (dark/light mode), manages avatar state machine (Entry, Roam, Listen, Think, Speak, Fly, Sleep), and GLSL cloth/rubber soft-body tab drag shader.
   - **Boundary**: Receives animation instructions from `/shell` and `/backend`. Emits `destination_reached` when flight animation completes.

---

## Exact WebSocket Data Contracts

### 1. Voice State Event
```json
{
  "event": "voice_state",
  "status": "listening" | "processing" | "speaking"
}
```

### 2. OS Command Event
```json
{
  "event": "os_command",
  "action": "minimize" | "resize" | "click",
  "target_window": "string",
  "coordinates": {
    "x": 1920,
    "y": 1080
  }
}
```

### 3. UI Animation Event
```json
{
  "event": "ui_animation",
  "type": "fly" | "stretch" | "idle",
  "target": {
    "x": 1920,
    "y": 1080
  }
}
```
