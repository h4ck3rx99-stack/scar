"""Scar's newest tools: point-and-click anything by description (vision), WhatsApp messages, clipboard history,
"watch me do this" macros, and the evening recap."""
import asyncio
import base64
import io
import logging
import os
import re
import time
import urllib.parse

from jarvis import runtime
from jarvis.skills.store import Store

log = logging.getLogger("tools")
S = {"type": "string"}
contacts = Store("contacts", {})    # {"rahul": "+919876543210"}


def _fn(name, desc, props=None, required=None):
    return {"type": "function", "function": {"name": name, "description": desc, "parameters": {
        "type": "object", "properties": props or {}, **({"required": required} if required else {})}}}


EXTRA_SCHEMA = [
    _fn("point_click", "Click something on screen by describing it: 'the second video', 'the red button', 'the profile "
                       "picture', or 'that' for what you just described from the screen.",
        {"target": S, "action": {"type": "string", "enum": ["click", "double_click", "right_click", "hover"]}}, ["target"]),
    _fn("whatsapp", "Send a WhatsApp message to a contact (always read back and confirmed before sending).",
        {"contact": S, "message": S}, ["contact", "message"]),
    _fn("save_contact", "Remember a contact's phone number for WhatsApp.", {"name": S, "number": S}, ["name", "number"]),
    _fn("clipboard_history", "Recent things the user copied: list them, paste one (by position like 'previous' or by "
                             "content like 'the link'), or clear the history.",
        {"action": {"type": "string", "enum": ["list", "paste", "clear"]}, "which": S, "query": S}, ["action"]),
    _fn("macro", "Record the user's clicks and typing as a named macro ('watch me do this'), stop/save it, replay it, "
                 "list or delete macros.",
        {"action": {"type": "string", "enum": ["start", "stop", "save", "play", "list", "delete"]}, "name": S}, ["action"]),
    _fn("evening_recap", "End-of-day recap: what the user worked on today, open to-dos, and tomorrow's schedule."),
    _fn("code_assist", "Coding practice pages (learning.ccbp.in / NxtWave, LeetCode, HackerRank...): read the problem on "
                       "screen and solve it into the page's code editor, give a hint, explain the problem, or check/fix the "
                       "user's code. Use this instead of type_text for code on those pages.",
        {"action": {"type": "string", "enum": ["solve", "hint", "explain", "check"]}, "language": S}, ["action"]),
    _fn("code_run", "Press the page's Run button (or Submit, which asks first).",
        {"which": {"type": "string", "enum": ["run", "submit"]}}, ["which"]),
]
EXTRA_DATA_TOOLS = {"evening_recap", "clipboard_history_list"}

_VISUAL = re.compile(r"\b(?:first|second|third|fourth|fifth|last|top|bottom|left|right|middle|red|blue|green|yellow|orange|"
                     r"purple|pink|black|white|grey|gray|image|thumbnail|icon|picture|photo|logo|avatar|video|arrow|"
                     r"big|small|circle|round)\b", re.I)


