"""Turns one transcribed utterance into speech + actions: fast router first, LLM with tools otherwise.

Follow-up turns (no wake word) may be background chatter, a video, or someone else in the room; for those
the LLM gets an `ignore` tool and the rule to use it, and the turn ends silently.
"""
import base64
import datetime
import io
import logging
import re
import time

from jarvis import config
from jarvis.brain.llm import LLM
from jarvis.brain.router import route
from jarvis.brain.tools import DATA_TOOLS, FALLTHROUGH, IGNORE_TOOL, SCHEMA, Tools
from jarvis.skills import store

log = logging.getLogger("agent")

SYSTEM = """You are {name}, a warm, witty voice assistant (in the spirit of Jarvis) living on the user's Windows PC as a small flying robot.
Replies are spoken aloud: 1-3 short sentences unless asked for detail. No lists, markdown, emojis or URLs.
Use tools for anything on the PC: windows, apps, sites, search, media, volume, brightness, timers, reminders, notes, to-dos,
system status, weather, files, reading or looking at the screen, clipboard and typing. Never claim an action without calling its tool.
Action tools speak their own short confirmation, so after one say nothing more unless something needs explaining.
Call at most 3 tools per reply. "Hide", "put away" or "get rid of for now" means minimize; only close when told close, quit or exit.
For questions about what is on the screen, an error, a page or "this", call look_at_screen.
Answer general knowledge directly. For facts about people, places or events you're unsure of, call wikipedia. Today is {today}.{facts}"""

FOLLOW_UP_RULE = """
IMPORTANT: this message was picked up WITHOUT the wake word, right after you spoke. It may be background talk, a video,
or someone speaking to another person. If it is not clearly a request or question for you, call `ignore` and nothing else."""

ANSWER_SYSTEM = """You are Jarvis, a voice assistant. Using the tool results, answer the user's request in 1-3 short spoken
sentences. No lists, markdown or emojis. Round numbers sensibly."""

VISION_SYSTEM = """You are Jarvis, a voice assistant looking at a screenshot of the user's screen. Answer their question about it
in 1-4 short spoken sentences: be specific (name the app, the error, the key point). No markdown or lists.
OCR text from the same screen is included to help with small text."""

# Sentence boundary: ., !, ? followed by space, or a newline. Keeps "3.5" and "e.g." mostly intact.
_SENT = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'])|\n+")
# For long first sentences, start speaking at a clause break instead of waiting for the full stop.
_CLAUSE = re.compile(r"(?<=[,;:])\s+")
CLAUSE_MIN_CHARS = 60
MAX_TOOL_CALLS = 3


class Memory:
    def __init__(self):
        self.turns = []          # [{"role": ..., "content": ...}]
        self.last_window = None

    def add(self, user, assistant):
        self.turns += [{"role": "user", "content": user}, {"role": "assistant", "content": assistant or "(done)"}]
        self.turns = self.turns[-config.HISTORY_TURNS * 2:]


