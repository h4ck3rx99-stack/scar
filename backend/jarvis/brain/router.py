"""Fast path: regex intents for the commands people say most. A hit skips the LLM entirely (<5 ms).
Returns (tool_name, args) matching the tool schema in tools.py, or None to fall through to the LLM."""
import re

_PREFIX = re.compile(r"^(?:(?:hey|hi|ok|okay|yo)\s+)?(?:(?:jarvis|scar|skar|scott)\W*\s*)?(?:(?:please|can you|could you|would you|will you|kindly|just|now|and|then|also|so|okay|i want you to|i need you to|go ahead and|try to|quickly)\s+)*")
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


@rule(r"^(?:go |get |switch )?(?:on |to |into )?stand ?by(?: mode)?$|^(?:go )?offline$|^disappear$|^leave me alone$|^take a break$")
def _standby(m):
    return "standby", {}


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


# --- keyboard actions / clicking / scrolling / settings / power ----------------------
_KEY_PHRASES = [
    (r"copy(?: (?:that|this|it|the text))?", "copy"), (r"paste(?: (?:it|that|here))?", "paste"), (r"cut(?: (?:that|this|it))?", "cut"),
    (r"undo(?: (?:that|it))?", "undo"), (r"redo(?: (?:that|it))?", "redo"), (r"select (?:all|everything)", "select_all"),
    (r"save(?: (?:this|it|the file|the document|my work))?", "save"), (r"(?:find|search) (?:on |in )?(?:this|the) page", "find"),
    (r"(?:refresh|reload)(?: (?:the |this )?page)?", "refresh"), (r"(?:go )?back(?: a page)?", "back"), (r"(?:go )?forward", "forward"),
    (r"zoom in", "zoom_in"), (r"zoom out", "zoom_out"), (r"reset (?:the )?zoom", "zoom_reset"),
    (r"(?:go |make it |enter |exit |leave )?full ?screen(?: mode)?", "fullscreen"),
    (r"(?:switch|change) (?:windows?|apps?)|alt tab|next window", "switch_window"), (r"task view|show (?:all )?(?:my )?windows", "task_view"),
    (r"(?:open |start )?(?:a )?new window", "new_window"), (r"reopen (?:the )?(?:last |closed )?tab", "reopen_tab"),
    (r"(?:go to |switch to )?(?:the )?next tab", "next_tab"), (r"(?:go to |switch to )?(?:the )?previous tab", "previous_tab"),
    (r"bookmark (?:this|it|this page)", "bookmark"), (r"(?:open |show )?(?:my )?(?:browser )?history", "history"),
    (r"(?:go to |scroll to )?(?:the )?top(?: of the page)?", "top"), (r"(?:go to |scroll to )?(?:the )?bottom(?: of the page)?", "bottom"),
    (r"page down", "page_down"), (r"page up", "page_up"), (r"(?:open )?(?:the )?clipboard history", "clipboard_history"),
    (r"(?:open )?(?:the )?emoji(?: panel| picker)?", "emoji"), (r"(?:open )?task manager", "task_manager"),
    (r"(?:open )?(?:the )?notifications?(?: panel| center)?", "notifications"), (r"(?:open )?quick settings", "quick_settings"),
    (r"snip|(?:take a )?snip(?:ping)?(?: tool)?|screen snip", "snip"), (r"rename (?:it|this|that)", "rename"),
]
_KEY_RE = [(re.compile(rf"^{p}$"), a) for p, a in _KEY_PHRASES]


@rule(r"^(?:press|hit|push|type) (?:the )?(?P<k>enter|return|escape|esc|space|spacebar|tab|backspace|delete)(?: key| button)?(?: (?P<n>\d+|two|three) times?| (?P<tw>twice))?$")
def _press_key(m):
    k = {"return": "enter", "esc": "escape", "spacebar": "space"}.get(m["k"], m["k"])
    n = 2 if m["tw"] else {"two": 2, "three": 3}.get(m["n"], int(m["n"]) if m["n"] else 1)
    return "press_keys", {"action": k, "times": n}


@rule(r"^(?P<phrase>.+)$")
def _named_key_action(m):
    for rx, action in _KEY_RE:
        if rx.match(m["phrase"]):
            return "press_keys", {"action": action}
    return None


