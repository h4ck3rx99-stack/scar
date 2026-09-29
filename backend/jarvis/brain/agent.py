"""Turns one transcribed utterance into speech + actions: fast router first, LLM with tools otherwise."""
import datetime
import logging
import re
import time

from jarvis import config
from jarvis.brain.llm import LLM
from jarvis.brain.router import route
from jarvis.brain.tools import FALLTHROUGH, SCHEMA, Tools

log = logging.getLogger("agent")

SYSTEM = """You are Jarvis, a friendly, witty voice assistant living on the user's Windows desktop as a small flying robot.
Your replies are spoken aloud: answer in 1-3 short sentences unless asked for detail. No lists, markdown, emojis or URLs.
Use tools to control windows, open apps and sites, search, and control media; never claim an action without calling the tool.
Tools speak their own confirmation, so after a tool call say nothing more unless the result needs explaining.
"Hide", "get rid of for now" or "put away" means minimize. Only close when the user clearly says close, quit or exit.
Answer general-knowledge questions directly. Only live information (news, weather, prices, scores) is unavailable because you run offline.
Today is {today}."""

# Sentence boundary: ., !, ? followed by space, or a newline. Keeps "3.5" and "e.g." mostly intact.
_SENT = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'])|\n+")


class Memory:
    def __init__(self):
        self.turns = []          # [{"role": ..., "content": ...}]
        self.last_window = None

    def add(self, user, assistant):
        self.turns += [{"role": "user", "content": user}, {"role": "assistant", "content": assistant or "(done)"}]
        self.turns = self.turns[-config.HISTORY_TURNS * 2:]


class Agent:
    def __init__(self, ui, speak, on_control):
        """speak: async fn(text). on_control: async fn(name) for 'stop' / 'go_to_sleep'."""
        self.llm = LLM()
        self.memory = Memory()
        self.tools = Tools(ui, speak, self.memory)
        self.speak = speak
        self.on_control = on_control

    async def warm_up(self):
        """Prime llama.cpp's prompt cache with the system prompt + tool schema, so the first real
        question doesn't pay ~1-2 s to process them."""
        today = datetime.date.today().strftime("%A %d %B %Y")
        messages = [{"role": "system", "content": SYSTEM.format(today=today)}, {"role": "user", "content": "hi"}]
        try:
            async for _ in self.llm.stream(messages, tools=SCHEMA, max_tokens=1):
                pass
        except Exception as e:  # noqa: BLE001 - warm-up is best effort
            log.warning("LLM warm-up failed: %s", e)

    async def handle(self, text: str, timings: dict):
        text = text.strip()
        if not text:
            return
        hit = route(text)
        if hit:
            name, args = hit
            timings["route"] = "router"
            if name in ("stop", "go_to_sleep"):
                await self.on_control(name)
                return
            log.info("router -> %s %s", name, args)
            result = await self.tools.call(name, args, from_router=True)
            if result is not FALLTHROUGH:
                timings["first_action"] = time.perf_counter()
                self.memory.add(text, f"[{name} {args}] {result}")
                return
        timings["route"] = "llm"
        await self._llm_turn(text, timings)

    async def _llm_turn(self, text, timings):
        # Date only: a system prompt that changes every minute would defeat llama.cpp's prompt cache.
        today = datetime.date.today().strftime("%A %d %B %Y")
        messages = [{"role": "system", "content": SYSTEM.format(today=today)}, *self.memory.turns, {"role": "user", "content": text}]
        spoken = []
        for _round in range(3):  # tool -> result -> answer, at most a couple of hops
            buf, calls = "", None
            async for kind, data in self.llm.stream(messages, tools=SCHEMA):
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
            if not calls:
                break
            timings.setdefault("first_token", time.perf_counter())
            messages.append({"role": "assistant", "content": " ".join(spoken) or None, "tool_calls": [
                {"id": c["id"], "type": "function", "function": {"name": c["name"], "arguments": c["raw"]}} for c in calls]})
            needs_answer = False
            for c in calls:
                log.info("llm tool -> %s %s", c["name"], c["arguments"])
                result = await self.tools.call(c["name"], c["arguments"])
                timings.setdefault("first_action", time.perf_counter())
                messages.append({"role": "tool", "tool_call_id": c["id"], "content": str(result)})
                needs_answer |= c["name"] == "list_windows" or str(result).startswith(("error", "No open", "Bad", "Unknown"))
            if not needs_answer:
                break
        self.memory.add(text, " ".join(spoken))


def _is_filler(s):
    return s.strip().lower().strip(".!") in ("", "done", "ok", "okay")
