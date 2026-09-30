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
from pathlib import Path

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
Only open or search things in the browser when the user asks you to search, look up, open or play something; for
suggestions, ideas, opinions, jokes and general questions, just answer yourself.
Use tools for anything on the PC: windows, apps, sites, search, media, volume, brightness, timers, reminders, notes, to-dos,
system status, weather, files, reading or looking at the screen, clipboard and typing. Never claim an action without calling its tool.
Action tools speak their own short confirmation, so after one say nothing more unless something needs explaining.
Call at most 3 tools per reply. "Hide", "put away" or "get rid of for now" means minimize; only close when told close, quit or exit.
For questions about what is on the screen, an error, a page or "this", call look_at_screen.
For multi-step requests, do a step or two, check the results (read_screen / look_at_screen show what's there, e.g. exact
names to click), then continue until the whole request is done, and finish with one short spoken summary.
For current facts, prices, news or comparisons, call web_research. To write emails, messages or text into an app, call write_text.
NEVER invent facts: product names, prices, numbers, dates and quotes must come from tool results (or be general knowledge you
are sure of). If a search or tool found nothing, say so plainly and do not save or type made-up content.
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
            "suggestions_on", "suggestions_off", "phone_on", "phone_off", "listen_always", "listen_on_call",
            "mic_on", "mic_off", "place"}
# Requests that obviously need several steps ("X and then Y", "research ... and put it in a note").
_MULTI = re.compile(r"\b(and then|then|after that|afterwards|and also|and (?:open|search|play|click|type|put|save|send|write|close|tell|read|summari[sz]e|find|make|add|note))\b|,\s*(?:then|and)\b|\bresearch\b|\bcompare\b|\bstep by step\b", re.I)
# Follow-ups phrased as a request to Scar are accepted without asking the LLM whether they were meant for it
# ("I would like to watch In the End", "can you tell me Bangalore's weather", "add a new document in notepad").
_REQUEST = re.compile(r"^\W*(?:(?:ok(?:ay)?|and|also|now|so|then|oh|um|uh|hey)\W+)*(?:can you|could you|would you|will you|please|"
                      r"i (?:want|would like|wanna|need|'d like)|let's|let me|show me|tell me|give me|help me|how (?:do|can|about)|what(?:'s| is| about)|"
                      r"open|close|play|pause|stop|search|find|type|write|add|remove|delete|set|turn|make|put|move|minimi[sz]e|maximi[sz]e|"
                      r"switch|go to|start|create|save|read|check|click|press|scroll|take|remind|call|send|explain|summari[sz]e|translate)\b", re.I)
# Pure knowledge / chat questions skip the ~6k-token tool list: faster, and no stray tool calls (a "fun fact"
# question once opened a Google search). Anything about the PC, the screen, files, or current events keeps the tools.
_KNOWLEDGE = re.compile(r"^\W*(?:(?:hey|ok|okay|so|and|also|um|uh)\W+)*(?:can you |could you |please )?(?:tell me (?:a |an |some |about |why |how |what )|"
                        r"(?:give me |share )?(?:a |an )?(?:fun |random |interesting |cool )?fact|explain|what (?:is|are|was|were|does|do|did|makes|causes|happens)|"
                        r"who (?:is|was|were|invented|discovered|wrote|painted|founded)|why (?:is|are|do|does|did|can't|cannot)|how (?:does|do|did|is|are|many|much|far|old|big|long)|"
                        r"when (?:was|did|is)|where (?:is|are|was|did)|define|describe|compare|summari[sz]e the (?:story|plot|history)|"
                        r"(?:tell me )?(?:a )?(?:joke|riddle|poem|story)|what's the difference|which is (?:better|bigger|faster))", re.I)
_NEEDS_TOOLS = re.compile(r"\b(?:screen|window|tab|file|folder|document|pdf|my |open|close|click|type|search|play|volume|brightness|"
                          r"battery|wifi|bluetooth|timer|remind|note|schedule|calendar|weather|news|today|tonight|tomorrow|yesterday|"
                          r"latest|current|currently|now|price|cost|stock|score|live|this|that|these|here|app|pc|computer|laptop|"
                          r"system|cpu|ram|memory|disk|clipboard|copied|download|youtube|spotify|whatsapp|chrome|code|error)\b", re.I)