@rule(r"^scroll (?P<d>up|down)(?: (?P<amt>a lot|a bit|a little|more|all the way))?$|^scroll$")
def _scroll(m):
    amt = {"a lot": 15, "a bit": 3, "a little": 3, "more": 8, "all the way": 30}.get(m["amt"], 6)
    return "scroll", {"direction": m["d"] or "down", "amount": amt}


@rule(r"^(?:open |show |go to )?(?:the )?(?P<p>bluetooth|wi-?fi|network|display|sound|audio|battery|power|apps|windows update|update|privacy|notification|personalization|background|wallpaper|themes?|mouse|touchpad|keyboard|storage|about|date and time|time|language|microphone|camera|vpn|hotspot|night light|default apps|printers?|accounts?|focus|gaming) settings$")
def _settings(m):
    p = {"audio": "sound", "wallpaper": "background", "touchpad": "mouse", "notification": "notifications",
         "date and time": "time", "theme": "themes", "printer": "printers", "account": "accounts"}.get(m["p"], m["p"])
    return "open_settings", {"page": p}


@rule(r"^(?:shut ?down|turn off|power off|switch off) (?:the |my )?(?:pc|computer|laptop|system)$|^shut ?down$")
def _shutdown(m):
    return "power", {"action": "shutdown"}


@rule(r"^(?:restart|reboot) (?:the |my )?(?:pc|computer|laptop|system)$|^reboot$")
def _restart(m):
    return "power", {"action": "restart"}


@rule(r"^(?:put )?(?:the |my )?(?:pc|computer|laptop) (?:to |in |into )?sleep$|^(?:put (?:the |my )?(?:pc|computer|laptop) to sleep)$")
def _pc_sleep(m):
    return "power", {"action": "sleep"}


@rule(r"^(?:sign|log) (?:me )?out$")
def _signout(m):
    return "power", {"action": "sign_out"}


@rule(r"^(?:click|tap|select|choose)(?: on)? (?:the )?(?P<n>.+?)(?: button| link| tab| option| icon| menu)?$|^press (?:the )?(?P<n2>.+?) button$")
def _click(m):
    return "click_element", {"name": m["n"] or m["n2"]}


# --- screen / vision ---------------------------------------------------------------
@rule(r"^(?:what(?:'s| is) (?:on )?(?:my|the|this) screen|what am i (?:looking at|seeing)|look at (?:my|the) screen|describe (?:my|the) screen|what do you see)$")
def _look(m):
    return "look_at_screen", {"question": "What's on my screen? Describe it briefly."}


@rule(r"^(?:explain|what(?:'s| is)|what does|can you explain|help me with|fix|solve)\s+(?:this|that|the)\s+(?P<thing>error|code|page|question|problem|message|chart|graph|image|screen|mean|say)(?: mean| say)?$")
def _explain_this(m):
    return "look_at_screen", {"question": f"Explain the {m['thing']} on my screen and how to handle it."}


@rule(r"^(?:read|read out|read me) (?:this|the screen|my screen|what's on (?:my|the) screen|this page)$")
def _read(m):
    return "read_screen", {}


@rule(r"^(?:summari[sz]e|explain|translate|read) (?:my |the )?(?P<src>clipboard|selection|selected text|this text|what i copied)$")
def _clip(m):
    verb = m.string.split()[0]
    action = "summarize" if verb.startswith("summar") else verb
    source = "clipboard" if m["src"] in ("clipboard", "what i copied") else "selection"
    return "clipboard", {"action": action, "source": source}


# --- system -------------------------------------------------------------------------
@rule(r"^(?:how(?:'s| is) (?:my |the )?(?:system|pc|computer|laptop)(?: doing)?|system status|status report|pc status|diagnostics)$")
def _status(m):
    return "system_status", {"what": "all"}


@rule(r"^(?:how much )?(?:battery|charge)(?: left| level| status| percentage)?$|^(?:what(?:'s| is) (?:my |the )?battery(?: level| percentage| status)?|how much battery (?:do i have|is left))$")
def _battery(m):
    return "system_status", {"what": "battery"}


@rule(r"^(?:what(?:'s| is) using (?:all )?(?:my |the )?(?:memory|ram)|what(?:'s| is) slowing (?:down )?(?:my |the )?(?:pc|computer|laptop))$")
def _top(m):
    return "system_status", {"what": "top_apps"}


@rule(r"^(?:set |turn |put )?(?:the )?volume (?:to |at )?(?P<n>\d{1,3})(?: percent|%)?$")
def _vol_set(m):
    return "set_volume", {"level": int(m["n"])}


