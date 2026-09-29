"""Tools the assistant can use. One implementation serves both the fast router and LLM tool calls.

Each tool speaks its own short confirmation immediately, then (for window actions) waits for the avatar to
fly to the real button before acting, so voice, animation and OS action overlap instead of queueing.
A tool returns FALLTHROUGH when the router guessed wrong and the LLM should handle the sentence instead.
"""
import asyncio
import datetime
import logging
import time

from jarvis import config
from jarvis.hands import actions as A
from jarvis.hands import capture as C
from jarvis.hands import uia
from jarvis.hands.windows import Rect, Window, find_window, foreground_window, list_windows
from jarvis.skills import calc, files, reminders, screen, store, system, web

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

S = {"type": "string"}
I = {"type": "integer"}


def _fn(name, desc, props=None, required=None):
    return {"type": "function", "function": {"name": name, "description": desc, "parameters": {
        "type": "object", "properties": props or {}, **({"required": required} if required else {})}}}


SCHEMA += [
    _fn("look_at_screen", "Look at the screen (or one app's window) to answer a question about what's shown: errors, code, pages, images.",
        {"question": S, "app": {"type": "string", "description": "optional app/window to look at; default whole screen"}}, ["question"]),
    _fn("read_screen", "Read out or summarize the text in a window or the screen.", {"app": S}),
    _fn("system_status", "Battery, CPU, RAM, GPU, disk space, uptime, or what's using memory.",
        {"what": {"type": "string", "enum": ["all", "battery", "cpu", "memory", "gpu", "disk", "top_apps"]}}),
    _fn("set_volume", "Set volume to a level (0-100) or change it by an amount (+/-).", {"level": I, "change": I}),
    _fn("set_brightness", "Set screen brightness to a level (0-100) or change it by an amount (+/-).", {"level": I, "change": I}),
    _fn("set_timer", "Start a countdown timer.", {"duration": {"type": "string", "description": "e.g. '5 minutes'"}, "label": S}, ["duration"]),
    _fn("set_reminder", "Remind the user of something later.",
        {"text": S, "when": {"type": "string", "description": "'in 20 minutes' or 'at 7:30 pm'"}}, ["text", "when"]),
    _fn("list_reminders", "Say which timers, alarms and reminders are pending."),
    _fn("cancel_reminders", "Cancel all timers or reminders.", {"kind": {"type": "string", "enum": ["timer", "reminder", "all"]}}),
    _fn("take_note", "Save a note.", {"text": S}, ["text"]),
    _fn("read_notes", "Read back saved notes."),
    _fn("add_todo", "Add an item to the to-do / shopping list.", {"item": S}, ["item"]),
    _fn("list_todos", "Read the to-do list."),
    _fn("complete_todo", "Mark a to-do item as done.", {"item": S}, ["item"]),
    _fn("remember", "Remember a fact about the user for the future (name, preferences...).", {"fact": S}, ["fact"]),
    _fn("forget", "Forget a remembered fact.", {"fact": S}, ["fact"]),
    _fn("weather", "Current weather and forecast, for here or a named city.", {"city": S}),
    _fn("wikipedia", "Look up a person, place, thing or event on Wikipedia.", {"topic": S}, ["topic"]),
    _fn("calculate", "Exact arithmetic, percentages, powers, square roots.", {"expression": S}, ["expression"]),
    _fn("open_folder", "Open a folder like downloads, documents, desktop, pictures.", {"name": S}, ["name"]),
    _fn("find_file", "Find and open a file by (part of) its name.", {"name": S}, ["name"]),
    _fn("clipboard", "Read, summarize or explain the clipboard or the selected text.",
        {"action": {"type": "string", "enum": ["read", "summarize", "explain", "translate"]}, "source": {"type": "string", "enum": ["clipboard", "selection"]}}),
    _fn("type_text", "Type text into the active window.", {"text": S}, ["text"]),
    _fn("screenshot", "Save a screenshot to Pictures/Jarvis."),
    _fn("lock_pc", "Lock the computer."),
    _fn("arrange_windows", "Put the two most recent windows side by side.", {}),
    _fn("briefing", "A short briefing: time, weather, battery, reminders and to-dos (e.g. for 'good morning')."),
]

