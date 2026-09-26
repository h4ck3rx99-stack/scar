# ADR 0012: User applications are launched detached; commands and dev servers run in kill-on-close Job Objects

Status: accepted (2026-09-26)

## Decision

Apps the user asked to open (VS Code, Chrome) must keep running after SCAR exits, so they are started detached and verified by window. Commands, dev servers and model servers are created suspended, assigned to their own Job Object with KILL_ON_JOB_CLOSE, then resumed — the whole tree dies on cancel, timeout, kill switch or SCAR exit.