@rule(r"^(?:set |turn |put )?(?:the )?(?:screen )?brightness (?:to |at )?(?P<n>\d{1,3})(?: percent|%)?$")
def _bright_set(m):
    return "set_brightness", {"level": int(m["n"])}


@rule(r"^(?:increase|raise|turn up) (?:the )?(?:screen )?brightness$|^brightness up$|^brighter$")
def _bright_up(m):
    return "set_brightness", {"change": 20}


@rule(r"^(?:decrease|lower|reduce|turn down|dim) (?:the )?(?:screen )?(?:brightness)?$|^brightness down$|^dimmer$|^dim the screen$")
def _bright_down(m):
    return "set_brightness", {"change": -20}


@rule(r"^(?:take a |grab a )?screen ?shot$|^capture (?:my |the )?screen$")
def _screenshot(m):
    return "screenshot", {}


@rule(r"^lock (?:my |the )?(?:pc|computer|laptop|screen)$")
def _lock(m):
    return "lock_pc", {}


@rule(r"^(?:put |place |arrange |show )?(?:these |my |the )?(?:two )?windows side by side$|^split (?:the )?screen$|^tile (?:my )?windows$")
def _arrange(m):
    return "arrange_windows", {}


# --- time keeping ---------------------------------------------------------------------
@rule(r"^(?:set |start )?(?:a |the )?timer (?:for )?(?P<d>.+)$|^(?:set |start )?(?:a )?(?P<d2>.+?) timer$")
def _timer(m):
    return "set_timer", {"duration": m["d"] or m["d2"]}


@rule(r"^remind me (?:to |about )?(?P<text>.+?) (?P<when>in .+|at .+|after .+)$")
def _remind1(m):
    return "set_reminder", {"text": m["text"], "when": m["when"]}


@rule(r"^(?:in|after) (?P<when>.+?),? remind me (?:to |about )?(?P<text>.+)$")
def _remind2(m):
    return "set_reminder", {"text": m["text"], "when": "in " + m["when"]}


@rule(r"^(?:set an alarm|wake me up|alarm) (?:for |at )?(?P<when>.+)$")
def _alarm(m):
    return "set_reminder", {"text": "Alarm", "when": ("at " + m["when"]) if not m["when"].startswith("in ") else m["when"]}


@rule(r"^(?:what are my |any |list (?:my )?|show (?:my )?)(?:reminders|timers|alarms)\??$|^how much time (?:is )?left(?: on the timer)?$")
def _list_rem(m):
    return "list_reminders", {}


@rule(r"^(?:cancel|stop|delete|clear) (?:the |my |all )?(?:all )?(?P<kind>timers?|reminders?|alarms?)$")
def _cancel(m):
    k = m["kind"].rstrip("s")
    return "cancel_reminders", {"kind": "timer" if k == "timer" else "reminder" if k in ("reminder", "alarm") else "all"}


# --- notes / todos / memory ---------------------------------------------------------------
@rule(r"^(?:take a note|make a note|note down|note that|write down)(?: that)?[:,]?\s+(?P<t>.+)$")
def _note(m):
    return "take_note", {"text": m["t"]}


@rule(r"^(?:read|show|what are) (?:me )?my notes$")
def _notes(m):
    return "read_notes", {}


@rule(r"^add (?P<t>.+?) to (?:my |the )?(?:to ?do|todo|shopping|grocery)? ?list$")
def _todo(m):
    return "add_todo", {"item": m["t"]}


@rule(r"^(?:what(?:'s| is) on my (?:to ?do |todo |shopping )?list|read (?:me )?my (?:to ?do |todo |shopping )?list|my to ?dos?)$")
def _todos(m):
    return "list_todos", {}


@rule(r"^(?:mark|check off|cross off|tick off) (?P<t>.+?)(?: as done| off| from my list)?$|^i (?:finished|did|bought) (?P<t2>.+)$")
def _done(m):
    return "complete_todo", {"item": m["t"] or m["t2"]}


@rule(r"^remember (?:that )?(?P<f>.+)$")
def _remember(m):
    return "remember", {"fact": m["f"]}


@rule(r"^forget (?:that )?(?P<f>.+)$")
def _forget(m):
    return "forget", {"fact": m["f"]}