IGNORE_TOOL = _fn("ignore", "The message was not meant for you (background talk, video, someone else). Say nothing.")

# Tools that return information for the LLM to phrase, instead of speaking a fixed confirmation.
DATA_TOOLS = {"list_windows", "read_screen", "system_status", "list_reminders", "read_notes", "list_todos", "weather",
              "wikipedia", "clipboard", "briefing", "look_at_screen"}


class Tools:
    def __init__(self, ui, speak, memory, scheduler=None):
        self.ui = ui                # UiLink
        self.speak = speak          # async fn(text) - queues speech, returns immediately
        self.memory = memory
        self.scheduler = scheduler  # reminders.Scheduler
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

    async def _grab_point(self, w: Window):
        f = w.frame
        return f.left + f.width // 2, f.top + max(12, int(16 * w.dpi_scale))

    async def _proxy(self, w: Window, target, grab, effect, duration_ms, finalize):
        """Swap the real window for a rubbery card, animate the card (move / close / minimize / maximize),
        run `finalize` (the real OS action) while the window is invisible, then reveal it and drop the card."""
        loop = asyncio.get_running_loop()
        name, bounds = await loop.run_in_executor(None, C.snapshot, w)
        ready, arrived = loop.create_future(), loop.create_future()
        self.ui.proxy_waiters[(w.hwnd, "ready")] = ready
        self.ui.proxy_waiters[(w.hwnd, "arrived")] = arrived
        hidden = False
        try:
            await self.ui.send("proxy_start", window_id=hex(w.hwnd), texture_url=f"http://127.0.0.1:{config.WS_PORT}/cap/{name}",
                               bounds=bounds, target=target.as_dict(), grab={"x": int(grab[0]), "y": int(grab[1])},
                               effect=effect, duration_ms=duration_ms)
            try:
                # Hide the real window only once the card is on screen, so the swap never flashes.
                await asyncio.wait_for(ready, 1.5)
                C.hide(w)
                hidden = True
                await asyncio.wait_for(arrived, duration_ms / 1000 + 2.5)
            except asyncio.TimeoutError:
                log.warning("cloth proxy handshake timed out")
            finalize(w)
            await asyncio.sleep(0.12 if effect != "move" else 0.05)
        finally:
            self.ui.proxy_waiters.pop((w.hwnd, "ready"), None)
            self.ui.proxy_waiters.pop((w.hwnd, "arrived"), None)
            if hidden:
                C.reveal(w.hwnd)
            await self.ui.send("proxy_end", window_id=hex(w.hwnd),
                               final_bounds=(w.frame.as_dict() if w.alive else bounds))

    async def _cloth_move(self, w: Window, target):
        """Animate the window as a rubbery card from its current frame to `target`, then place the real window."""
        if not self.ui.clients or w.minimized:
            A.set_frame(w, target)
            return
        if w.maximized:
            A.restore(w)
            await asyncio.sleep(0.15)
        grab = await self._grab_point(w)
        await self.ui.fly_to(*grab, purpose="grab", duration_ms=500)
        await self._proxy(w, target, grab, "move", 900, lambda win: A.set_frame(win, target))

    async def _press_button(self, w: Window, action: str):
        """The physical version of minimize / maximize / close: fly to the real caption button, hover it,
        poke it, then play the window's reaction as a card before the real action happens underneath."""
        do = {"minimize": A.minimize, "maximize": A.maximize, "close": A.close}[action]
        if not self.ui.clients:
            do(w)
            return
        button = "restore" if action == "maximize" and w.maximized else action
        rect, how = await uia.caption_button(w, button)
        point = (rect.left + rect.width // 2, rect.top + rect.height // 2)
        log.info("%s %s button at %s (%s)", action, w.spoken_name, point, how)
        arrived = await self.ui.fly_to(*point, purpose=action, button=rect.as_dict())
        if arrived and config.REAL_CLICK:
            A.real_click(*point)
            return
        if action == "maximize" and w.maximized:
            A.restore(w)  # pressing maximize on a maximized window restores it; no card needed
            return
        f = w.frame
        if action == "close":
            target = Rect(point[0] - 8, point[1] - 5, point[0] + 8, point[1] + 5)
            grab, ms = point, 520
        elif action == "minimize":
            area = A.work_area(w)
            cx = (f.left + f.right) // 2
            target = Rect(cx - f.width // 16, area.bottom - f.height // 16, cx + f.width // 16, area.bottom)
            grab, ms = (cx, f.bottom), 560
        else:
            target = A.work_area(w)
            grab, ms = point, 520
        await self._proxy(w, target, grab, action, ms, do)

    async def _present(self, before: set):
        """After launching an app, wait for its window and fly over to show it off."""
        for _ in range(40):
            await asyncio.sleep(0.1)
            new = [w for w in list_windows() if w.hwnd not in before]
            if new:
                w = new[0]
                self.memory.last_window = w
                await asyncio.sleep(0.35)  # let it finish its own opening animation
                gx, gy = await self._grab_point(w)
                await self.ui.fly_to(gx, gy, purpose="present")
                return w
        return None

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
                await self.speak(f"Closing {name}.")
                await self._press_button(w, "close")
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
            await self._press_button(w, action)
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
        before = {w.hwnd for w in list_windows()}
        resolved = await loop.run_in_executor(None, A.open_app, key)
        if resolved:
            await self.speak(f"Opening {name}.")
            if self.ui.clients:
                await self._present(before)
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

    # ------------------------------------------------------------- the eyes
    def _target_or_screen(self, app):
        if app and app.lower() not in ("screen", "the screen", "my screen", "everything"):
            return self._resolve(app)
        return None

    async def t_look_at_screen(self, question="What's on my screen?", app=None, from_router=False):
        from PIL import ImageGrab
        await self.speak("Let me take a look.")
        w = self._target_or_screen(app)
        if w and not w.minimized:
            f = w.frame
            img = ImageGrab.grab(bbox=(f.left, f.top, f.right, f.bottom), all_screens=True)
        else:
            img = ImageGrab.grab()
        ocr = ""
        try:
            ocr = await screen.ocr_image(img)
        except Exception as e:  # noqa: BLE001 - OCR is a helper, the image still goes to the model
            log.warning("ocr failed: %s", e)
        # ~1280 px keeps the image around 1k vision tokens: detailed enough, still quick.
        if img.width > 1280:
            img = img.resize((1280, round(img.height * 1280 / img.width)))
        return {"image": img.convert("RGB"), "ocr": ocr, "question": question}

    async def t_read_screen(self, app=None, from_router=False):
        w = self._target_or_screen(app) or foreground_window()
        text = await screen.read_window(w)
        return f"Text on {w.spoken_name if w else 'the screen'}: {text[:3000]}" if text.strip() else "No readable text found."

    # ------------------------------------------------------------ the system
    async def t_system_status(self, what="all", from_router=False):
        loop = asyncio.get_running_loop()
        if what == "top_apps":
            return "Using the most memory: " + ", ".join(await loop.run_in_executor(None, system.top_processes))
        info = await loop.run_in_executor(None, system.status)
        keys = {"battery": ("battery_percent", "charging", "battery_time_left"), "cpu": ("cpu_percent",),
                "memory": ("ram_used_percent", "ram_free_gb"), "gpu": ("gpu_percent", "vram_used_gb", "vram_total_gb", "gpu_temp_c"),
                "disk": ("disk_free_gb", "disk_used_percent")}.get(what)
        return {k: v for k, v in info.items() if not keys or k in keys}.__repr__()

    async def t_set_volume(self, level=None, change=None, from_router=False):
        v = system.set_volume(level) if level is not None else system.change_volume(int(change or 10))
        await self.ui.send("hud", kind="volume", value=v)
        await self.speak(f"Volume {v} percent.")
        return "done"

    async def t_set_brightness(self, level=None, change=None, from_router=False):
        loop = asyncio.get_running_loop()
        fn = (lambda: system.set_brightness(level)) if level is not None else (lambda: system.change_brightness(int(change or 10)))
        v = await loop.run_in_executor(None, fn)
        if v is None:
            await self.speak("I can't control this screen's brightness.")
            return "unsupported"
        await self.ui.send("hud", kind="brightness", value=v)
        await self.speak(f"Brightness {v} percent.")
        return "done"

    async def t_screenshot(self, from_router=False):
        await self.ui.send("hud", kind="flash", value=0)
        path = await asyncio.get_running_loop().run_in_executor(None, system.screenshot)
        await self.speak("Screenshot saved in Pictures, Jarvis folder.")
        return f"saved {path}"

    async def t_lock_pc(self, from_router=False):
        await self.speak("Locking. See you soon.")
        await asyncio.sleep(1.2)
        system.lock()
        return "done"

    # -------------------------------------------------------- time keeping
    async def t_set_timer(self, duration, label=None, from_router=False):
        secs = reminders.parse_duration(str(duration))
        if not secs:
            await self.speak("How long should the timer be?")
            return "no duration"
        self.scheduler.add(label or f"{reminders.human_delta(secs)} timer", time.time() + secs, "timer")
        await self.speak(f"Timer set for {reminders.human_delta(secs)}.")
        return "done"

    async def t_set_reminder(self, text, when, from_router=False):
        secs = reminders.parse_duration(when)
        if secs:
            due = time.time() + secs
            said = f"in {reminders.human_delta(secs)}"
        else:
            dt = reminders.parse_clock(when)
            if not dt:
                await self.speak("When should I remind you?")
                return "no time"
            due = dt.timestamp()
            day = "tomorrow " if dt.date() > datetime.date.today() else ""
            said = f"{day}at {dt.strftime('%I:%M %p').lstrip('0')}"
        self.scheduler.add(text, due, "reminder")
        await self.speak(f"Okay, I'll remind you {said}.")
        return "done"

    async def t_list_reminders(self, from_router=False):
        items = self.scheduler.pending()
        if not items:
            return "Nothing scheduled."
        now = time.time()
        return "; ".join(f"{r['kind']} '{r['text']}' in {reminders.human_delta(r['due'] - now)}" for r in items[:8])

    async def t_cancel_reminders(self, kind="all", from_router=False):
        n = self.scheduler.cancel(None if kind == "all" else kind)
        await self.speak(f"Cancelled {n}." if n else "Nothing to cancel.")
        return "done"

    # -------------------------------------------------------- notes & memory
    async def t_take_note(self, text, from_router=False):
        store.add_note(text)
        await self.speak("Noted.")
        return "done"

    async def t_read_notes(self, from_router=False):
        data = store.notes.load()[-8:]
        return "Notes: " + " | ".join(f"({n['at']}) {n['text']}" for n in data) if data else "No notes yet."

    async def t_add_todo(self, item, from_router=False):
        n = store.add_todo(item)
        await self.speak(f"Added {item}. That's {n} on your list.")
        return "done"

    async def t_list_todos(self, from_router=False):
        items = [t["text"] for t in store.todos.load() if not t["done"]]
        return "To-do: " + "; ".join(items) if items else "The to-do list is empty."

    async def t_complete_todo(self, item, from_router=False):
        done = store.complete_todo(item)
        await self.speak(f"Checked off {done}." if done else f"I couldn't find {item} on your list.")
        return "done"

    async def t_remember(self, fact, from_router=False):
        store.remember(fact)
        await self.speak("Got it, I'll remember that.")
        return "done"

    async def t_forget(self, fact, from_router=False):
        n = store.forget(fact)
        await self.speak("Forgotten." if n else "I didn't have that remembered.")
        return "done"

    # ----------------------------------------------------------------- info
    async def t_weather(self, city=None, from_router=False):
        w = await web.weather(city)
        return w if w else "Weather is unavailable (no internet connection)."

    async def t_wikipedia(self, topic, from_router=False):
        w = await web.wikipedia(topic)
        return f"{w['title']}: {w['summary']}" if w else f"Couldn't look up {topic} (offline or no article)."

    async def t_calculate(self, expression, from_router=False):
        try:
            v = calc.evaluate(expression)
        except (ValueError, SyntaxError, ZeroDivisionError, OverflowError):
            if from_router:
                return FALLTHROUGH
            await self.speak("I couldn't work that out.")
            return "bad expression"
        await self.speak(f"That's {calc.spoken_number(v)}.")
        return f"= {v}"

    async def t_briefing(self, from_router=False):
        now = datetime.datetime.now()
        parts = [now.strftime("It's %A %d %B, %I:%M %p")]
        w = await web.weather()
        if w:
            parts.append(f"Weather in {w['place']}: {w['now']}; today {w['today']}")
        b = system.battery()
        if b:
            parts.append(f"Battery {b[0]}%{' and charging' if b[1] else ''}")
        rem = self.scheduler.pending() if self.scheduler else []
        if rem:
            parts.append(f"{len(rem)} reminders pending, next: {rem[0]['text']}")
        todos = [t["text"] for t in store.todos.load() if not t["done"]]
        if todos:
            parts.append(f"{len(todos)} to-dos, e.g. {todos[0]}")
        return ". ".join(parts)

    # ---------------------------------------------------------------- files
    async def t_open_folder(self, name, from_router=False):
        p = files.folder(name)
        if not p:
            if from_router:
                return FALLTHROUGH
            await self.speak(f"I don't know a folder called {name}.")
            return "unknown folder"
        await self.speak(f"Opening {p.name}.")
        files.open_path(p)
        return "done"

    async def t_find_file(self, name, from_router=False):
        if name.lower() in ("latest download", "last download", "my latest download"):
            p = files.latest_download()
            hits = [p] if p else []
        else:
            hits = await asyncio.get_running_loop().run_in_executor(None, files.find, name)
        if not hits:
            await self.speak(f"I couldn't find a file called {name}.")
            return "not found"
        await self.speak(f"Opening {hits[0].name}.")
        files.open_path(hits[0])
        return "opened " + str(hits[0])

    # ----------------------------------------------------- clipboard & typing
    async def t_clipboard(self, action="read", source="clipboard", from_router=False):
        loop = asyncio.get_running_loop()
        if source == "selection":
            text = await loop.run_in_executor(None, screen.copy_selection, foreground_window())
        else:
            text = screen.get_clipboard()
        if not text.strip():
            return "The clipboard is empty." if source == "clipboard" else "Nothing is selected."
        return f"Task: {action} this text.\n{text[:4000]}"

    async def t_type_text(self, text, from_router=False):
        target = self.memory.last_window if self.memory.last_window and self.memory.last_window.alive else None
        await asyncio.get_running_loop().run_in_executor(None, screen.type_text, text, target)
        return "done"

    async def t_arrange_windows(self, from_router=False):
        wins = [w for w in list_windows() if not w.minimized][:2]
        if len(wins) < 2:
            await self.speak("I need two open windows for that.")
            return "not enough windows"
        await self.speak(f"{wins[0].spoken_name} on the left, {wins[1].spoken_name} on the right.")
        await self._cloth_move(wins[0], A.layout_rect(wins[0], "left"))
        await self._cloth_move(wins[1], A.layout_rect(wins[1], "right"))
        return "done"

    async def t_smalltalk(self, kind, from_router=False):
        await self.speak("Anytime.")
        return "done"
