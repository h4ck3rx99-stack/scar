"""Tools the assistant can use. One implementation serves both the fast router and LLM tool calls.

Each tool speaks its own short confirmation immediately, then (for window actions) waits for the avatar to
fly to the real button before acting, so voice, animation and OS action overlap instead of queueing.
A tool returns FALLTHROUGH when the router guessed wrong and the LLM should handle the sentence instead.
"""
import asyncio
import datetime
import logging

from jarvis import config
from jarvis.hands import actions as A
from jarvis.hands import capture as C
from jarvis.hands import uia
from jarvis.hands.windows import Window, find_window, list_windows

log = logging.getLogger("tools")
FALLTHROUGH = object()

LAYOUT_NAMES = {"left": "the left half", "right": "the right half", "top": "the top half", "bottom": "the bottom half",
                "top_left": "the top left", "top_right": "the top right", "bottom_left": "the bottom left",
                "bottom_right": "the bottom right", "left_third": "the left third", "right_third": "the right third",
                "center_third": "the middle third", "center": "the center", "next_monitor": "the other screen",
                "left_two_thirds": "the left two thirds", "right_two_thirds": "the right two thirds"}

# JSON schema handed to the LLM (OpenAI tools format).
SCHEMA = [
    {"type": "function", "function": {
        "name": "window_action", "description": "Minimize, maximize, restore, close or focus an open app window.",
        "parameters": {"type": "object", "properties": {
            "app": {"type": "string", "description": "App or window name as the user said it, e.g. 'chrome', 'vs code', 'youtube', or 'it' for the last one"},
            "action": {"type": "string", "enum": ["minimize", "maximize", "restore", "close", "focus"]}},
            "required": ["app", "action"]}}},
    {"type": "function", "function": {
        "name": "move_window", "description": "Move/snap a window to part of the screen or to the other monitor.",
        "parameters": {"type": "object", "properties": {
            "app": {"type": "string"},
            "where": {"type": "string", "enum": list(LAYOUT_NAMES)}},
            "required": ["app", "where"]}}},
    {"type": "function", "function": {
        "name": "resize_window", "description": "Resize a window to an aspect ratio like '16:9' or '4:3', or 'bigger' / 'smaller'.",
        "parameters": {"type": "object", "properties": {
            "app": {"type": "string"}, "preset": {"type": "string", "description": "'16:9', '4:3', '1:1', '21:9', 'bigger' or 'smaller'"}},
            "required": ["app", "preset"]}}},
    {"type": "function", "function": {
        "name": "open", "description": "Open an app (e.g. notepad, spotify, settings) or a well-known website by name (e.g. youtube, gmail).",
        "parameters": {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}}},
    {"type": "function", "function": {
        "name": "open_url", "description": "Open a specific web address in the browser.",
        "parameters": {"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]}}},
    {"type": "function", "function": {
        "name": "web_search", "description": "Search the web or a site (youtube, google, wikipedia, amazon, github, maps) in the browser.",
        "parameters": {"type": "object", "properties": {"engine": {"type": "string"}, "query": {"type": "string"}}, "required": ["engine", "query"]}}},
    {"type": "function", "function": {
        "name": "browser_tab", "description": "Close the current browser tab, open a new tab, or switch to a tab by its title.",
        "parameters": {"type": "object", "properties": {
            "action": {"type": "string", "enum": ["close", "new", "switch"]}, "query": {"type": "string", "description": "tab title to switch to"}},
            "required": ["action"]}}},
    {"type": "function", "function": {
        "name": "media", "description": "System volume and media playback control.",
        "parameters": {"type": "object", "properties": {
            "action": {"type": "string", "enum": ["volume_up", "volume_down", "mute", "play_pause", "next", "previous"]}},
            "required": ["action"]}}},
    {"type": "function", "function": {
        "name": "list_windows", "description": "List the app windows that are currently open.",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "get_time", "description": "Say the current time or date.",
        "parameters": {"type": "object", "properties": {"what": {"type": "string", "enum": ["time", "date"]}}}}},
    {"type": "function", "function": {
        "name": "set_theme", "description": "Switch the assistant's look to dark or light mode.",
        "parameters": {"type": "object", "properties": {"mode": {"type": "string", "enum": ["dark", "light"]}}, "required": ["mode"]}}},
]


class Tools:
    def __init__(self, ui, speak, memory):
        self.ui = ui                # UiLink
        self.speak = speak          # async fn(text) - queues speech, returns immediately
        self.memory = memory
        self.pending_confirm = None  # coroutine factory waiting for yes/no

    async def call(self, name, args, from_router=False):
        fn = getattr(self, f"t_{name}", None)
        if fn is None:
            return f"Unknown tool {name}"
        try:
            result = await fn(from_router=from_router, **(args or {}))
        except TypeError as e:
            log.warning("bad args for %s: %s", name, e)
            return f"Bad arguments for {name}"
        except Exception as e:  # noqa: BLE001 - tell the user instead of dying mid-sentence
            log.exception("tool %s failed", name)
            await self.speak("Sorry, that didn't work.")
            return f"error: {e}"
        return result

    # ------------------------------------------------------------ helpers
    def _resolve(self, app):
        app = (app or "").strip()
        if app.lower() in ("it", "that", "this", "that window", "this window", "the window", "them") and self.memory.last_window:
            w = self.memory.last_window
            if w.alive:
                return w
        w = find_window(app)
        if w:
            self.memory.last_window = w
        return w

    async def _fly_and_tap(self, w: Window, button: str, purpose: str):
        point, how = await uia.caption_button_point(w, button)
        log.info("%s %s button at %s (%s)", purpose, w.spoken_name, point, how)
        arrived = await self.ui.fly_to(*point, purpose=purpose)
        if arrived and config.REAL_CLICK:
            A.real_click(*point)
            return True
        return False

    async def _grab_point(self, w: Window):
        f = w.frame
        return f.left + f.width // 2, f.top + max(12, int(16 * w.dpi_scale))

    async def _cloth_move(self, w: Window, target):
        """Animate the window as a rubbery card from its current frame to `target`, then place the real window."""
        if not self.ui.clients or w.minimized:
            A.set_frame(w, target)
            return
        if w.maximized:
            A.restore(w)
            await asyncio.sleep(0.15)
        gx, gy = await self._grab_point(w)
        await self.ui.fly_to(gx, gy, purpose="grab", duration_ms=500)
        loop = asyncio.get_running_loop()
        name, bounds = await loop.run_in_executor(None, C.snapshot, w)
        ready, arrived = loop.create_future(), loop.create_future()
        self.ui.proxy_waiters[(w.hwnd, "ready")] = ready
        self.ui.proxy_waiters[(w.hwnd, "arrived")] = arrived
        hidden = False
        try:
            await self.ui.send("proxy_start", window_id=hex(w.hwnd), texture_url=f"http://127.0.0.1:{config.WS_PORT}/cap/{name}",
                               bounds=bounds, target=target.as_dict(), grab={"x": gx, "y": gy}, duration_ms=900)
            try:
                # Hide the real window only once the card is on screen, so the swap never flashes.
                await asyncio.wait_for(ready, 1.5)
                C.hide(w)
                hidden = True
                await asyncio.wait_for(arrived, 900 / 1000 + 2.5)  # lift + move + settle ≈ 1.7 s at 60 fps
            except asyncio.TimeoutError:
                log.warning("cloth proxy handshake timed out")
            A.set_frame(w, target)
            await asyncio.sleep(0.05)
        finally:
            self.ui.proxy_waiters.pop((w.hwnd, "ready"), None)
            self.ui.proxy_waiters.pop((w.hwnd, "arrived"), None)
            if hidden:
                C.reveal(w.hwnd)
            await self.ui.send("proxy_end", window_id=hex(w.hwnd), final_bounds=w.frame.as_dict())

    async def _not_found(self, app, from_router):
        if from_router:
            return FALLTHROUGH
        await self.speak(f"I couldn't find {app} open.")
        return f"No open window matches '{app}'."

    # -------------------------------------------------------------- tools
    async def t_window_action(self, app, action, from_router=False):
        w = self._resolve(app)
        if not w:
            if from_router and action == "focus":
                return FALLTHROUGH       # "show me the weather" is not a window
            return await self._not_found(app, False)
        name = w.spoken_name
        if action == "close" and A.looks_unsaved(w):
            async def do_close():
                await self._fly_and_tap(w, "close", "close")
                A.close(w)
                await self.speak(f"Closed {name}.")
            self.pending_confirm = do_close
            await self.speak(f"{name} has unsaved changes. Close it anyway?")
            return "asked for confirmation"
        verbs = {"minimize": "Minimizing", "maximize": "Maximizing", "restore": "Restoring", "close": "Closing", "focus": "Switching to"}
        await self.speak(f"{verbs[action]} {name}.")
        if action == "focus":
            if not w.minimized:
                gx, gy = await self._grab_point(w)
                await self.ui.fly_to(gx, gy, purpose="focus")
            A.focus(w)
        elif action == "restore":
            A.restore(w)
            A.focus(w)
        else:
            if w.minimized and action != "close":
                A.restore(w)
                await asyncio.sleep(0.2)
            button = "maximize" if action == "maximize" and not w.maximized else ("restore" if action == "maximize" else action)
            clicked = await self._fly_and_tap(w, button, action)
            if not clicked:
                {"minimize": A.minimize, "maximize": A.maximize, "close": A.close}[action](w)
        log.info("%s %s -> minimized=%s maximized=%s alive=%s", action, name, w.minimized, w.maximized, w.alive)
        return f"{action} {name}: done"

    async def t_minimize_all(self, from_router=False):
        await self.speak("Clearing the desktop.")
        for w in list_windows():
            A.minimize(w)
        return "done"

    async def t_move_window(self, app, where, from_router=False):
        w = self._resolve(app)
        if not w:
            return await self._not_found(app, from_router)
        if where == "next_monitor":
            target = A.next_monitor_rect(w)
            if target is None:
                await self.speak("There's only one screen.")
                return "only one monitor"
        elif where in A.LAYOUTS:
            target = A.layout_rect(w, where)
        else:
            return f"unknown position {where}"
        await self.speak(f"Moving {w.spoken_name} to {LAYOUT_NAMES.get(where, where)}.")
        await self._cloth_move(w, target)
        return "done"

    async def t_resize_window(self, app, preset, from_router=False):
        w = self._resolve(app)
        if not w:
            return await self._not_found(app, from_router)
        p = preset.replace(" ", "").replace("by", ":").replace("x", ":").replace("/", ":")
        if p in ("bigger", "larger"):
            target, said = A.scaled_rect(w, 1.25), "bigger"
        elif p in ("smaller", "half"):
            target, said = A.scaled_rect(w, 0.75), "smaller"
        elif ":" in p:
            a, b = p.split(":", 1)
            try:
                rw, rh = float(a), float(b)
            except ValueError:
                return f"bad ratio {preset}"
            target, said = A.aspect_rect(w, rw, rh), f"{a} by {b}"
        else:
            return f"unknown preset {preset}"
        await self.speak(f"Making {w.spoken_name} {said}.")
        await self._cloth_move(w, target)
        return "done"

    async def t_open(self, name, from_router=False):
        key = name.lower().strip()
        url = A.site_url(key)
        if url:
            await self.speak(f"Opening {name}.")
            A.open_url(url)
            return f"opened {url}"
        loop = asyncio.get_running_loop()
        resolved = await loop.run_in_executor(None, A.open_app, key)
        if resolved:
            await self.speak(f"Opening {name}.")
            return f"opened {resolved}"
        if from_router:
            return FALLTHROUGH
        await self.speak(f"I couldn't find an app called {name}.")
        return "not found"

    async def t_open_url(self, url, from_router=False):
        await self.speak("Opening that link.")
        return f"opened {A.open_url(url)}"

    async def t_web_search(self, engine, query, from_router=False):
        await self.speak(f"Searching {engine} for {query}.")
        A.open_url(A.search_url(engine, query))
        return "done"

    def _browser(self):
        for w in list_windows():
            if w.exe in ("opera.exe", "chrome.exe", "msedge.exe", "firefox.exe", "brave.exe", "vivaldi.exe"):
                return w
        return None

    async def t_browser_tab(self, action, query=None, from_router=False):
        w = self._browser()
        if not w:
            await self.speak("No browser is open.")
            return "no browser"
        loop = asyncio.get_running_loop()
        if action == "close":
            await self.speak("Closing the tab.")
            A.close_tab(w)
        elif action == "new":
            await self.speak("New tab.")
            A.new_tab(w)
        elif action == "switch" and query:
            hit = await uia.select_tab(w, query)
            if hit:
                A.focus(w)
            else:
                hit = await loop.run_in_executor(None, A.cycle_to_tab, w, query)
            await self.speak(f"Here's {query}." if hit else f"I couldn't find a {query} tab.")
            return "done" if hit else "not found"
        return "done"

    async def t_media(self, action, from_router=False):
        times = 5 if action in ("volume_up", "volume_down") else 1
        A.media(action, times)
        return "done"

    async def t_list_windows(self, from_router=False):
        wins = list_windows()
        return "Open windows: " + "; ".join(f"{w.spoken_name} ({w.title[:60]})" for w in wins[:15]) if wins else "No windows are open."

    async def t_set_theme(self, mode, from_router=False):
        await self.ui.send("theme", mode=mode)
        await self.speak(f"{mode.title()} mode.")
        return "done"

    async def t_get_time(self, what="time", from_router=False):
        now = datetime.datetime.now()
        if what == "date":
            await self.speak(now.strftime("It's %A, %B %d."))
        else:
            await self.speak(now.strftime("It's %I:%M %p.").replace(" 0", " ").replace("It's 0", "It's "))
        return "done"

    async def t_confirm(self, answer, from_router=False):
        pending, self.pending_confirm = self.pending_confirm, None
        if pending is None:
            return FALLTHROUGH if from_router else "nothing to confirm"
        if answer:
            await pending()
        else:
            await self.speak("Okay, leaving it.")
        return "done"

    async def t_smalltalk(self, kind, from_router=False):
        await self.speak("Anytime.")
        return "done"