class ExtraTools:
    """Mixed into Tools (uses self.speak, self.ui, self.memory, self.pending_confirm)."""

    last_seen = ""        # Scar's last description of the screen (for "click that")
    _vision = None

    # ------------------------------------------------------------ pointing (vision)
    async def vision_locate(self, description, w=None):
        """Screen point of a described thing, via the vision model's bounding box (bbox_2d, 0-1000). Or None."""
        from PIL import ImageGrab
        from jarvis.brain.llm import LLM
        from jarvis.hands.capture import window_image
        loop = asyncio.get_running_loop()
        if w is not None and not w.minimized:
            img = await loop.run_in_executor(None, window_image, w)
            ox, oy = w.frame.left, w.frame.top
        else:
            img = await loop.run_in_executor(None, ImageGrab.grab)
            ox = oy = 0
        full_w, full_h = img.size
        if img.width > 1280:
            img = img.resize((1280, round(img.height * 1280 / img.width)))
        buf = io.BytesIO()
        img.convert("RGB").save(buf, "JPEG", quality=85)
        b64 = base64.b64encode(buf.getvalue()).decode()
        if self._vision is None:
            ExtraTools._vision = LLM()
        messages = [{"role": "user", "content": [
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
            {"type": "text", "text": f"Locate {description} in the screenshot and output its bbox_2d in JSON."}]}]
        out = ""
        try:
            async for kind, data in self._vision.stream(messages, max_tokens=60):
                if kind == "text":
                    out += data
        except Exception as e:  # noqa: BLE001
            log.warning("vision locate failed: %s", e)
            return None
        m = re.search(r'"bbox_2d"\s*:\s*\[\s*([\d.]+)\s*,\s*([\d.]+)\s*,\s*([\d.]+)\s*,\s*([\d.]+)', out)
        if not m:
            log.info("vision locate: no box for %r (%r)", description, out[:120])
            return None
        x1, y1, x2, y2 = map(float, m.groups())
        if not (0 <= x1 < x2 <= 1000 and 0 <= y1 < y2 <= 1000):
            return None
        cx, cy = (x1 + x2) / 2000 * full_w, (y1 + y2) / 2000 * full_h
        bw, bh = (x2 - x1) / 1000 * full_w, (y2 - y1) / 1000 * full_h
        return int(ox + cx), int(oy + cy), int(bw), int(bh)

    async def t_point_click(self, target, action="click", from_router=False):
        from jarvis.hands import actions as A
        from jarvis.hands.windows import foreground_window
        w = foreground_window()
        desc = target.strip()
        if desc.lower() in ("that", "it", "this", "that one", "this one", "the one you mentioned", "the one you said"):
            if not self.last_seen:
                await self.speak("Which one? Tell me what it says or looks like.")
                return "nothing described yet"
            desc = f"the thing this description refers to: \"{self.last_seen[:300]}\""
        await self.speak(f"Looking for {target}." if len(target) < 40 else "Looking for it.")
        hit = await self.vision_locate(desc, w)
        if not hit:
            await self.speak(f"I can't spot {target} on the screen.")
            return f"not found: {target}"
        x, y, bw, bh = hit
        await self.ui.fly_to(x, y, purpose="click", button={"x": x - bw // 2, "y": y - bh // 2, "width": bw, "height": bh})
        if w is not None:
            A.focus(w)
            await asyncio.sleep(0.1)
        if action == "hover":
            A.user32.SetCursorPos(x, y)
        else:
            _click(x, y, action)
        if w is not None:
            self.memory.last_window = w
        return f"{action} on {target} at ({x}, {y})"

    # ------------------------------------------------------------ WhatsApp
    async def t_save_contact(self, name, number, from_router=False):
        digits = re.sub(r"[^\d+]", "", number)
        if len(re.sub(r"\D", "", digits)) < 8:
            await self.speak("That doesn't sound like a full phone number.")
            return "bad number"
        if not digits.startswith("+") and len(digits) == 10:
            digits = "+91" + digits               # a bare 10-digit number: India
        data = contacts.load()
        data[name.lower().strip()] = digits
        contacts.save(data)
        await self.speak(f"Saved {name.title()}'s number.")
        return f"saved {name}"

    async def t_whatsapp(self, contact, message, from_router=False):
        name = contact.strip()
        message = message.strip()
        if not message:
            await self.speak(f"What should I say to {name}?")
            return "no message"
        number = contacts.load().get(name.lower())

        async def send():
            await self._whatsapp_send(name, message, number)

        self.pending_confirm = send
        await self.ui.send("say", text=f"💬 To {name.title()}: {message}")
        await self.speak(f"Message to {name.title()}: {message.rstrip('.!? ')}. Should I send it?")
        return "asked for confirmation"

    async def _whatsapp_front(self):
        from jarvis.hands import actions as A
        from jarvis.hands.windows import list_windows
        for _ in range(40):
            w = next((x for x in list_windows() if "whatsapp" in (x.exe + " " + x.title).lower()), None)
            if w:
                if w.minimized:
                    A.restore(w)
                    await asyncio.sleep(0.3)
                A.focus(w)
                return w
            await asyncio.sleep(0.25)
        return None

    async def _whatsapp_send(self, name, message, number):
        from rapidfuzz import fuzz
        from jarvis.hands import actions as A
        from jarvis.skills import screen
        loop = asyncio.get_running_loop()
        if number:
            os.startfile("whatsapp://send?phone=" + urllib.parse.quote(number.lstrip("+")) + "&text=" + urllib.parse.quote(message))
            w = await self._whatsapp_front()
            if w is None:
                await self.speak("WhatsApp didn't open.")
                return
            await asyncio.sleep(2.5)                   # the chat opens with the text already in the box
        else:
            if not await loop.run_in_executor(None, A.open_app, "whatsapp"):
                await self.speak("I couldn't open WhatsApp.")
                return
            w = await self._whatsapp_front()
            if w is None:
                await self.speak("WhatsApp didn't open.")
                return
            await asyncio.sleep(1.5)
            A.press("ctrl", "f")                       # search chats
            await asyncio.sleep(0.4)
            await loop.run_in_executor(None, screen.type_text, name, w)
            await asyncio.sleep(1.6)
            A.press("down")
            await asyncio.sleep(0.2)
            A.press("enter")
            await asyncio.sleep(1.2)
            # Make sure the chat that opened is really this person before typing anything.
            from jarvis.hands.capture import window_image
            img = await loop.run_in_executor(None, window_image, w)
            header = img.crop((int(img.width * 0.3), 0, img.width, min(img.height, 140)))
            seen = await screen.ocr_image(header)
            if fuzz.partial_ratio(name.lower(), seen.lower()) < 80:
                A.press("escape")
                await self.speak(f"I couldn't find a chat called {name}, so I didn't send anything. "
                                 f"You can save their number: say, save {name}'s number as, then the number.")
                return
            await loop.run_in_executor(None, screen.type_text, message, w)
            await asyncio.sleep(0.3)
        fg = A.user32.GetForegroundWindow()
        if fg != w.hwnd:
            await self.speak("WhatsApp isn't in front any more, so I didn't press send. The message is ready in the box.")
            return
        A.press("enter")
        await self.speak(f"Sent to {name.title()}.")

    # ------------------------------------------------------------ clipboard history
    async def t_clipboard_history(self, action="list", which=None, query=None, from_router=False):
        from jarvis.skills import cliphistory, screen
        from jarvis.hands.windows import foreground_window
        if action == "clear":
            cliphistory.clear()
            await self.speak("Clipboard history cleared.")
            return "cleared"
        if action == "list":
            items = cliphistory.items()[:5]
            if not items:
                await self.speak("You haven't copied anything since I started.")
                return "empty"
            await self.ui.send("say", text="📋 " + "  ·  ".join(f"{i + 1}. {x['text'][:60]}" for i, x in enumerate(items)))
            spoken = "; ".join(f"{['last', 'before that', 'third', 'fourth', 'fifth'][i]}: {x['text'][:80]}" for i, x in enumerate(items[:3]))
            await self.speak(f"Your recent copies. {spoken}.")
            return "listed"
        item = cliphistory.pick(which, query)
        if not item:
            await self.speak("I don't have anything like that in your clipboard history.")
            return "not found"
        w = foreground_window()
        await asyncio.get_running_loop().run_in_executor(None, screen.type_text, item["text"], w)
        return f"pasted {item['text'][:60]!r}"

    # ------------------------------------------------------------ macros
    async def t_macro(self, action, name=None, from_router=False):
        from jarvis.skills import macros
        rec = macros.recorder
        loop = asyncio.get_running_loop()
        if action == "start":
            if rec.active:
                await self.speak("I'm already watching.")
                return "already recording"
            self._macro_name = name
            await self.speak("Okay, I'm watching. Do the task, then say: stop recording." +
                             ("" if name else " I'll ask you what to call it."))
            await asyncio.sleep(0.2)
            await loop.run_in_executor(None, rec.start)
            await self.ui.send("say", text="⏺ Recording your clicks and typing… say “stop recording” when done")
            return "recording"
        if action == "stop":
            if not rec.active:
                if from_router:
                    from jarvis.brain.tools import FALLTHROUGH
                    return FALLTHROUGH
                await self.speak("I wasn't recording.")
                return "not recording"
            events = rec.stop()
            n = len([e for e in events if e["type"] == "click" and e["down"]])
            keys = len([e for e in events if e["type"] == "key" and e["down"]])
            name = name or getattr(self, "_macro_name", None)
            if not events:
                await self.speak("I didn't see you do anything, so there's nothing to save.")
                return "empty"
            if name:
                rec.save(name)
                await self.speak(f"Got it: {n} clicks and {keys} key presses, saved as {name}. Say run {name} to replay it.")
                return f"saved {name}"
            await self.speak(f"Got {n} clicks and {keys} key presses. What should I call it? Say: call it, then a name.")
            return "needs a name"
        if action == "save":
            if not rec.pending:
                if from_router:
                    from jarvis.brain.tools import FALLTHROUGH
                    return FALLTHROUGH
                await self.speak("There's no recording waiting for a name.")
                return "nothing to save"
            rec.save(name or "my macro")
            await self.speak(f"Saved as {name}. Say run {name} whenever you want it again.")
            return f"saved {name}"
        if action == "list":
            names = macros.names()
            await self.speak(("Your macros: " + ", ".join(names) + ".") if names else "You don't have any macros yet. Say: watch me do this.")
            return "listed"
        if action == "delete":
            ok = macros.delete(name or "")
            await self.speak(f"Deleted {name}." if ok else f"I don't have a macro called {name}.")
            return "deleted" if ok else "not found"
        if action == "play":
            real = macros.find(name or "")
            if not real:
                if from_router:
                    from jarvis.brain.tools import FALLTHROUGH
                    return FALLTHROUGH
                await self.speak(f"I don't have a macro called {name}.")
                return "not found"
            await self.speak(f"Running {real}. Press Escape to stop me.")
            await asyncio.sleep(0.8)
            n = await loop.run_in_executor(None, macros.play, real)
            if n < 0:
                await self.speak("Stopped.")
                return "aborted"
            return f"played {real}"
        return "unknown macro action"

    # ------------------------------------------------------------ evening recap
    async def t_evening_recap(self, from_router=False):
        from jarvis.skills import store
        parts = []
        try:
            parts.append("Today's activity: " + str(await self.t_my_activity("today")))
        except Exception as e:  # noqa: BLE001
            log.warning("recap activity failed: %s", e)
        todos = [t["text"] for t in store.todos.load() if not t.get("done")]
        parts.append("Open to-dos: " + ("; ".join(todos[:8]) if todos else "none"))
        try:
            parts.append("Tomorrow: " + str(await self.t_my_schedule("tomorrow")))
        except Exception as e:  # noqa: BLE001
            log.warning("recap schedule failed: %s", e)
        try:
            parts.append("Reminders: " + str(await self.t_list_reminders()))
        except Exception:  # noqa: BLE001
            pass
        return ("Give a warm, short end-of-day recap (4-5 sentences): what they worked on (top apps, rough time), "
                "open to-dos, and what's on tomorrow. Data:\n" + "\n".join(parts))


CODE_SYSTEM = """You are an expert programming tutor helping a student on a coding-practice website (like learning.ccbp.in).
You get OCR text of their screen: the problem statement, sample input/output, and whatever is in the code editor.
Language: use the one the page or the editor shows ({language}); if unclear, Python 3.
Task: {task}"""
CODE_TASKS = {
    "solve": "Write a complete, correct solution that reads input and prints output EXACTLY in the format the problem "
             "shows (no extra prompts or text). Reply with ONLY the code, no markdown fences, no explanation.",
    "check": "Find what is wrong with the student's code in the editor (logic, syntax, output format). First line: a short "
             "spoken explanation (max 2 sentences) starting with 'EXPLAIN:'. Then the full corrected code after a line "
             "'CODE:'. No markdown fences.",
    "hint": "Give ONE helpful hint in 1-2 short spoken sentences that moves them forward without giving the full solution.",
    "explain": "Explain what the problem asks in 2-4 short spoken sentences: the input, what to compute, and the output. "
               "No code.",
}


_PROBLEM_SIGNS = [r"sample\s+input", r"sample\s+output", r"expected\s+output", r"\binput\s+format\b", r"\boutput\s+format\b",
                  r"\bconstraints?\b", r"\btest\s*cases?\b", r"\bexplanation\b", r"write\s+a\s+(?:program|function|query|code)",
                  r"\bthe\s+(?:first|second|next)\s+line\s+(?:of\s+input\s+)?(?:contains|is)", r"\breturn\s+the\b",
                  r"\b(?:submit|run)\b.{0,40}\b(?:submit|run|reset)\b", r"\bcoding\s+practice\b", r"\bexample\s*\d\b"]


def looks_like_problem(text: str) -> bool:
    """A real coding problem shows at least two of these signs (a privacy-error page saying "for example" is not one)."""
    return sum(1 for p in _PROBLEM_SIGNS if re.search(p, text, re.I | re.S)) >= 2


def _strip_fences(code: str) -> str:
    code = code.strip()
    m = re.search(r"```[\w+#-]*\n(.*?)```", code, re.S)
    if m:
        code = m.group(1)
    return code.strip("\n") + "\n"


def _guess_language(text: str) -> str:
    t = text.lower()
    for lang, pat in (("Python 3", r"\bpython\b|\bdef \w+\(|print\("), ("JavaScript", r"\bjavascript\b|\bconsole\.log\b|\bfunction \w+\("),
                      ("SQL", r"\bselect\b.+\bfrom\b|\bsqlite\b|\bsql\b"), ("Java", r"\bpublic static void main\b|\bjava\b"),
                      ("C++", r"#include|\bcout\b|\bc\+\+\b"), ("HTML/CSS", r"<div|\bhtml\b|\bcss\b")):
        if re.search(pat, t):
            return lang
    return "Python 3"


class CodeTools:
    """Mixed into Tools: coding-practice help on learning.ccbp.in and similar sites."""

    async def _page_text(self, w):
        from jarvis.hands.capture import window_image
        from jarvis.skills.screen import ocr_image
        img = await asyncio.get_running_loop().run_in_executor(None, window_image, w)
        return await ocr_image(img)

    async def t_code_assist(self, action="solve", language=None, from_router=False):
        from jarvis.brain.llm import LLM
        from jarvis.hands import actions as A
        from jarvis.hands.windows import foreground_window
        from jarvis.skills import screen
        w = foreground_window()
        if w is None:
            await self.speak("Open the problem first, then ask me again.")
            return "no window"
        await self.speak({"solve": "Let me read the problem.", "check": "Let me look at your code.",
                          "hint": "Let me look.", "explain": "Let me read it."}[action])
        from jarvis.brain.tools import _EDITORS
        if w.exe not in _EDITORS:
            await self.speak("Switch to the browser or editor with the problem, then ask me again.")
            return f"foreground is {w.exe}, not a browser or editor"
        text = await self._page_text(w)
        if not looks_like_problem(text):
            await self.speak("I can't see a coding problem on the screen. Open it and scroll so the question is visible.")
            return "no problem on screen"
        if len(text.strip()) < 40:
            await self.speak("I can't read a problem on the screen. Scroll so the question is visible and try again.")
            return "no problem text"
        lang = language or _guess_language(text)
        messages = [{"role": "system", "content": CODE_SYSTEM.format(language=lang, task=CODE_TASKS[action])},
                    {"role": "user", "content": "Screen text:\n" + text[:6000]}]
        llm = LLM()
        out = ""
        try:
            async for kind, data in llm.stream(messages, max_tokens=900 if action in ("solve", "check") else 160):
                if kind == "text":
                    out += data
        finally:
            await llm.aclose()
        out = out.strip()
        if action in ("hint", "explain"):
            await self.speak(out)
            return out
        if action == "check":
            m = re.search(r"EXPLAIN:\s*(.+?)\s*CODE:\s*(.+)$", out, re.S)
            if not m:
                await self.speak(out[:400])
                return out
            explanation, code = m.group(1).strip(), _strip_fences(m.group(2))

            async def put_fix():
                await self._code_into_editor(w, code)
            self.pending_confirm = put_fix
            await self.speak(explanation + " Want me to put the fixed code in the editor?")
            return "explained; offered fix"
        code = _strip_fences(out)
        ok = await self._code_into_editor(w, code)
        if ok:
            await self.speak(f"Done, that's a {lang} solution in the editor. Say run the code to test it.")
            return f"wrote {len(code.splitlines())} lines of {lang}"
        return "couldn't reach the editor"

    async def _code_into_editor(self, w, code):
        """Click into the page's code editor, replace what's there, paste the code (pasting keeps indentation)."""
        from jarvis.hands import actions as A
        from jarvis.skills import screen
        hit = await self.vision_locate("the code editor panel (the area with line numbers where code is typed, not the "
                                       "problem statement)", w)
        f = w.frame
        if not hit or hit[2] * hit[3] > 0.8 * f.width * f.height or hit[2] < 120 or hit[3] < 80:
            # No box, or "the whole window": there is no editor to type into, so don't click or paste anything.
            await self.speak("I can't find a code editor on this page, so I didn't type anything.")
            return False
        x, y, bw, bh = hit
        await self.ui.fly_to(x, y, purpose="click", button={"x": x - bw // 2, "y": y - bh // 2, "width": bw, "height": bh})
        A.focus(w)
        await asyncio.sleep(0.15)
        A.real_click(x, y)
        await asyncio.sleep(0.2)
        A.press("ctrl", "a")             # inside the editor this selects only the editor's code
        await asyncio.sleep(0.1)
        # Always paste code, even one line: editors auto-close brackets and quotes while typing ("print(" -> "print()").
        await asyncio.get_running_loop().run_in_executor(None, lambda: screen.type_text(code.rstrip("\n"), None, paste=True))
        return True

    async def t_code_run(self, which="run", from_router=False):
        if which == "submit":
            async def submit():
                await self.t_click_element("Submit")
            self.pending_confirm = submit
            await self.speak("Submit your code now?")
            return "asked for confirmation"
        return await self.t_click_element("Run")


def _click(x, y, action):
    from jarvis.hands import actions as A
    if action == "right_click":
        old = A.wintypes.POINT()
        A.user32.GetCursorPos(A.ctypes.byref(old))
        A.user32.SetCursorPos(int(x), int(y))
        A.user32.mouse_event(0x0008, 0, 0, 0, 0)
        A.user32.mouse_event(0x0010, 0, 0, 0, 0)
        time.sleep(0.05)
        A.user32.SetCursorPos(old.x, old.y)
    elif action == "double_click":
        A.real_click(x, y, restore=False)
        time.sleep(0.05)
        A.real_click(x, y)
    else:
        A.real_click(x, y)
