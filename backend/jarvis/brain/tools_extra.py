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
        {"action": {"type": "string", "enum": ["solve", "hint", "explain", "check"]}, "language": S,
         "submit": {"type": "boolean", "description": "after solving, offer to submit (the user asked to submit)"}}, ["action"]),
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
    "solve_html": "Write a complete, properly indented HTML document (<!DOCTYPE html>, <html lang=\"en\">, <head> with "
                  "<meta charset=\"UTF-8\">, the viewport meta and a <title>, then <body>) for what the problem describes: "
                  "match the required text, element types, ids, "
                  "class names and structure exactly as stated. Put required CSS in a <style> tag inside <head> and any "
                  "JavaScript in a <script> tag at the end of <body>. Reply with ONLY the code, no markdown fences, no "
                  "explanation.",
    "solve":"Write a complete, correct solution that reads input and prints output EXACTLY in the format the problem "
             "shows (no extra prompts or text). The screen text comes from OCR, so where the description and the Sample "
             "Output disagree on spacing or punctuation, copy the Sample Output character for character. Reply with "
             "ONLY the code, no markdown fences, no explanation.",
    "solve_web": "The editor has separate HTML, CSS and JS tabs. Write the solution as three sections, each starting with a "
                 "line that is exactly === HTML ===, === CSS === or === JS === (leave a section empty if it isn't needed). "
                 "The HTML section must be a complete, properly indented document: <!DOCTYPE html>, then <html lang=\"en\"> "
                 "with a <head> (<meta charset=\"UTF-8\">, <meta name=\"viewport\" content=\"width=device-width, "
                 "initial-scale=1.0\">, a <title>) and a <body> holding the page markup. No <style> or <script> tags in "
                 "it: the CSS and JS tabs are linked by the site. Match the required text, element types, ids, class names "
                 "and structure exactly. No markdown fences, no explanation.",
    "fix": "The student's code (below) fails when run; the run output is included. Return the full corrected code "
           "ONLY, no markdown fences, no explanation.",
    "fix_web": "The code (below, in === HTML === / === CSS === / === JS === sections) fails; the output is included. "
               "Return all three corrected sections in the same format, no markdown fences, no explanation.",
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


_LANG_SIGNS = {
    # uppercase editor-tab labels (case-sensitive, strongest), then words and code in the page
    "HTML": ([r"\bHTML\b", r"\bCSS\b"], [r"<(?:div|html|body|h1|p|img|button|a|ul|li|span|section|head)\b", r"\bhtml\b", r"\bcss\b",
                                        r"\bbootstrap\b", r"\belement\b", r"\bstyle\b", r"\bweb ?page\b"]),
    "Python 3": ([r"\bPYTHON\b"], [r"\bpython\b", r"\bdef \w+\(", r"\bprint\(", r"\binput\(\)"]),
    "JavaScript": ([r"\bJAVASCRIPT\b", r"\bJS\b", r"\bNODE\b"], [r"\bjavascript\b", r"console\.log", r"\bfunction \w+\(", r"\bconst \w+ ="]),
    "SQL": ([r"\bSQL\b", r"\bSQLITE\b"], [r"\bselect\b.+\bfrom\b", r"\bsql\b", r"\btable\b", r"\bquery\b"]),
    "Java": ([r"\bJAVA\b"], [r"public static void main", r"\bjava\b"]),
    "C++": ([r"\bC\+\+\b", r"\bCPP\b"], [r"#include", r"\bcout\b", r"\bc\+\+"]),
}


def _guess_language(text: str) -> str:
    scores = {}
    for lang, (labels, words) in _LANG_SIGNS.items():
        scores[lang] = 3 * sum(len(re.findall(p, text)) for p in labels) + sum(1 for p in words if re.search(p, text, re.I))
    best = max(scores, key=scores.get)
    return best if scores[best] > 0 else "Python 3"


