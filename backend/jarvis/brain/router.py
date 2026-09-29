"""Fast path: regex intents for the commands people say most. A hit skips the LLM entirely (<5 ms).
Returns (tool_name, args) matching the tool schema in tools.py, or None to fall through to the LLM."""
import re

_PREFIX = re.compile(r"^(?:(?:hey|hi|ok|okay|yo)\s+)?(?:jarvis\W*\s*)?(?:(?:please|can you|could you|would you|will you|kindly|just|now|and|then|also)\s+)*")
_SUFFIX = re.compile(r"\s+(?:please|for me|now|right now|thanks|thank you)$")
_NUM = {"one": "1", "two": "2", "three": "3", "four": "4", "nine": "9", "sixteen": "16", "twenty one": "21"}


def clean(text: str) -> str:
    t = text.lower().strip()
    t = re.sub(r"[^\w\s:'./-]", " ", t)          # drop punctuation whisper adds, keep 16:9 / urls
    t = re.sub(r"\s+", " ", t).strip(" .")
    t = _PREFIX.sub("", t)
    for _ in range(2):
        t = _SUFFIX.sub("", t)
    return t.strip()


LAYOUT_WORDS = {
    "left": "left", "left side": "left", "left half": "left", "right": "right", "right side": "right", "right half": "right",
    "top": "top", "top half": "top", "bottom": "bottom", "bottom half": "bottom",
    "top left": "top_left", "top right": "top_right", "bottom left": "bottom_left", "bottom right": "bottom_right",
    "top left corner": "top_left", "top right corner": "top_right", "bottom left corner": "bottom_left", "bottom right corner": "bottom_right",
    "left third": "left_third", "right third": "right_third", "middle": "center", "center": "center", "centre": "center",
    "other screen": "next_monitor", "other monitor": "next_monitor", "next screen": "next_monitor", "second screen": "next_monitor",
}
_LAYOUT_RE = "|".join(sorted(map(re.escape, LAYOUT_WORDS), key=len, reverse=True))
_WIN = r"(?P<app>.+?)"

RULES = []


def rule(pattern):
    def deco(fn):
        RULES.append((re.compile(pattern), fn))
        return fn
    return deco


# --- conversation control ---------------------------------------------------------
@rule(r"^(?:stop|cancel|never ?mind|nevermind|shut up|be quiet|quiet|stop talking|that's enough|forget it)$")
def _stop(m):
    return "stop", {}


@rule(r"^(?:go to sleep|sleep|goodbye|good bye|bye|that's all|that is all|see you|dismiss|go away|hide)$")
def _sleep(m):
    return "go_to_sleep", {}


@rule(r"^(?:yes|yeah|yep|sure|do it|go ahead|confirm|close it anyway|yes close it|ok do it)$")
def _yes(m):
    return "confirm", {"answer": True}


@rule(r"^(?:no|nope|don't|do not|keep it|no thanks|no thank you)$")
def _no(m):
    return "confirm", {"answer": False}


@rule(r"^(?:thanks|thank you|thank you jarvis|cool|great|awesome|nice|perfect)$")
def _thanks(m):
    return "smalltalk", {"kind": "thanks"}


# --- time -------------------------------------------------------------------------
@rule(r"^(?:what(?:'s| is) the time|what time is it|time|tell me the time)(?: now)?$")
def _time(m):
    return "get_time", {"what": "time"}


@rule(r"^(?:what(?:'s| is) (?:the |today's )?date|what day is (?:it|today)|what(?:'s| is) today)$")
def _date(m):
    return "get_time", {"what": "date"}


# --- theme ------------------------------------------------------------------------
@rule(r"^(?:switch to |change to |turn on |enable |go |use |set )?(?P<mode>dark|light)(?: mode| theme)?(?: on)?$")
def _theme(m):
    return "set_theme", {"mode": m["mode"]}


# --- media / volume ---------------------------------------------------------------
@rule(r"^(?:turn (?:the )?volume up|volume up|louder|turn it up|increase (?:the )?volume)$")
def _vol_up(m):
    return "media", {"action": "volume_up"}


@rule(r"^(?:turn (?:the )?volume down|volume down|quieter|turn it down|decrease (?:the )?volume|lower (?:the )?volume)$")
def _vol_down(m):
    return "media", {"action": "volume_down"}


@rule(r"^(?:mute|unmute)(?: (?:the )?(?:sound|volume|audio))?$")
def _mute(m):
    return "media", {"action": "mute"}


@rule(r"^(?:pause|play|resume|pause (?:the )?(?:music|song|video)|play (?:the )?(?:music|song|video)|resume (?:the )?(?:music|song|video))$")
def _play(m):
    return "media", {"action": "play_pause"}


