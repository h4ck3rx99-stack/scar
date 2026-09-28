# ADR 0014: Desktop app is Tauri 2 + React over a local app API

Status: accepted (2026-09-27)

## Decision

The desktop app is a Tauri 2 shell (Rust, system WebView2) with a React 19 + TypeScript frontend. It never contains
SCAR logic. It talks to the Python runtime only through the versioned app API (`/api/v1`): HTTP plus one WebSocket on
127.0.0.1, authenticated by a per-start random token written to `%LOCALAPPDATA%\SCAR\app-api.json` (user-only ACL),
with Host/Origin checks, exact-origin CORS and an auth-failure rate limit. TypeScript types are generated from the
Pydantic models (`scripts/gen_api_types.py`), so the contract can't drift silently.

The shell attaches to a running runtime (daemon, CLI or another app instance) or starts one as a detached sidecar
with a bounded watchdog (3 restarts in 10 minutes). The CLI keeps working unchanged against the same runtime.

## Why

- Electron is excluded by the brief. Tauri uses the WebView2 runtime that ships with Windows 11 (small installer,
  ~13 MB) and a Rust shell with a narrow capability set.
- One runtime, many clients: CLI, voice, app and tray all see the same tasks and approvals, and there is one source of
  truth for "verified".
- A local HTTP/WS API is testable without the shell (pytest contract tests, Playwright e2e against the Vite build).

## Consequences

- Approvals from the app carry `args_hash` and `user_gesture`; HIGH/CRITICAL approvals need the main window (never a
  toast).
- The frontend's CSP allows `connect-src` only to 127.0.0.1; markdown renders with raw HTML skipped.