class CodeTools:
    """Mixed into Tools: coding-practice help on learning.ccbp.in and similar sites.

    Solve flow: find the editor -> read the whole problem (scrolling the problem pane if it's long) -> write the
    solution (one file, or HTML / CSS / JS for the page's tabs) -> type it in (paste is blocked on CCBP) ->
    Run and read the output -> if it errors, fix once and re-run -> Submit and report the result."""

    async def _shot(self, w):
        from jarvis.hands.capture import window_image
        return await asyncio.get_running_loop().run_in_executor(None, window_image, w)

    async def _locate_editor(self, w):
        hit = await self.vision_locate("the code editor panel (the area with line numbers where code is typed, not the "
                                       "problem statement)", w)
        f = w.frame
        if not hit or hit[2] * hit[3] > 0.8 * f.width * f.height or hit[2] < 120 or hit[3] < 80:
            return None                     # no box, or "the whole window": there is no editor
        return hit

    async def _read_problem(self, w, editor):
        """All of the problem text. A long problem pane scrolls: scroll it down step by step, read each view, stitch
        the lines together, then scroll back to the top."""
        from rapidfuzz import fuzz
        from jarvis.skills.screen import ocr_image, scroll_at
        self._samples = (None, None)                                # never reuse the previous problem's samples
        img = await self._shot(w)
        f = w.frame
        full = await ocr_image(img)
        if editor is None:
            return full
        ex, ey, ew, eh = editor
        left, right = ex - ew // 2 - f.left, ex + ew // 2 - f.left
        if left > img.width * 0.2:
            region = (0, 0, max(1, left - 6), img.height)          # problem on the left (CCBP layout)
        elif right < img.width * 0.8:
            region = (right + 6, 0, img.width, img.height)         # problem on the right
        else:
            return full
        px, py = f.left + (region[0] + region[2]) // 2, f.top + img.height // 2
        crop = img.crop(region)
        views, view_imgs = [await ocr_image(crop)], [crop]
        scrolled = 0
        for _ in range(15):
            scroll_at(px, py, -5)
            scrolled += 5
            await asyncio.sleep(0.45)
            crop = (await self._shot(w)).crop(region)
            view = await ocr_image(crop)
            if fuzz.ratio(view, views[-1]) > 96:
                break                                               # reached the bottom
            views.append(view)
            view_imgs.append(crop)
        # OCR garbles short monospace sample lines ("Ravi: A" came out as "Ravi:" with the grade lost), so the exact
        # samples are transcribed by the vision model from the view that shows them.
        self._samples = (None, None)
        sample_views = [im for v, im in zip(views, view_imgs) if re.search(r"sample\s+output|expected\s+output", v, re.I)]
        if sample_views:
            self._samples = await self._transcribe_samples(sample_views[-1])
        if scrolled:
            scroll_at(px, py, scrolled + 10)                        # back to the top
        seen, lines = set(), []
        for view in views:
            for ln in view.splitlines():
                key = re.sub(r"\s+", " ", ln.strip().lower())
                if key and key not in seen:
                    seen.add(key)
                    lines.append(ln)
        if len(views) > 1:
            log.info("read a long problem in %d views (%d lines)", len(views), len(lines))
        editor_text = await ocr_image(img.crop((max(0, left), max(0, ey - eh // 2 - f.top - 90), min(img.width, right),
                                                min(img.height, ey + eh // 2 - f.top))))
        exact = ""
        if self._samples[1]:
            exact = ("\n\nEXACT SAMPLES (transcribed from the image; trust these over the OCR text above, which can garble "
                     f"them):\nSample Input:\n{self._samples[0] or ''}\nSample Output:\n{self._samples[1]}")
        return "\n".join(lines) + exact + "\n\nCODE EDITOR (tabs, language, starter code):\n" + editor_text

    async def _transcribe_output(self, w):
        """The program output shown after Run, read by the vision model (plain OCR garbles short output lines)."""
        from jarvis.brain.llm import LLM
        img = await self._shot(w)
        if img.width > 1600:
            img = img.resize((1600, round(img.height * 1600 / img.width)))
        buf = io.BytesIO()
        img.convert("RGB").save(buf, "PNG")
        b64 = base64.b64encode(buf.getvalue()).decode()
        prompt = ("This is a coding-practice page after pressing Run. Transcribe exactly, line by line, the text in the "
                  "program output / console / result panel (not the code editor, not the problem statement). Keep every "
                  "space and punctuation mark. If there is no output yet, reply exactly NONE.")
        llm = LLM()
        out = ""
        try:
            async for kind, data in llm.stream([{"role": "user", "content": [
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
                    {"type": "text", "text": prompt}]}], max_tokens=400):
                if kind == "text":
                    out += data
        except Exception as e:  # noqa: BLE001
            log.warning("output transcription failed: %s", e)
            return None
        finally:
            await llm.aclose()
        out = out.strip().strip("`").strip()
        return None if not out or out.upper() == "NONE" else out

    async def _transcribe_samples(self, img):
        """(sample input, sample output) read by the vision model from a screenshot of the problem pane."""
        from jarvis.brain.llm import LLM
        buf = io.BytesIO()
        img.convert("RGB").save(buf, "PNG")
        b64 = base64.b64encode(buf.getvalue()).decode()
        prompt = ("Transcribe the FIRST Sample Input and Sample Output blocks in this screenshot exactly, character for "
                  "character, keeping every line, space and punctuation mark. Reply in exactly this format and nothing "
                  "else:\nINPUT:\n<sample input lines>\nOUTPUT:\n<sample output lines>")
        llm = LLM()
        out = ""
        try:
            async for kind, data in llm.stream([{"role": "user", "content": [
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
                    {"type": "text", "text": prompt}]}], max_tokens=400):
                if kind == "text":
                    out += data
        except Exception as e:  # noqa: BLE001 - fall back to the OCR text
            log.warning("sample transcription failed: %s", e)
            return None, None
        finally:
            await llm.aclose()
        m = re.search(r"INPUT:\s*\n(.*?)\n\s*OUTPUT:\s*\n(.*)$", out.strip().strip("`"), re.S)
        if not m:
            return None, None
        log.info("samples transcribed: %r / %r", m.group(1)[:80], m.group(2)[:80])
        return m.group(1).strip("\n"), m.group(2).strip("\n").rstrip("`").rstrip()

    async def _editor_tabs(self, w, editor):
        """HTML / CSS / JS tabs above the editor -> {"HTML": (x, y), ...} (only when there are two or more)."""
        from jarvis.skills.screen import ocr_words
        img = await self._shot(w)
        f = w.frame
        ex, ey, ew, eh = editor
        top = ey - eh // 2 - f.top
        box = (max(0, ex - ew // 2 - f.left - 10), max(0, top - 110), min(img.width, ex + ew // 2 - f.left + 10),
               min(img.height, top + 60))
        tabs = {}
        for word, (l, t, r, b) in await ocr_words(img.crop(box)):
            name = {"HTML": "HTML", "CSS": "CSS", "JS": "JS", "JAVASCRIPT": "JS"}.get(re.sub(r"[^A-Za-z]", "", word).upper())
            if name and name not in tabs:
                tabs[name] = (f.left + box[0] + (l + r) // 2, f.top + box[1] + (t + b) // 2)
        return tabs if len(tabs) >= 2 else {}

    async def _find_button(self, w, label):
        """Screen point of a page button by its text ("Run", "Submit"), preferring the one near the other buttons."""
        from jarvis.skills.screen import ocr_words
        img = await self._shot(w)
        f = w.frame
        words = await ocr_words(img)
        def norm(wd):
            return re.sub(r"\W", "", wd).lower()
        hits = [(l, t, r, b) for word, (l, t, r, b) in words if norm(word) == label.lower()]
        if not hits:
            return None
        buttons = ("run", "submit", "reset", "html", "css", "js", "python")

        def in_sentence(h):
            # A button label stands alone; the same word inside a sentence ("Submit to check your page") has other
            # words right next to it on its line.
            l, t, r, b = h
            return any(abs(wt - t) < 12 and (0 <= wl - r < 25 or 0 <= l - wr < 25) and norm(word) not in buttons
                       for word, (wl, wt, wr, wb) in words if (wl, wt, wr, wb) != h)

        def crowd(h):   # buttons sit together in a toolbar
            return sum(1 for word, (wl, wt, wr, wb) in words
                       if norm(word) in buttons and abs(wt - h[1]) < 40 and 0 < abs(wl - h[0]) < 400)
        l, t, r, b = sorted(hits, key=lambda h: (in_sentence(h), -crowd(h), h[1]))[0]
        if in_sentence((l, t, r, b)):
            return None                 # only ever seen inside text: there's no such button
        return f.left + (l + r) // 2, f.top + (t + b) // 2

    async def _press(self, w, point, what):
        from jarvis.hands import actions as A
        x, y = point
        await self.ui.fly_to(x, y, purpose="click", button={"x": x - 30, "y": y - 16, "width": 60, "height": 32})
        if not A.focus(w):
            return False
        await asyncio.sleep(0.1)
        A.real_click(x, y)
        log.info("pressed %s at %s", what, point)
        return True

    async def _type_into(self, w, editor, code, tab=None):
        """Click the tab (if any) and into the editor, then type the code (typed, not pasted: CCBP blocks paste)."""
        from rapidfuzz import fuzz
        from jarvis.hands import actions as A
        from jarvis.skills import screen
        x, y, bw, bh = editor
        if tab:
            if not await self._press(w, tab, "tab"):
                return False
            await asyncio.sleep(0.7)
        await self.ui.fly_to(x, y, purpose="click", button={"x": x - bw // 2, "y": y - bh // 2, "width": bw, "height": bh})
        if not A.focus(w):
            await self.speak("I couldn't get into the editor, so I didn't type anything.")
            return False
        await asyncio.sleep(0.15)
        A.real_click(x, y)
        await asyncio.sleep(0.25)
        await asyncio.get_running_loop().run_in_executor(None, screen.type_code, code)
        await asyncio.sleep(0.4)
        f = w.frame
        img = await self._shot(w)
        box = img.crop((max(0, x - f.left - bw // 2), max(0, y - f.top - bh // 2),
                        min(img.width, x - f.left + bw // 2), min(img.height, y - f.top + bh // 2)))
        seen = (await screen.ocr_image(box)).lower()
        probes = [ln.strip().lower() for ln in code.splitlines() if len(ln.strip()) >= 6][:4]
        if probes and max(fuzz.partial_ratio(p, seen) for p in probes) < 70:
            log.warning("code typed but not visible in the editor; saw %r", seen[:200])
            return False
        return True

    async def _ask_llm(self, system, user, max_tokens):
        from jarvis.brain.llm import LLM
        llm = LLM()
        out = ""
        try:
            async for kind, data in llm.stream([{"role": "system", "content": system}, {"role": "user", "content": user}],
                                               max_tokens=max_tokens):
                if kind == "text":
                    out += data
        finally:
            await llm.aclose()
        return out.strip()

    async def t_code_assist(self, action="solve", language=None, submit=False, from_router=False):
        from jarvis.brain.tools import _EDITORS
        from jarvis.core import settings
        from jarvis.hands.windows import foreground_window
        w = foreground_window()
        if w is None:
            await self.speak("Open the problem first, then ask me again.")
            return "no window"
        if w.exe not in _EDITORS:
            await self.speak("Switch to the browser or editor with the problem, then ask me again.")
            return f"foreground is {w.exe}, not a browser or editor"
        await self.speak({"solve": "Let me read the problem.", "check": "Let me look at your code.",
                          "hint": "Let me look.", "explain": "Let me read it."}[action])
        editor = await self._locate_editor(w)
        text = await self._read_problem(w, editor)
        if not looks_like_problem(text):
            await self.speak("I can't see a coding problem on the screen. Open it and try again.")
            return "no problem on screen"
        lang = {"html": "HTML", "css": "HTML", "js": "JavaScript", "javascript": "JavaScript", "python": "Python 3",
                "sql": "SQL", "java": "Java", "c++": "C++", "cpp": "C++"}.get((language or "").lower(), language) or _guess_language(text)
        tabs = await self._editor_tabs(w, editor) if editor and action in ("solve", "check") else {}
        if tabs:
            lang = "HTML"
        if action in ("hint", "explain"):
            out = await self._ask_llm(CODE_SYSTEM.format(language=lang, task=CODE_TASKS[action]), "Screen text:\n" + text[:9000], 180)
            await self.speak(out)
            return out
        if editor is None:
            await self.speak("I can't find a code editor on this page, so I didn't type anything.")
            return "no editor"
        if action == "check":
            out = await self._ask_llm(CODE_SYSTEM.format(language=lang, task=CODE_TASKS["check"]), "Screen text:\n" + text[:9000], 900)
            m = re.search(r"EXPLAIN:\s*(.+?)\s*CODE:\s*(.+)$", out, re.S)
            if not m:
                await self.speak(out[:400])
                return out
            explanation, code = m.group(1).strip(), _strip_fences(m.group(2))

            async def put_fix():
                await self._type_into(w, editor, code)
            self.pending_confirm = put_fix
            await self.speak(explanation + " Want me to put the fixed code in the editor?")
            return "explained; offered fix"

        # ---- solve
        task = CODE_TASKS["solve_web" if tabs else "solve_html" if lang == "HTML" else "solve"]
        out = await self._ask_llm(CODE_SYSTEM.format(language=lang, task=task), "Screen text:\n" + text[:9000], 1400)
        parts = split_web_parts(out) if tabs else {"": _strip_fences(out)}
        n = sum(len(c.strip().splitlines()) for c in parts.values())
        await self.speak(f"Typing {n} lines{' across ' + ', '.join(p for p in parts if p) if tabs else ''}. "
                         "Hands off the keyboard for a moment.")
        await asyncio.sleep(0.6)
        for name, code in parts.items():
            if not code.strip():
                continue
            if not await self._type_into(w, editor, code, tabs.get(name) if name else None):
                await self.speak("I typed it but I can't see it in the editor. The site may be blocking typed input.")
                return "typing didn't show up"
        auto = submit or settings.get("code_auto_submit")
        if not auto:
            await self.speak(f"Done, that's a {lang} solution in the editor. Say run the code to test it.")
            return f"wrote {n} lines of {lang}"
        return await self._run_fix_submit(w, editor, parts, lang, text, tabs)

    async def _page_after(self, w, before, wait=4.0):
        """New text that appeared on the page after pressing a button (the output / verdict area)."""
        from jarvis.skills.screen import ocr_image
        await asyncio.sleep(wait)
        after = await ocr_image(await self._shot(w))
        old = {re.sub(r"\s+", " ", ln.strip().lower()) for ln in before.splitlines()}
        return "\n".join(ln for ln in after.splitlines() if re.sub(r"\s+", " ", ln.strip().lower()) not in old)

    async def _fix_and_retype(self, w, editor, parts, lang, problem, tabs, report):
        code_now = "\n\n".join(f"=== {k} ===\n{v}" if k else v for k, v in parts.items())
        fixed = await self._ask_llm(CODE_SYSTEM.format(language=lang, task=CODE_TASKS["fix_web" if tabs else "fix"]),
                                    f"Problem:\n{problem[:7000]}\n\nCurrent code:\n{code_now}\n\n{report[:1500]}", 1400)
        new_parts = split_web_parts(fixed) if tabs else {"": _strip_fences(fixed)}
        for name, code in new_parts.items():
            if code.strip() and not await self._type_into(w, editor, code, tabs.get(name) if name else None):
                return None
        return new_parts

    async def _run_fix_submit(self, w, editor, parts, lang, problem, tabs):
        """Run -> compare with the sample output -> fix once if wrong -> Submit -> if hidden tests fail, fix once more
        and resubmit. Never submits code that visibly fails the sample."""
        from jarvis.skills.screen import ocr_image
        sample_in, sample_out = getattr(self, "_samples", (None, None))
        if not sample_out:
            sample_in, sample_out = sample_io(problem)
        for attempt in range(2):
            run = await self._find_button(w, "Run")
            if run is None:
                break                                   # web problems often only have Submit
            before = await ocr_image(await self._shot(w))
            await self._press(w, run, "Run")
            await asyncio.sleep(4.5)
            output = await self._transcribe_output(w)
            if output is None:                          # vision couldn't read it: fall back to the new OCR text
                output = await self._page_after(w, before, wait=0)
            log.info("run output: %r", output[:300])
            match = output_matches(sample_out, output) if sample_out else "exact"
            if not RUN_FAILED.search(output) and match == "exact":
                break
            if attempt == 1:
                if match == "loose" and not RUN_FAILED.search(output):
                    break                               # only spacing differs, twice: likely OCR, go ahead
                await self.speak("It still doesn't match the sample output, so I didn't submit. Say check my code for details.")
                return "run failed twice; not submitted"
            await self.speak("The output doesn't match the sample yet. Fixing it." if not RUN_FAILED.search(output)
                             else "The run shows an error. Fixing it.")
            diff = output_diff(sample_out, output) if sample_out else ""
            report = (f"Run output:\n{output}\n\nExpected (sample output):\n{sample_out or '(see problem)'}"
                      + (f"\n\nDifferences to fix:\n{diff}" if diff else ""))
            parts = await self._fix_and_retype(w, editor, parts, lang, problem, tabs, report)
            if parts is None:
                await self.speak("I couldn't type the fix in, so I didn't submit.")
                return "fix not typed"
        for round_ in range(2):
            sub = await self._find_button(w, "Submit")
            if sub is None:
                await self.speak(f"The {lang} solution is in, but I can't find a Submit button.")
                return "no submit button"
            before = await ocr_image(await self._shot(w))
            await self._press(w, sub, "Submit")
            verdict = await self._page_after(w, before, wait=6.0)
            log.info("submit result: %r", verdict[:300])
            if not submit_failed(verdict):
                if SUBMIT_PASSED.search(verdict):
                    await self.speak("Submitted, and all the test cases passed!")
                    return "submitted; passed"
                await self.speak("Submitted. Have a look at the result on the page.")
                return "submitted"
            if round_ == 1:
                await self.speak("I resubmitted, but some test cases still fail. Say check my code and I'll explain why.")
                return "submitted twice; some tests failed"
            await self.speak("Some hidden test cases failed. Let me fix it and resubmit.")
            report = (f"Submit result: {verdict[:500]}\nSome hidden test cases fail. Re-read the problem for edge cases "
                      "(ties, empty input, exact output format) and fix the code.")
            parts = await self._fix_and_retype(w, editor, parts, lang, problem, tabs, report)
            if parts is None:
                await self.speak("I couldn't type the fix in.")
                return "fix not typed"

    async def t_code_run(self, which="run", from_router=False):
        from jarvis.hands.windows import foreground_window
        w = foreground_window()
        if which == "submit":
            async def submit():
                point = await self._find_button(w, "Submit") if w else None
                if point:
                    await self._press(w, point, "Submit")
                else:
                    await self.t_click_element("Submit")
            self.pending_confirm = submit
            await self.speak("Submit your code now?")
            return "asked for confirmation"
        point = await self._find_button(w, "Run") if w else None
        if point:
            await self._press(w, point, "Run")
            return "pressed Run"
        return await self.t_click_element("Run")


RUN_FAILED = re.compile(r"traceback|\berror\b|exception|syntaxerror|nameerror|typeerror|indentationerror|valueerror|"
                        r"\bfailed\b|wrong answer|incorrect|not matching|doesn't match|does not match", re.I)


def sample_io(problem: str):
    """(sample input, sample output) from the problem text, or (None, None)."""
    m = re.search(r"sample\s+input\s*1?\s*:?\s*\n(.*?)\n\s*sample\s+output\s*1?\s*:?\s*\n(.*?)"
                  r"(?:\n\s*(?:sample\s+input|constraints?|explanation|note|test\s*cases?|code editor)\b|\Z)", problem, re.I | re.S)
    if not m:
        return None, None
    return m.group(1).strip(), m.group(2).strip()


def output_matches(expected: str, got: str) -> str:
    """'exact' if the expected sample output appears in what the run printed, 'loose' if only the spacing differs
    (could be OCR), '' if it's different."""
    exp_lines = [ln.rstrip() for ln in expected.strip().splitlines() if ln.strip()]
    got_text = "\n".join(ln.rstrip() for ln in got.splitlines())
    if "\n".join(exp_lines) in got_text:
        return "exact"
    squash = lambda s: re.sub(r"\s+", "", s)  # noqa: E731
    return "loose" if squash("".join(exp_lines)) in squash(got_text) else ""


def output_diff(expected: str, got: str) -> str:
    """Spell out the first differing lines ("line 4: expected 'Top: Ravi' but printed 'Top : Ravi'"): a small model
    can't spot a one-space difference by comparing two blocks itself."""
    exp = [ln.rstrip() for ln in expected.strip().splitlines() if ln.strip()]
    # the run area can hold other text too: align on the first expected line
    lines = [ln.rstrip() for ln in got.splitlines() if ln.strip()]
    start = next((i for i, ln in enumerate(lines) if re.sub(r"\s+", "", ln) == re.sub(r"\s+", "", exp[0])), 0) if exp else 0
    got_lines = lines[start:start + len(exp) + 2]
    notes = []
    for i, e in enumerate(exp):
        g = got_lines[i] if i < len(got_lines) else "(nothing)"
        if g != e:
            hint = " (only the spacing differs: match it character for character)" if re.sub(r"\s+", "", g) == re.sub(r"\s+", "", e) else ""
            notes.append(f"line {i + 1}: expected {e!r} but the code printed {g!r}{hint}")
    if len(got_lines) > len(exp):
        notes.append(f"extra output after the expected lines: {got_lines[len(exp):]!r}")
    return "\n".join(notes[:6])


def submit_failed(text: str) -> bool:
    """A verdict with failures: "failed", "wrong answer", or a count like "3/5 test cases passed"."""
    if re.search(r"\bfailed\b|wrong answer|incorrect|not passed|\berror\b", text, re.I):
        return True
    return any(int(a) < int(b) for a, b in re.findall(r"\b(\d+)\s*/\s*(\d+)\b", text))


SUBMIT_PASSED = re.compile(r"all test ?cases passed|\bpassed\b|success|accepted|correct|congratulations|completed", re.I)


def split_web_parts(out: str) -> dict:
    """'=== HTML ===\\n...\\n=== CSS ===\\n...' -> {"HTML": ..., "CSS": ..., "JS": ...}."""
    parts, cur = {}, None
    for ln in out.replace("\r\n", "\n").split("\n"):
        m = re.match(r"^\s*(?:=+|#+|\*\*|//|/\*|<!--)?\s*(HTML|CSS|JS|JAVASCRIPT)\s*(?:=+|\*\*|\*/|-->|:)?\s*$", ln, re.I)
        if m:
            cur = "JS" if m.group(1).upper().startswith("J") else m.group(1).upper()
            parts[cur] = []
            continue
        if cur and not ln.strip().startswith("```"):
            parts[cur].append(ln)
    return {k: "\n".join(v).strip("\n") + "\n" for k, v in parts.items()}


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
