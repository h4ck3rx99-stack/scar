# ADR 0013: Deterministic fast path before any LLM

Status: accepted (2026-09-26)

## Decision

Common intents (open app/folder in editor, URLs, screenshots, system info, processes, media, windows, reminders, memory, run tests, watch a process, status/cancel) are matched by a regex grammar with entity resolution (memory aliases → Windows Search index → Everything → bounded scan). They still pass through the full tool pipeline. When no model is reachable, the fast path is SCAR's working subset.
