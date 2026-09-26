# ADR 0001: Runtime stack: Python 3.11, uv, asyncio, pydantic v2

Status: accepted (2026-09-26)

## Decision

SCAR targets Python 3.11 (3.12-compatible) managed by uv. One asyncio runtime owns everything; blocking and COM/UIA work runs in dedicated worker threads (UIA on a single STA thread with COM initialised). Pydantic v2 models are the frozen interfaces (core/types.py); pydantic-settings loads configuration.

## Alternatives and rationale

Considered anyio: not needed — asyncio covers every integration used (Playwright, httpx, subprocess).
