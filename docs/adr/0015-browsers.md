# ADR 0015: The user's browser for viewing, Opera GX (own profile) for automation

Status: accepted (2026-09-28)

## Decision

Two different jobs, two different rules:

1. **"Open this website" / handing a page to the user.** The page opens in the user's Windows default browser
   (`HKCU\…\UrlAssociations\https\UserChoice`, see `tools/browser/default.py`), in their normal profile, with
   `os.startfile`. SCAR never assumes Chrome. If the user names a browser ("open opera gx and go to …", "in edge"),
   SCAR opens that one. On the owner's machine the default is Opera GX.
2. **Automation (Playwright: reading pages, filling forms, research).** SCAR drives its own persistent profile
   (`<data>\browser-profile`), never the user's profile, cookies or sessions. `browser_channel = "auto"` prefers
   **Opera GX** when it is installed (the owner's stated preference). SCAR launches the newest versioned `opera.exe`
   directly (≈0.8 s), because the top-level launcher checks for updates first (≈6 s). If Opera GX won't start, SCAR
   falls back to Edge, which ships with Windows 11, and then to Playwright's bundled Chromium. An explicit
   `browser_channel` (`opera`, `msedge`, `chrome`, `chromium`) overrides the choice.

## Evidence

Tested 2026-09-28 in a scratch directory with Opera GX 136 (Chromium 152), headless, with a separate profile. Both the
launcher and the versioned binary started, loaded `example.com` and closed. No automation process remained afterwards,
and the owner's 27 running Opera GX processes were untouched. `BrowserManager` picked `opera` on this machine.

## Consequences

- Opera GX is Chromium-based, but Playwright doesn't officially support it. Opera updates can change behaviour, so the
  fallback chain is part of the design, and the log records `browser_channel_failed` with the reason.
- Because the automation profile is separate, SCAR never sees the user's logged-in sessions. A site needing a login is
  either signed into once in SCAR's own profile by the user, or handed off to the user's browser.
- Tests pin `browser_channel = "chromium"` so CI never depends on the local browser.