@rule(r"^(?:next|skip)(?: (?:song|track|video))?$|^play (?:the )?next (?:song|track|video)$")
def _next(m):
    return "media", {"action": "next"}


@rule(r"^(?:previous|go back)(?: (?:song|track|video))$|^play (?:the )?previous (?:song|track|video)$")
def _prev(m):
    return "media", {"action": "previous"}


# --- browser tabs -----------------------------------------------------------------
@rule(r"^close (?:this|the current|current|that|the) tab$|^close tab$")
def _close_tab(m):
    return "browser_tab", {"action": "close"}


@rule(r"^(?:open |start )?(?:a )?new tab$")
def _new_tab(m):
    return "browser_tab", {"action": "new"}


@rule(r"^(?:switch|go|jump|change) to (?:the )?(?P<q>.+?) tab$")
def _switch_tab(m):
    return "browser_tab", {"action": "switch", "query": m["q"]}


# --- search / open ----------------------------------------------------------------
@rule(r"^(?:search|look up|find) (?:on )?(?P<engine>youtube|google|wikipedia|amazon|github|maps) for (?P<q>.+)$")
def _search1(m):
    return "web_search", {"engine": m["engine"], "query": m["q"]}


@rule(r"^(?:search|look up|google) (?:for )?(?P<q>.+?) on (?P<engine>youtube|google|wikipedia|amazon|github|maps)$")
def _search2(m):
    return "web_search", {"engine": m["engine"], "query": m["q"]}


@rule(r"^play (?P<q>.+?) on youtube$")
def _yt_play(m):
    return "web_search", {"engine": "youtube", "query": m["q"]}


@rule(r"^(?:google|search for|search) (?P<q>.+)$")
def _google(m):
    return "web_search", {"engine": "google", "query": m["q"]}


@rule(r"^(?:open|launch|start|run|go to|take me to) (?P<target>.+)$")
def _open(m):
    t = m["target"]
    if re.match(r"^[\w-]+(\.[\w-]+)+(/\S*)?$", t):  # something.com
        return "open_url", {"url": t}
    return "open", {"name": t}


# --- windows ----------------------------------------------------------------------
@rule(r"^(?:minimi[sz]e|hide) (?:all|everything|all (?:the )?windows|all apps)$|^show (?:me )?(?:the )?desktop$")
def _min_all(m):
    return "minimize_all", {}


@rule(rf"^(?P<act>minimi[sz]e|maximi[sz]e|restore|close|quit|exit|kill|focus|bring up|switch to|show me|show) {_WIN}$")
def _win(m):
    act = m["act"]
    action = ("minimize" if act.startswith("minim") else "maximize" if act.startswith("maxim") else
              "close" if act in ("close", "quit", "exit", "kill") else "restore" if act == "restore" else "focus")
    return "window_action", {"app": m["app"], "action": action}


@rule(rf"^(?:make|set|put) {_WIN} (?:to )?(?:full ?screen|maximi[sz]ed|big)$")
def _fullscreen(m):
    return "window_action", {"app": m["app"], "action": "maximize"}


@rule(rf"^(?:snap|move|put|drag|throw|send|push|place|dock) {_WIN} (?:to |on |onto |into |in |over to )?(?:the )?(?P<where>{_LAYOUT_RE})(?: side| half| of the screen| part)?$")
def _snap(m):
    where = LAYOUT_WORDS[m["where"]]
    if where == "next_monitor":
        return "move_window", {"app": m["app"], "where": "next_monitor"}
    return "move_window", {"app": m["app"], "where": where}


@rule(rf"^(?:make|resize|set|change|turn) {_WIN} (?:to |into )?(?:an? |the )?(?P<w>\d+|one|two|three|four|nine|sixteen|twenty one) ?(?:by|:|x|to|/) ?(?P<h>\d+|one|two|three|four|nine)(?: ratio| aspect(?: ratio)?)?$")
def _ratio(m):
    w, h = _NUM.get(m["w"], m["w"]), _NUM.get(m["h"], m["h"])
    return "resize_window", {"app": m["app"], "preset": f"{w}:{h}"}


@rule(rf"^(?:make|resize) {_WIN} (?P<size>bigger|larger|smaller|tinier|half (?:the )?size|double (?:the )?size|square)$")
def _size(m):
    s = m["size"]
    preset = ("bigger" if s in ("bigger", "larger") or s.startswith("double") else
              "1:1" if s == "square" else "smaller")
    return "resize_window", {"app": m["app"], "preset": preset}


def route(text: str):
    t = clean(text)
    if not t:
        return None
    for pattern, fn in RULES:
        m = pattern.match(t)
        if m:
            return fn(m)
    return None