# Suggestions and ideas are answered from the model's own head too, even when they mention "my" ("a name for my cat").
_IDEAS = re.compile(r"^\W*(?:(?:hey|ok|okay|so|um)\W+)*(?:can you |could you |please )?(?:suggest|recommend|brainstorm|think of|come up with|"
                    r"give me (?:some |a few |a |an )?(?:ideas?|names?|suggestions?|tips|options|recipes?)|"
                    r"what (?:should|could|can) i (?:name|call|cook|make|eat|gift|get|buy|watch|read|say|write|do))", re.I)
_LIVE = re.compile(r"\b(?:screen|window|file|folder|open|search|youtube|spotify|weather|news|price|prices|today|tonight|latest|"
                   r"current|now|near me|this|that)\b", re.I)
KNOWLEDGE_SYSTEM = """You are {name}, a warm, witty voice assistant. Answer from your own knowledge in 1-3 short spoken sentences
(up to 5 if asked to explain). No lists, markdown, emojis or URLs. Facts must be true: if you are not sure, say so briefly
instead of guessing. Jokes, riddles and stories are fine when asked for, but never present something false as a fact."""
_TYPE_ORIGINAL = re.compile(r"(?i)\btype(?:\s+out)?[\s,:]+(.+?)\s*$")
_FAIL_PREFIXES = ("error", "No open", "Bad", "Unknown", "not found", "no ", "I can't", "Couldn't", "Wikipedia had no", "Web search for")


def looks_like_request(text: str) -> bool:
    return bool(_REQUEST.match(text)) and 2 <= len(text.split()) <= 20


