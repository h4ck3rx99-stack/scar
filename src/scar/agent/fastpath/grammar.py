"""Deterministic fast path (C8.2): common intents without an LLM.

Returns a ``FastPlan`` (tool calls + how to phrase the result) or ``None``
when the utterance is not a confident match — the LLM path handles those.
Fast-path actions still run through the full tool pipeline and permissions.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from scar.agent.fastpath.entities import FolderResolver

_URL = r"(?P<url>(?:https?://|www\.)\S+|[\w-]+(?:\.[\w-]+)+(?:/\S*)?|localhost:\d+\S*)"
EDITORS = r"(?P<app>vs ?code|visual studio code|code|cursor|zed)"


@dataclass
class FastCall:
    tool: str
    args: dict[str, Any]


@dataclass
class FastPlan:
    intent: str
    calls: list[FastCall] = field(default_factory=list)
    reply: str | None = None  # fixed reply when no tool is needed (e.g. clarification)
    clarify: tuple[str, list[str]] | None = None  # (question, options)
    confidence: float = 1.0
    control: str | None = None  # runtime control intents: "status", "cancel"


def _norm(text: str) -> str:
    t = text.strip()
    t = re.sub(r"^(hey\s+scar[,!]?\s*|scar[,!]\s*|please\s+|can you\s+|could you\s+|would you\s+)", "", t, flags=re.I)
    return re.sub(r"[?!.]+$", "", t).strip()


class FastPath:
    def __init__(self, services: Any) -> None:
        self.s = services
        self.folders = FolderResolver(services)

    def match(self, utterance: str) -> FastPlan | None:
        t = _norm(utterance)
        low = t.lower()
        if not t or len(t) > 240:
            return None
        compound = re.search(r"(?i)(,|;|\bthen\b|\band\b)\s*(start|run|open|tell|let|create|send|email|save|write|fix|find|"
                             r"search|commit|push|install|build|close|delete|summari[sz]e|notify|go|navigate|read|copy|move|watch)\b",
                             t)
        handlers = (self._control, self._remember, self._remind, self._tests, self._watch, self._editor_folder, self._browser_url,
                    self._screenshot, self._sysinfo, self._processes, self._media, self._window, self._open_app)
        if compound:
            # multi-step objectives belong to the planner; only whole-utterance intents stay deterministic
            handlers = (self._control, self._remember, self._remind, self._watch, self._browser_url)
        for handler in handlers:
            plan = handler(t, low)
            if plan is not None:
                return plan
        return None

    # ---------------------------------------------------------------- handlers
    def _control(self, t: str, low: str) -> FastPlan | None:
        if re.fullmatch(r"(stop|cancel|abort)( (it|that|the task|everything|all tasks))?", low):
            return FastPlan("cancel", control="cancel")
        if re.fullmatch(r"(what('s| is) the )?(task )?status|what are you doing|are you (done|busy)", low):
            return FastPlan("status", control="status")
        return None

    def _remember(self, t: str, low: str) -> FastPlan | None:
        m = re.match(r"(?i)^(?:remember|note)\s+(?:that\s+)?(.+)$", t)
        if m:
            return FastPlan("remember", [FastCall("memory.remember", {"text": m.group(1)})])
        m = re.match(r"(?i)^forget\s+(?:about\s+|that\s+)?(.+)$", t)
        if m:
            return FastPlan("forget", [FastCall("memory.forget", {"query": m.group(1)})])
        return None

    def _remind(self, t: str, low: str) -> FastPlan | None:
        # "remind me in 10 minutes to X" / "remind me at 5pm to X" / "remind me to X in 10 minutes"
        m = re.match(r"(?i)^remind me\s+((?:in|at|on|tomorrow|tonight|every|next)\b.+?)\s+(?:to|that|about)\s+(.+)$", t)
        if m:
            return FastPlan("reminder", [FastCall("schedule.reminder", {"when": m.group(1), "text": m.group(2)})])
        m = re.match(r"(?i)^remind me\s+(?:to|that|about)\s+(.+?)\s+((?:in|at|on|tomorrow|tonight|every|next)\b.+)$", t)
        if m:
            return FastPlan("reminder", [FastCall("schedule.reminder", {"when": m.group(2), "text": m.group(1)})])
        return None

    def _tests(self, t: str, low: str) -> FastPlan | None:
        m = re.match(r"(?i)^run (?:the |all (?:the )?)?tests(?: (?:in|for|of) (?:the )?(?P<folder>.+?))?(?: project| folder| repo)?$", t)
        if not m:
            return None
        phrase = m.group("folder")
        if not phrase or phrase.lower() in ("this", "this folder", "here", "this project"):
            cwd = self.s.extras.get("cwd")
            return FastPlan("run_tests", [FastCall("dev.run_tests", {"path": cwd} if cwd else {})])
        cands = self.folders.resolve(phrase)
        if len(cands) != 1:
            return None
        return FastPlan("run_tests", [FastCall("dev.run_tests", {"path": cands[0].path})])

    def _watch(self, t: str, low: str) -> FastPlan | None:
        m = re.match(r"(?i)^(?:watch|monitor)\s+(?:the\s+)?(?:process\s+)?(?:(?:pid|process)\s+)?(?P<target>\d+|[\w.-]+?)"
                     r"(?:\s+process)?\s+and\s+(?:tell|let|notify|alert)\s+me\s+(?:know\s+)?if\s+it\s+(?P<what>crashes|exits|stops|dies|ends)$", t)
        if not m:
            return None
        target = m.group("target")
        args: dict[str, Any] = {"kind": "process", "notify_on": "crash" if m.group("what") in ("crashes", "dies") else "any_exit"}
        if target.isdigit():
            args["pid"] = int(target)
        else:
            args["process_name"] = target
        return FastPlan("watch_process", [FastCall("monitor.start", args)])

    def _editor_folder(self, t: str, low: str) -> FastPlan | None:
        m = re.match(rf"(?i)^(?:open|launch|start)\s+{EDITORS}\s+(?:in|on|at|with|for)\s+(?:the\s+)?(?P<folder>.+?)(?:\s+folder)?$", t) \
            or re.match(rf"(?i)^open\s+(?P<folder>.+?)\s+in\s+{EDITORS}$", t)
        if not m:
            return None
        folder_phrase = m.group("folder")
        if folder_phrase.lower() in ("this folder", "here", "the current folder", "current folder", "this directory"):
            cwd = self.s.extras.get("cwd")
            if not cwd:
                return None
            folder_phrase = cwd
        cands = self.folders.resolve(folder_phrase)
        app = "vs code" if "code" in m.group("app").lower() else m.group("app").lower()
        if not cands:
            return FastPlan("open_editor", reply=f"I couldn't find a folder called \"{folder_phrase}\". Tell me its path "
                                                 "and I'll remember it.", confidence=0.9)
        if len(cands) > 1 and cands[0].source not in ("path", "memory alias"):
            return FastPlan("open_editor", clarify=(f"Which \"{folder_phrase}\" folder?", [c.path for c in cands[:5]]),
                            calls=[FastCall("apps.launch", {"app": app, "folder": "{choice}"})])
        return FastPlan("open_editor", [FastCall("apps.launch", {"app": app, "folder": cands[0].path})])

    def _browser_url(self, t: str, low: str) -> FastPlan | None:
        m = re.match(rf"(?i)^(?:open\s+(?P<browser>chrome|edge|the browser|a browser|browser)\s+(?:and\s+)?)?"
                     rf"(?:go to|navigate to|open|browse to|visit|load)\s+{_URL}$", t)
        if not m:
            m = re.match(rf"(?i)^open\s+(?P<browser>chrome|edge|the browser|browser)\s+(?:at|on|to)\s+{_URL}$", t)
        if not m:
            return None
        url = m.group("url")
        if not re.search(r"[./:]", url):
            return None
        return FastPlan("open_url", [FastCall("browser.open", {"url": url})])

    def _screenshot(self, t: str, low: str) -> FastPlan | None:
        if re.fullmatch(r"(take|grab|capture)( a)? (screenshot|screen ?shot|screen capture)( of (my|the) (whole )?screen)?", low):
            target = "screen" if "screen" in low.split("of")[-1] and "of" in low else "foreground"
            return FastPlan("screenshot", [FastCall("screen.capture", {"target": target})])
        if re.fullmatch(r"(look at|what'?s on|what is on|describe|read) (my|the) screen", low):
            return FastPlan("describe_screen", [FastCall("screen.describe", {"question": "What is on the screen?"})])
        return None

    def _sysinfo(self, t: str, low: str) -> FastPlan | None:
        if re.search(r"\b(what'?s|what is|which apps?|who'?s) (using|eating|hogging) (my|the) (ram|memory)\b", low):
            return FastPlan("top_memory", [FastCall("system.info", {"sections": ["memory", "top"], "sort_by": "memory"})])
        if re.search(r"\b(what'?s|what is|which apps?) (using|eating|hogging) (my|the) cpu\b", low):
            return FastPlan("top_cpu", [FastCall("system.info", {"sections": ["cpu", "top"], "sort_by": "cpu"})])
        if re.fullmatch(r"(system (info|status|information)|how('s| is) my (pc|computer|system)( doing)?|battery( status| level)?|"
                        r"gpu (usage|status)|disk space|how much (ram|memory|disk space)( do i have)?( left| free)?)", low):
            return FastPlan("sysinfo", [FastCall("system.info", {})])
        return None

    def _processes(self, t: str, low: str) -> FastPlan | None:
        if re.fullmatch(r"(list|show)( me)?( all| the| running)* processes", low):
            return FastPlan("processes", [FastCall("process.list", {})])
        m = re.match(r"(?i)^(?:kill|terminate|end)\s+(?:the\s+)?(?:process\s+)?(?P<name>[\w .-]+?)(?:\s+process)?$", t)
        if m:
            name = m.group("name").strip()
            if name.isdigit():
                return FastPlan("kill", [FastCall("process.stop", {"pid": int(name)})])
            return FastPlan("kill", [FastCall("process.stop", {"name": name if name.lower().endswith(".exe") else name + ".exe"})])
        return None

    def _media(self, t: str, low: str) -> FastPlan | None:
        m = re.fullmatch(r"(?:set )?(?:the )?volume (?:to )?(\d{1,3})\s*%?", low)
        if m:
            return FastPlan("volume", [FastCall("system.media", {"action": "set_volume", "level": min(100, int(m.group(1)))})])
        table = {r"(turn )?(the )?volume up|louder": ("volume_up", 5), r"(turn )?(the )?volume down|quieter": ("volume_down", 5),
                 r"mute|unmute|(toggle )?mute": ("mute", 1), r"(pause|play|resume)( (the )?(music|song|video|media))?": ("play_pause", 1),
                 r"(next|skip)( (track|song))?": ("next", 1), r"previous( (track|song))?|go back a (track|song)": ("previous", 1)}
        for pattern, (action, steps) in table.items():
            if re.fullmatch(pattern, low):
                return FastPlan("media", [FastCall("system.media", {"action": action, "steps": steps})])
        return None

    def _window(self, t: str, low: str) -> FastPlan | None:
        m = re.match(r"(?i)^(minimi[sz]e|maximi[sz]e|restore)\s+(?:the\s+)?(?P<app>.+?)(?:\s+window)?$", t)
        if m:
            action = {"minimi": "minimize", "maximi": "maximize", "restor": "restore"}[m.group(1).lower()[:6]]
            return self._window_call("windows.arrange", m.group("app"), {"action": action})
        m = re.match(r"(?i)^snap\s+(?:the\s+)?(?P<app>.+?)\s+(?:to\s+the\s+)?(left|right)$", t)
        if m:
            return self._window_call("windows.arrange", m.group("app"), {"action": f"snap_{m.group(2).lower()}"})
        m = re.match(r"(?i)^(?:switch to|focus|go to|bring up|show)\s+(?:the\s+)?(?P<app>.+?)(?:\s+window)?$", t)
        if m and not re.search(r"[./]", m.group("app")):
            return self._window_call("windows.focus", m.group("app"), {})
        m = re.match(r"(?i)^close\s+(?:the\s+)?(?P<app>[\w .-]+?)(?:\s+window)?$", t)
        if m:
            return self._window_call("windows.close", m.group("app"), {})
        return None

    def _window_call(self, tool: str, app_phrase: str, extra: dict[str, Any]) -> FastPlan | None:
        target = self._window_target(app_phrase)
        if target is None:
            return None
        return FastPlan(tool.split(".")[1], [FastCall(tool, {**target, **extra})], confidence=0.9)

    def _window_target(self, phrase: str) -> dict[str, Any] | None:
        from scar.tools.apps.index import SYNONYMS

        p = phrase.lower().strip()
        exe = next((s for s in SYNONYMS.get(p, []) if s.endswith(".exe")), None)
        if exe:
            return {"process": "Code.exe" if exe == "code.exe" else exe}
        if re.fullmatch(r"[\w.-]+\.exe", p):
            return {"process": p}
        if len(p) >= 3:
            return {"title": phrase.strip()}
        return None

    def _open_app(self, t: str, low: str) -> FastPlan | None:
        m = re.match(r"(?i)^(?:open|launch|start|run)\s+(?:the\s+)?(?:app\s+)?(?P<app>[\w .+'-]{2,40}?)(?:\s+app)?$", t)
        if not m:
            return None
        app = m.group("app").strip()
        if re.search(r"\b(and|then|with|in|on|at|to|for)\b", app.lower()):
            return None
        idx = getattr(self.s, "app_index", None)
        if idx is None:
            return None
        hits = idx.search(app, 3)
        if not hits or hits[0][1] < 0.85:
            return None
        return FastPlan("open_app", [FastCall("apps.launch", {"app": app})], confidence=hits[0][1])
