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

from jarvis import config, runtime
from jarvis.brain.llm import LLM
from jarvis.brain.normalize import fix
from jarvis.brain.router import route_all
from jarvis.brain.tools import DATA_TOOLS, FALLTHROUGH, IGNORE_TOOL, SCHEMA, Tools
from jarvis.skills import store

log = logging.getLogger("agent")

SYSTEM = """You are {name}, a warm, witty voice assistant (in the spirit of Jarvis) living on the user's Windows PC as a small flying robot.
Replies are spoken aloud: 1-3 short sentences unless asked for detail. No lists, markdown, emojis or URLs.
Never end with filler like "anything else?" or "let me know if you need more"; just stop when the answer is complete.
Use tools for anything on the PC: windows, apps, sites, search, media, volume, brightness, timers, reminders, notes, to-dos,
system status, weather, files, reading or looking at the screen, clipboard and typing. Never claim an action without calling its tool.
Action tools speak their own short confirmation, so after one say nothing more unless something needs explaining.
Call at most 3 tools per reply. "Hide", "put away" or "get rid of for now" means minimize; only close when told close, quit or exit.
For questions about what is on the screen, an error, a page or "this", call look_at_screen.
For multi-step requests, do a step or two, check the results (read_screen / look_at_screen show what's there, e.g. exact
names to click), then continue until the whole request is done, and finish with one short spoken summary.
For current facts, prices, news or comparisons, call web_research. To write emails, messages or text into an app, call write_text.
Answer general knowledge directly. For facts about people, places or events you're unsure of, call wikipedia. Today is {today}.{facts}"""

FOLLOW_UP_RULE = """
IMPORTANT: this message was picked up WITHOUT the wake word, right after you spoke. It may be background talk, a video,
or someone speaking to another person. If it is not clearly a request or question for you, call `ignore` and nothing else."""

DIRECTED_SYSTEM = """You judge whether speech picked up by a desktop voice assistant's microphone was meant for the assistant.
YES: a request, command or question for the assistant, or a direct reply to what it just said (e.g. "and tomorrow?", "open it", "thanks").
NO: people talking to each other, a lecture, a video or song playing, fragments, filler, or anything unrelated to the assistant.
When unsure, answer NO. Reply with exactly one word: YES or NO."""

ANSWER_SYSTEM = """You are Scar, a voice assistant. Using the tool results, answer the user's request in 1-3 short spoken
sentences. No lists, markdown or emojis. Round numbers sensibly. Never ask "anything else?" or offer more help. If a tool says a service could not be reached,
say that service didn't respond and suggest trying again; don't claim the whole PC is offline."""

VISION_SYSTEM = """You are Scar, a voice assistant looking at a screenshot of the user's screen. Answer their question about it
in 1-4 short spoken sentences: be specific (name the app, the error, the key point). No markdown or lists.
OCR text from the same screen is included to help with small text."""

# Sentence boundary: ., !, ? followed by space, or a newline. Keeps "3.5" and "e.g." mostly intact.
_SENT = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'])|\n+")
# For long first sentences, start speaking at a clause break instead of waiting for the full stop.
_CLAUSE = re.compile(r"(?<=[,;:])\s+")
CLAUSE_MIN_CHARS = 60
MAX_TOOL_CALLS = 3
MAX_STEPS = 8
# Router intents handled by the session itself (they change how Scar listens, not the PC).
CONTROLS = {"stop", "go_to_sleep", "standby", "learn_voice", "forget_voice", "only_me", "everyone", "dictation",
            "suggestions_on", "suggestions_off", "phone_on", "phone_off"}
# Requests that obviously need several steps ("X and then Y", "research ... and put it in a note").
_MULTI = re.compile(r"\b(and then|then|after that|afterwards|and also|and (?:open|search|play|click|type|put|save|send|write|close|tell|read|summari[sz]e|find|make|add|note))\b|,\s*(?:then|and)\b|\bresearch\b|\bcompare\b|\bstep by step\b", re.I)
_FAIL_PREFIXES = ("error", "No open", "Bad", "Unknown", "not found", "no ", "I can't", "Couldn't", "Wikipedia had no", "Web search for")