class Memory:
    def __init__(self):
        self.turns = []          # [{"role": ..., "content": ...}]
        self.last_window = None

    def add(self, user, assistant):
        self.turns += [{"role": "user", "content": user}, {"role": "assistant", "content": assistant or "(done)"}]
        if len(self.turns) > config.HISTORY_TURNS * 2:
            # Trim by half, not one turn at a time: a sliding window changes the prompt's start every turn and
            # defeats the LLM's prompt cache; this way the cache is only rebuilt every few turns.
            self.turns = self.turns[-config.HISTORY_TURNS:]
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
        from jarvis.core import settings
        style = settings.PERSONALITY.get(settings.get("personality"), "")
        brain = (f"a cloud model ({config.CLOUD_LLM_MODEL}) via the user's own API key, with a local fallback" if config.CLOUD_LLM_URL
                 else f"a local model ({Path(config.LLM_GGUF).stem}) through llama.cpp on this PC, with no API key and no cloud")
        style = (f"About yourself (only if asked): you run {brain}; speech recognition is local Whisper, your voice is "
                 f"local Kokoro. Users can change your settings by voice (say 'what are your settings').\n") + style
        return SYSTEM.format(name=config.NAME, today=today, facts=facts_txt) + ("\n" + style if style else "")

    async def warm_up(self):
        """Prime llama.cpp's prompt cache with the system prompt + tool schema, so the first real
        question doesn't pay ~10 s to process them. One real chat request first (the first chat request after
        llama-server starts never reused a completion-primed prefix), then the precise prime."""
        messages = [{"role": "system", "content": self._system()}, {"role": "user", "content": "hi"}]
        try:
            async for _ in self.llm.stream(messages, tools=SCHEMA + [IGNORE_TOOL], max_tokens=1):
                pass
        except Exception as e:  # noqa: BLE001 - warm-up is best effort
            log.warning("LLM warm-up failed: %s", e)
        await self._prime()

    async def _prime(self):
        """Lay down a llama.cpp save point right after the current conversation, so the next question only
        processes its own words. (Qwen3.5 is a hybrid model: the cache can only resume from such save points, and the
        chat template writes past replies slightly differently from how they were generated, so without this every
        turn re-read the whole ~7k-token prompt: 8-10 s.)"""
        messages = [{"role": "system", "content": self._system()}, *self.memory.turns]
        try:
            if hasattr(self.llm, "prime"):
                await self.llm.prime(messages, SCHEMA + [IGNORE_TOOL])
        except Exception as e:  # noqa: BLE001 - priming is best effort
            log.debug("LLM priming failed: %s", e)

    async def handle(self, text: str, timings: dict, follow_up=False, quiet=False):
        """Returns "ignored" when a follow-up turn turned out not to be meant for Scar."""
        turns_before = len(self.memory.turns)
        try:
            return await self._handle(text, timings, follow_up, quiet)
        finally:
            if len(self.memory.turns) != turns_before or turns_before == 0:
                import asyncio
                self._prime_task = asyncio.get_running_loop().create_task(self._prime())

    async def _handle(self, text: str, timings: dict, follow_up=False, quiet=False):
        text = text.strip()
        if not text:
            return None
        # Obvious multi-step requests go to the planner; the router would swallow the whole sentence as one command.
        hits = [] if _MULTI.search(text) and len(text.split()) > 5 else route_all(text)
        for name, args in hits:
            timings["route"] = "router"
            if name == "repeat":
                return await self._repeat(args.get("what"), timings)
            if name == "type_text":
                m = _TYPE_ORIGINAL.search(text)       # the router lower-cases; type what was actually said
                if m and args.get("text") != "cls":
                    typed = m.group(1).strip(" ,")
                    if args.get("app"):               # "type dir /s on command prompt": the app isn't part of the text
                        typed = re.sub(rf"\s+(?:in|on|into|inside)\s+(?:the\s+|my\s+)?(?:{re.escape(args['app'])}|cmd)\W*$", "", typed, flags=re.I)
                    args = {**args, "text": typed.rstrip("?.!") if args.get("app") else typed}
            if name == "whatsapp":
                m = re.search(r"(?i)\b(?:saying|that says|that)[:,]?\s+(.+?)\s*$", text)
                if m:
                    args = {**args, "message": m.group(1)}   # keep capitals and punctuation for the message
            if name == "look_at_screen" and len(text.split()) >= 5:
                args = {**args, "question": text}     # keep the user's actual question, not a generic "describe it"
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
                self.memory.add(text, str(result))   # plain words: a "[tool {args}]" tag here gets parroted aloud by the LLM
            self.last_request = text
            return "end" if name == "smalltalk" else None
        text = fix(text)
        if follow_up and len(text.split()) > 30:
            log.info("ignoring long follow-up utterance (likely background talk)")
            return "ignored"
        if follow_up and not looks_like_request(text) and not await self._is_directed(text):
            log.info("follow-up judged not addressed to Scar: %r", text)
            return "ignored"
        self.last_request = text
        if (_KNOWLEDGE.match(text) and not _NEEDS_TOOLS.search(text)) or (_IDEAS.match(text) and not _LIVE.search(text)):
            timings["route"] = "knowledge"
            return await self._knowledge_turn(text, timings)
        timings["route"] = "llm"
        return await self._llm_turn(text, timings, follow_up)

    async def _knowledge_turn(self, text, timings):
        from jarvis.core import settings
        style = settings.PERSONALITY.get(settings.get("personality"), "")
        messages = [{"role": "system", "content": KNOWLEDGE_SYSTEM.format(name=config.NAME) + ("\n" + style if style else "")},
                    *self.memory.turns[-6:], {"role": "user", "content": text}]
        spoken, _ = await self._stream_speech(messages, timings, max_tokens=300)
        self.memory.add(text, " ".join(spoken))
        return None

    async def _repeat(self, what, timings):
        """"Say that again" repeats the last answer; "do that again" re-runs the last request."""
        if what == "action":
            last = getattr(self, "last_request", None)
            if not last:
                await self.speak("There's nothing to repeat yet.")
                return None
            log.info("repeating: %r", last)
            return await self.handle(last, timings)
        speech = getattr(self.speak, "__self__", None)      # SpeechOut: remembers what was really said aloud
        said = list(getattr(speech, "prev_said", []) or [])
        if not said:
            await self.speak("I haven't said anything yet.")
        for sentence in said:
            await self.speak(sentence)
        return None

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
        # System prompt and tool list are byte-identical on every turn (the follow-up rule rides on the user message),
        # so llama.cpp can reuse the cached ~7k-token prefix instead of re-reading it.
        tools = SCHEMA + [IGNORE_TOOL]
        user = text + ("\n\n(" + FOLLOW_UP_RULE.strip() + ")" if follow_up else "")
        messages = [{"role": "system", "content": self._system()}, *self.memory.turns, {"role": "user", "content": user}]
        multi_step = bool(_MULTI.search(text))
        said, trail, evidence = [], [], [text]   # evidence: everything the user and the tools actually said
        uses = {}                                  # tool name -> calls this turn (stops retry loops)
        self.tools.spoken_fillers = set()
        for step in range(MAX_STEPS):
            spoken, calls = await self._stream_speech(messages, timings, tools=tools)
            said += spoken
            if not calls:
                break
            calls = calls[:MAX_TOOL_CALLS]
            if not follow_up:
                calls = [c for c in calls if c["name"] != "ignore"]   # only meaningful for wake-word-free follow-ups
                if not calls:
                    break
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
                uses[c["name"]] = uses.get(c["name"], 0) + 1
                if c["name"] in DATA_TOOLS and uses[c["name"]] > 2:
                    messages.append({"role": "tool", "tool_call_id": c["id"], "content":
                                     "error: already tried this twice. Stop and tell the user briefly what you found or that it failed."})
                    need_more = True
                    continue
                ungrounded = _ungrounded(c, evidence)
                if ungrounded:
                    # Small models make up products and prices when a search fails. Don't let that reach the user.
                    log.warning("blocked ungrounded %s: %s", c["name"], ungrounded)
                    result = ("error: refused, these details are not in any search or tool result: " + ", ".join(ungrounded[:6]) +
                              ". Tell the user plainly that you couldn't find reliable information. Do not retry with made-up data.")
                    messages.append({"role": "tool", "tool_call_id": c["id"], "content": result})
                    trail.append(f"[{c['name']}] {result[:120]}")
                    need_more = True
                    continue
                result = await self.tools.call(c["name"], c["arguments"])
                if c["name"] in DATA_TOOLS:
                    evidence.append(str(result))
                timings.setdefault("first_action", time.perf_counter())
                if isinstance(result, dict) and "image" in result:
                    result = "Screen shows: " + await self._describe(result)
                result = str(result)
                trail.append(f"[{c['name']}] {result[:120]}")
                messages.append({"role": "tool", "tool_call_id": c["id"], "content": result[:TOOL_RESULT_CHARS]})
                _fit_budget(messages)
                # Information has to be phrased for the user; failures need a reaction.
                need_more |= c["name"] in DATA_TOOLS or result.startswith(_FAIL_PREFIXES)
            if not need_more:
                break
        self.memory.add(text, " ".join(said) + (" " + "; ".join(trail) if trail else ""))
        if not said and any(t.split("] ", 1)[1].startswith(_FAIL_PREFIXES) for t in trail):
            if any("Web search" in t or "search service" in t for t in trail):
                await self.speak("I couldn't reach the search engines just now, so I didn't make anything up. Try again in a few minutes.")
            else:
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
        self.tools.last_seen = out.strip()
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
        self.tools.last_seen = " ".join(spoken)     # "click that" / "open the second one" refer to this


