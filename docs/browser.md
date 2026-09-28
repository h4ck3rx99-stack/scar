# Browser

SCAR drives a real browser with **Playwright**, using a **dedicated persistent SCAR profile**
(`%LOCALAPPDATA%\SCAR\browser-profile`), never your own browser profile. It uses **Opera GX** when it is installed,
otherwise Edge, otherwise Playwright's bundled Chromium. Set `SCAR_BROWSER_CHANNEL` (`opera`, `msedge`, `chrome`,
`chromium`) to force one, and run `uv run playwright install chromium` to get the bundled one. If the preferred browser
won't start, SCAR falls back to the next one and logs why. See [ADR 0015](adr/0015-browsers.md).

"Open this website" is different: the page opens in **your Windows default browser**, in your normal profile. SCAR
doesn't assume Chrome. If you name a browser ("open opera gx and go to github.com", "in edge"), that browser opens.

## Capabilities

`browser.open` (verified by URL, load state and HTTP status), `navigate` (back, forward, reload), `tabs`, `click`,
`type`, `press`, `select`, `scroll`, `extract` (main article text, all text, links, tables, a selector, or the
accessibility snapshot), `screenshot`, `wait`, `download`, `upload`, `state` and `close`.

Elements are located by **role + accessible name**, visible text, label, placeholder, or a CSS selector as a last
resort, never by guessing coordinates. When several elements match, SCAR asks for an index. For UIs that are only
visual, `vision.click` grounds through UI Automation → OCR → set-of-marks vision.

## Safety

* Page content is untrusted: it is wrapped as data for the model and taints any value lifted from it.
* Clicking things labelled submit, send, post, delete or confirm is HIGH risk. Buy, pay, order or checkout is
  CRITICAL (typed confirmation).
* Password, one-time-code and payment fields are detected from the live DOM (`type=password`,
  `autocomplete=cc-*`/`*-password`). SCAR refuses unless the call is marked `sensitive_field`, which is always
  CRITICAL.
* Downloads go to `SCAR_DOWNLOADS_DIR` and are **never executed automatically**. Opening a file that carries the
  internet Zone.Identifier mark is CRITICAL.
* Uploads only come from allowed folders; secret files are denied.
* Navigation to a new domain taken from page content, for form submission, asks first.

## Crashes

If a tab or the browser process crashes (or the window is closed), the next browser call relaunches the profile and
restores up to five previously open tabs.

## Attaching to your own Chrome (optional, advanced)

Recent Chrome versions block remote debugging on the default profile. To let SCAR drive a Chrome you started
yourself, start it with a **separate** user-data directory:

```
"C:\Program Files\Google\Chrome\Application\chrome.exe" --remote-debugging-port=9222 --user-data-dir=%LOCALAPPDATA%\ChromeDebug
```

SCAR's default is its own profile. Attaching to a CDP endpoint is not wired into the default configuration,
because it would expose that profile's logged-in sessions to automation.

## Research vs automation

"Find information about X" uses **web research** (`web.search` → `web.fetch` → extract → summarise with sources)
without opening a browser. "Open this site and fill this form" or "search Google for X and open the third result"
uses browser automation. `web.fetch` respects robots.txt, paces one request per second per domain, caches for
30 minutes, and records every fetched URL. Documents can only cite URLs fetched in the same task.