class Memory:
    def __init__(self):
        self.turns = []          # [{"role": ..., "content": ...}]
        self.last_window = None

    def add(self, user, assistant):
        self.turns += [{"role": "user", "content": user}, {"role": "assistant", "content": assistant or "(done)"}]
        self.turns = self.turns[-config.HISTORY_TURNS * 2:]
        if runtime.memory is not None:
            runtime.memory.add("conversation", user[:120], f"User: {user}\nScar: {assistant or '(done)'}")


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

    async def handle(self, text: str, timings: dict, follow_up=False, quiet=False):
        """Returns "ignored" when a follow-up turn turned out not to be meant for Scar."""
        text = text.strip()
        if not text:
            return None
        # Obvious multi-step requests go to the planner; the router would swallow the whole sentence as one command.
        hits = [] if _MULTI.search(text) and len(text.split()) > 5 else route_all(text)
        for name, args in hits:
            timings["route"] = "router"
            if name in CONTROLS:
                await self.on_control(name, args)
                return None
            if follow_up and name == "confirm" and self.tools.pending_confirm is None:
                return "ignored"          # a stray "yes" / "no" after an answer: nothing to do
            log.info("router -> %s %s", name, args)
            result = await self.tools.call(name, args, from_router=True)
            if result is FALLTHROUGH:
                continue                  # e.g. "open opera bukhachot" isn't an app: try the next phrasing
            timings["first_action"] = time.perf_counter()
            if name in DATA_TOOLS or isinstance(result, dict):
                await self._answer_from(text, name, args, result, timings)
            else:
                self.memory.add(text, f"[{name} {args}] {result}")
            return "end" if name == "smalltalk" else None
        text = fix(text)
        if follow_up and len(text.split()) > 30:
            log.info("ignoring long follow-up utterance (likely background talk)")
            return "ignored"
        if follow_up and not await self._is_directed(text):
            log.info("follow-up judged not addressed to Scar: %r", text)
            return "ignored"
        timings["route"] = "llm"
        return await self._llm_turn(text, timings, follow_up)

    async def _is_directed(self, text):
        """Tiny yes/no LLM check for wake-word-free follow-ups: was this said *to* the assistant?
        A dedicated question is far more reliable for a small model than offering an `ignore` tool."""
        last = next((t["content"] for t in reversed(self.memory.turns) if t["role"] == "assistant"), "")
        messages = [
            {"role": "system", "content": DIRECTED_SYSTEM},
            {"role": "user", "content": f'Assistant just said: "{last[:300]}"\nThen the microphone heard: "{text}"\n'
                                        "Was that spoken TO the assistant? Answer YES or NO."},
        ]
        answer = ""
        try:
            async for kind, data in self.llm.stream(messages, max_tokens=3):
                if kind == "text":
                    answer += data
        except Exception as e:  # noqa: BLE001 - if the check fails, don't drop a real request
            log.warning("directed check failed: %s", e)
            return True
        return not answer.strip().upper().startswith("NO")

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
                    if sentence.strip() and not _is_filler(sentence):
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
        """Act -> observe -> continue. Single commands take one LLM round; multi-step requests
        ("open YouTube, search lofi and play the first one") loop until done, up to MAX_STEPS."""
        system = self._system() + (FOLLOW_UP_RULE if follow_up else "")
        tools = SCHEMA + ([IGNORE_TOOL] if follow_up else [])
        messages = [{"role": "system", "content": system}, *self.memory.turns, {"role": "user", "content": text}]
        multi_step = bool(_MULTI.search(text))
        said, trail = [], []
        for step in range(MAX_STEPS):
            spoken, calls = await self._stream_speech(messages, timings, tools=tools)
            said += spoken
            if not calls:
                break
            calls = calls[:MAX_TOOL_CALLS]
            if step == 0 and any(c["name"] == "ignore" for c in calls):
                log.info("LLM judged the follow-up as not addressed to Scar")
                return "ignored"
            timings.setdefault("first_token", time.perf_counter())
            # Fast path: a lone screen question is answered straight from the image, spoken as it streams.
            if step == 0 and not multi_step and len(calls) == 1 and calls[0]["name"] == "look_at_screen":
                shot = await self.tools.call("look_at_screen", calls[0]["arguments"])
                timings.setdefault("first_action", time.perf_counter())
                if isinstance(shot, dict):
                    return await self._vision_answer(text, shot, timings)
            messages.append({"role": "assistant", "content": " ".join(spoken) or None, "tool_calls": [
                {"id": c["id"], "type": "function", "function": {"name": c["name"], "arguments": c["raw"]}} for c in calls]})
            need_more = multi_step
            for c in calls:
                log.info("llm tool (step %d) -> %s %s", step + 1, c["name"], c["arguments"])
                result = await self.tools.call(c["name"], c["arguments"])
                timings.setdefault("first_action", time.perf_counter())
                if isinstance(result, dict) and "image" in result:
                    result = "Screen shows: " + await self._describe(result)
                result = str(result)
                trail.append(f"[{c['name']}] {result[:120]}")
                messages.append({"role": "tool", "tool_call_id": c["id"], "content": result[:3500]})
                # Information has to be phrased for the user; failures need a reaction.
                need_more |= c["name"] in DATA_TOOLS or result.startswith(_FAIL_PREFIXES)
            if not need_more:
                break
        self.memory.add(text, " ".join(said) + (" " + "; ".join(trail) if trail else ""))
        if not said and any(t.split("] ", 1)[1].startswith(_FAIL_PREFIXES) for t in trail):
            await self.speak("Sorry, I couldn't do that.")
        return None

    async def _describe(self, shot) -> str:
        """Vision model's description of a screenshot, as text for the next reasoning step (not spoken)."""
        buf = io.BytesIO()
        shot["image"].save(buf, "JPEG", quality=80)
        b64 = base64.b64encode(buf.getvalue()).decode()
        messages = [{"role": "system", "content": "Describe the screenshot precisely for an assistant that must act on it: "
                                                  "the app, what is shown, and the exact visible names of buttons, links, results or errors."},
                    {"role": "user", "content": [
                        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
                        {"type": "text", "text": f"Focus: {shot.get('question', '')}\nOCR text:\n{(shot.get('ocr') or '')[:2000]}"}]}]
        out = ""
        try:
            async for kind, data in self.llm.stream(messages, max_tokens=300):
                if kind == "text":
                    out += data
        except Exception as e:  # noqa: BLE001 - fall back to the OCR text
            log.warning("describe failed: %s", e)
            out = (shot.get("ocr") or "")[:2000]
        return out.strip() or "nothing readable"

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
    t = s.strip().lower().strip(".!")
    # Small models sometimes write the tool name as text ("[look_at_screen]") instead of calling it.
    return t in ("", "done", "ok", "okay") or re.fullmatch(r"[\[(<{]?\s*[a-z_]+\s*[\])>}]?", t) is not None and "_" in t