_WRITES = {"take_note": "text", "write_text": "instructions", "add_todo": "item", "type_text": "text"}
_FACT = re.compile(r"\b\d[\d,.]{2,}\b|\b[A-Z][a-zA-Z]+(?:\s+[A-Z0-9][a-zA-Z0-9]+)+\b")


def _ungrounded(call, evidence):
    """For notes/text written after research in this turn: facts (numbers, multi-word names) that no tool
    result mentions. Returns the unsupported ones, or [] when fine / not applicable."""
    field = _WRITES.get(call["name"])
    if not field or len(evidence) < 2:          # only guard content that claims to come from lookups
        return []
    content = str(call["arguments"].get(field, ""))
    seen = " ".join(evidence).lower().replace(",", "")
    facts = {f.strip() for f in _FACT.findall(content)}
    missing = [f for f in facts if f.lower().replace(",", "") not in seen]
    return missing if facts and len(missing) / len(facts) > 0.4 else []


TOOL_RESULT_CHARS = 3000
CONTEXT_CHARS = 20000   # ~6k tokens of conversation on top of the ~4k-token tool schema, inside a 12k context


def _fit_budget(messages):
    """Keep a long multi-step task inside the context window: shrink the oldest tool results first."""
    def size():
        return sum(len(m["content"]) if isinstance(m.get("content"), str) else 2000 for m in messages)
    for m in messages:
        if size() <= CONTEXT_CHARS:
            return
        if m.get("role") == "tool" and len(m["content"]) > 400:
            m["content"] = m["content"][:400] + " ...(trimmed)"


def _is_filler(s):
    t = s.strip().lower().strip(".!")
    # Small models sometimes write the tool name as text ("[look_at_screen]") instead of calling it.
    return t in ("", "done", "ok", "okay") or re.fullmatch(r"[\[(<{]?\s*[a-z_]+\s*[\])>}]?", t) is not None and "_" in t