class Agent:
    def __init__(self, ui, speak, on_control, scheduler=None):
        """speak: async fn(text). on_control: async fn(name) for 'stop' / 'go_to_sleep'."""
        self.llm = LLM()
        self.memory = Memory()
        self.tools = Tools(ui, speak, self.memory, scheduler)
        self.speak = speak
        self.on_control = on_control

    def _system(self):
        today = datetime.date.today().strftime("%A %d %B %Y")
        facts = store.facts.load()
        facts_txt = ("\nThings the user told you to remember: " + "; ".join(facts[-15:]) + ".") if facts else ""
        return SYSTEM.format(name=config.NAME, today=today, facts=facts_txt)

    async def warm_up(self):
        """Prime llama.cpp's prompt cache with the system prompt + tool schema, so the first real
        question doesn't pay ~1-2 s to process them."""
        messages = [{"role": "system", "content": self._system()}, {"role": "user", "content": "hi"}]
        try:
            async for _ in self.llm.stream(messages, tools=SCHEMA, max_tokens=1):
                pass
        except Exception as e:  # noqa: BLE001 - warm-up is best effort
            log.warning("LLM warm-up failed: %s", e)

    async def handle(self, text: str, timings: dict, follow_up=False):
        """Returns "ignored" when a follow-up turn turned out not to be meant for Jarvis."""
        text = text.strip()
        if not text:
            return None
        hit = route(text)
        if hit:
            name, args = hit
            timings["route"] = "router"
            if name in ("stop", "go_to_sleep", "standby"):
                await self.on_control(name)
                return None
            log.info("router -> %s %s", name, args)
            result = await self.tools.call(name, args, from_router=True)
            if result is not FALLTHROUGH:
                timings["first_action"] = time.perf_counter()
                if name in DATA_TOOLS or isinstance(result, dict):
                    await self._answer_from(text, name, args, result, timings)
                else:
                    self.memory.add(text, f"[{name} {args}] {result}")
                return None
        if follow_up and len(text.split()) > 30:
            log.info("ignoring long follow-up utterance (likely background talk)")
            return "ignored"
        timings["route"] = "llm"
        return await self._llm_turn(text, timings, follow_up)

    # ------------------------------------------------------------------ speech
    async def _stream_speech(self, messages, timings, tools=None, max_tokens=400):
        """Stream an LLM reply into TTS sentence by sentence. Returns (spoken sentences, tool calls)."""
        spoken, calls, buf = [], None, ""
        async for kind, data in self.llm.stream(messages, tools=tools, max_tokens=max_tokens):
            if kind == "text":
                timings.setdefault("first_token", time.perf_counter())
                buf += data
                parts = _SENT.split(buf)
                for sentence in parts[:-1]:
                    if sentence.strip():
                        spoken.append(sentence.strip())
                        await self.speak(sentence.strip())
                buf = parts[-1]
                if not spoken and len(buf) >= CLAUSE_MIN_CHARS:
                    clauses = _CLAUSE.split(buf)
                    if len(clauses) > 1:
                        head = " ".join(clauses[:-1]).strip()
                        spoken.append(head)
                        await self.speak(head)
                        buf = clauses[-1]
            else:
                calls = data
        if buf.strip() and not _is_filler(buf):
            spoken.append(buf.strip())
            await self.speak(buf.strip())
        return spoken, calls

    # -------------------------------------------------------------------- turns
    async def _llm_turn(self, text, timings, follow_up):
        system = self._system() + (FOLLOW_UP_RULE if follow_up else "")
        tools = SCHEMA + ([IGNORE_TOOL] if follow_up else [])
        messages = [{"role": "system", "content": system}, *self.memory.turns, {"role": "user", "content": text}]
        spoken, calls = await self._stream_speech(messages, timings, tools=tools)
        if not calls:
            self.memory.add(text, " ".join(spoken))
            return None
        calls = calls[:MAX_TOOL_CALLS]
        if any(c["name"] == "ignore" for c in calls):
            log.info("LLM judged the follow-up as not addressed to Jarvis")
            return "ignored"
        timings.setdefault("first_token", time.perf_counter())
        results = []
        for c in calls:
            log.info("llm tool -> %s %s", c["name"], c["arguments"])
            result = await self.tools.call(c["name"], c["arguments"])
            timings.setdefault("first_action", time.perf_counter())
            results.append((c, result))
        data = [(c, r) for c, r in results if c["name"] in DATA_TOOLS or isinstance(r, dict)]
        if data:
            c, r = data[0]
            await self._answer_from(text, c["name"], c["arguments"], r, timings)
            return None
        failed = [(c, r) for c, r in results if str(r).startswith(("error", "No open", "Bad", "Unknown"))]
        self.memory.add(text, " ".join(spoken) + " " + "; ".join(f"[{c['name']}] {r}" for c, r in results))
        if failed and not spoken:
            await self.speak("Sorry, I couldn't do that.")
        return None

    async def _answer_from(self, text, name, args, result, timings):
        """Second pass: the tool returned information; have the LLM say it naturally."""
        if isinstance(result, dict) and "image" in result:
            return await self._vision_answer(text, result, timings)
        messages = [{"role": "system", "content": ANSWER_SYSTEM},
                    {"role": "user", "content": f"User asked: {text}\nTool {name} returned: {result}"}]
        spoken, _ = await self._stream_speech(messages, timings, max_tokens=200)
        self.memory.add(text, " ".join(spoken))

    async def _vision_answer(self, text, shot, timings):
        buf = io.BytesIO()
        shot["image"].save(buf, "JPEG", quality=80)
        b64 = base64.b64encode(buf.getvalue()).decode()
        question = shot.get("question") or text
        ocr = (shot.get("ocr") or "")[:2500]
        messages = [{"role": "system", "content": VISION_SYSTEM}, {"role": "user", "content": [
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
            {"type": "text", "text": f"Question: {question}\n\nOCR text from the screen:\n{ocr}"},
        ]}]
        try:
            spoken, _ = await self._stream_speech(messages, timings, max_tokens=250)
        except Exception as e:  # noqa: BLE001 - no vision support: fall back to the OCR text
            log.warning("vision call failed (%s); answering from OCR only", e)
            messages = [{"role": "system", "content": ANSWER_SYSTEM},
                        {"role": "user", "content": f"User asked: {question}\nText read from their screen:\n{ocr}"}]
            spoken, _ = await self._stream_speech(messages, timings, max_tokens=250)
        self.memory.add(text, " ".join(spoken))


def _is_filler(s):
    return s.strip().lower().strip(".!") in ("", "done", "ok", "okay")