# --- info ------------------------------------------------------------------------------
@rule(r"^(?:what(?:'s| is) the |how(?:'s| is) the )?weather(?: like)?(?: today| now| tomorrow)?(?: in (?P<city>.+?))?(?: today| now| tomorrow)?$|^(?:is it|will it) (?:going to )?(?:rain|be hot|be cold)(?: today| tomorrow)?(?: in (?P<city2>.+))?$")
def _weather(m):
    return "weather", {"city": m["city"] or m["city2"]}


@rule(r"^(?:good morning|morning briefing|daily briefing|brief me|what did i miss|give me a briefing)$")
def _briefing(m):
    return "briefing", {}


@rule(r"^(?:calculate|compute|what(?:'s| is)|how much is)\s+(?P<e>[\d(][\d\s.,+\-*/()%x^]*(?:\s*(?:plus|minus|times|multiplied by|divided by|over|into|percent of|squared|cubed|to the power of)\s*[\d.,()]+)*[\d)%]?(?: squared| cubed)?)$|^(?:what(?:'s| is) )?(?:the )?square root of (?P<e2>[\d.]+)$|^(?:what(?:'s| is) )?(?P<e3>[\d.]+ ?(?:percent|%) of [\d.,]+)$")
def _calc(m):
    e = m["e"] or (f"square root of {m['e2']}" if m["e2"] else m["e3"])
    return "calculate", {"expression": e}


# --- files ---------------------------------------------------------------------------------
@rule(r"^(?:open|show) (?:my |the )?(?P<f>downloads?|documents|desktop|pictures|photos|music|videos|screenshots)(?: folder)?$")
def _folder(m):
    return "open_folder", {"name": m["f"]}


@rule(r"^(?:open|show) (?:my |the )?(?:latest|last|newest|most recent) download$")
def _latest(m):
    return "find_file", {"name": "latest download"}


@rule(r"^(?:find|search for|locate|open) (?:the |my )?file (?:called |named )?(?P<n>.+)$")
def _find(m):
    return "find_file", {"name": m["n"]}


@rule(r"^type (?:out )?(?P<t>.+)$")
def _type(m):
    return "type_text", {"text": m["t"]}


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
    t = re.sub(r"^(?:a|an|the|my|up)\s+", "", m["target"]).strip()
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


def _route_one(t):
    for pattern, fn in RULES:
        m = pattern.match(t)
        if m:
            hit = fn(m)
            if hit is None:
                continue
            args = hit[1]
            # An "app name" that is really half a sentence means the rule misfired.
            for key in ("app", "name"):
                if key in args and len(str(args[key]).split()) > 4:
                    hit = None
            if hit:
                return hit
    return None


_CITY = re.compile(r"\b(?:in|at|for) ([a-z][a-z .]{2,30}?)(?: today| now| tomorrow|$)")


def keyword_route(t: str):
    """Last resort for short utterances the exact rules missed: one unmistakable keyword decides."""
    if len(t.split()) > 12:
        return None
    if "standby" in t:
        return "standby", {}
    if "screenshot" in t:
        return "screenshot", {}
    if re.search(r"\b(?:on|at|see|look at|read) (?:my|the|this) screen\b", t):
        return "look_at_screen", {"question": "What's on my screen? Describe it briefly."}
    if "weather" in t or re.search(r"\b(?:rain|temperature)\b.*\b(?:today|tomorrow|outside)\b", t):
        m = _CITY.search(t)
        return "weather", {"city": m.group(1).strip() if m and m.group(1).strip() not in ("the", "here", "my area") else None}
    if re.search(r"\bbattery\b", t):
        return "system_status", {"what": "battery"}
    m = re.search(r"\btimer\b", t)
    if m:
        from jarvis.skills.reminders import parse_duration
        if parse_duration(t):
            return "set_timer", {"duration": t}
    if re.search(r"\bvolume (?:up|higher|louder)\b|\blouder\b", t):
        return "media", {"action": "volume_up"}
    if re.search(r"\bvolume (?:down|lower)\b|\bquieter\b", t):
        return "media", {"action": "volume_down"}
    return None


def route_all(text: str):
    """Every distinct routing of the utterance's phrasings, best first (the agent tries them in order)."""
    from jarvis.brain.normalize import candidates
    cands = candidates(text)
    hits = []
    for cand in cands:
        t = clean(cand)
        hit = _route_one(t) if t else None
        if hit and hit not in hits:
            hits.append(hit)
    if not hits:
        hit = keyword_route(clean(cands[-1]))
        if hit:
            hits.append(hit)
    return hits


def route(text: str):
    hits = route_all(text)
    return hits[0] if hits else None
