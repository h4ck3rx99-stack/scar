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


@rule(r"^(?:go |get |switch |start |enter |activate |turn on |begin |put yourself )?(?:on |to |into |in )?(?:the )?stand ?-?by(?: mode)?$"
      r"|^(?:go )?offline$|^disappear$|^leave me alone$|^take a break$|^(?:do not|don't) disturb(?: me)?$|^do not disturb mode$")
def _standby(m):
    return "standby", {}


@rule(r"^(?:go |get |switch )?(?:on |to |into )?(?:the )?(?:stand ?-?by|do not disturb|quiet mode)(?: mode)? for (?P<d>.+)$"
      r"|^(?:do not|don't) disturb(?: me)? for (?P<d2>.+)$|^(?:leave me alone|be quiet|hide|disappear|go away|shut up) for (?P<d3>.+)$")
def _standby_for(m):
    from jarvis.skills.reminders import parse_duration
    secs = parse_duration(m["d"] or m["d2"] or m["d3"])
    return ("standby", {"minutes": round(secs / 60, 1)}) if secs else None


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


# --- about Scar: help, repeat, self check -------------------------------------------
@rule(r"^(?:what (?:all )?can you do|tell me what you can do|what are your (?:features|abilities|skills|commands|powers)|help(?: me)?"
      r"|what do you do|how can you help(?: me)?|what are you capable of|show me what you can do|what commands (?:do you know|are there))$")
def _help(m):
    return "capabilities", {}


@rule(r"^(?:repeat that|say that again|what did you (?:just )?say|come again|pardon(?: me)?|repeat|sorry what|can you repeat(?: that)?)$")
def _repeat_said(m):
    return "repeat", {"what": "said"}


@rule(r"^(?:do (?:that|it) again|again|one more time|repeat (?:the |that )?last (?:command|action|thing)|same again)$")
def _repeat_action(m):
    return "repeat", {"what": "action"}


@rule(r"^(?:run (?:a )?(?:self[- ]?check|self[- ]?test|diagnostics? on yourself|health check)|check yourself|self[- ]?(?:check|test|diagnosis)"
      r"|are you (?:ok|okay|working (?:properly|fine|ok)?|alright|feeling ok)|how are your systems|diagnose yourself)$")
def _self_check(m):
    return "self_check", {}


@rule(r"^(?:what went wrong|why didn't (?:that|it|you) work|why did (?:that|it) fail|what happened(?: there)?|what was the (?:error|problem)"
      r"|why couldn't you (?:do )?(?:that|it))$")
def _what_wrong(m):
    return "what_went_wrong", {}


# --- Scar's own settings --------------------------------------------------------------
def _setting_names():
    from jarvis.core.settings import SPEC
    names = {k.replace("_", " ") for k in SPEC} | {n for s in SPEC.values() for n in s["names"]}
    names.discard("speed")               # "speed up" is about talking speed
    return "|".join(sorted(map(re.escape, names), key=len, reverse=True))


_SET_RE = _setting_names()


@rule(rf"^(?:set|change|make|put|turn|adjust|switch) (?:your |the |my )?(?P<k>{_SET_RE})(?: setting)? (?:to|at|as|into) (?P<v>.+)$")
def _set_to(m):
    return "change_setting", {"setting": m["k"], "value": m["v"]}


@rule(rf"^(?:turn|switch|set|put) (?P<v>on|off) (?:your |the |my )?(?P<k>{_SET_RE})$|^(?:turn|switch|set|put) (?:your |the |my )?(?P<k2>{_SET_RE}) (?P<v2>on|off)$"
      rf"|^(?P<v3>enable|disable) (?:your |the |my )?(?P<k3>{_SET_RE})$")
def _set_onoff(m):
    return "change_setting", {"setting": m["k"] or m["k2"] or m["k3"], "value": m["v"] or m["v2"] or m["v3"]}


@rule(rf"^(?:make |set )?(?:your |the )?(?P<k>{_SET_RE}) (?P<v>higher|lower|longer|shorter|faster|slower|bigger|smaller|louder|quieter|up|down|\d+(?:\.\d+)?(?: ?\w+)?)$"
      rf"|^(?:increase|raise) (?:your |the )?(?P<k2>{_SET_RE})$|^(?:decrease|reduce|lower) (?:your |the )?(?P<k3>{_SET_RE})$")
def _set_rel(m):
    if m["k"]:
        return "change_setting", {"setting": m["k"], "value": m["v"]}
    return "change_setting", {"setting": m["k2"] or m["k3"], "value": "up" if m["k2"] else "down"}


@rule(r"^(?:make yourself|get|grow|be|become|go) (?:a (?:bit|little|lot) )?(?P<d>bigger|larger|smaller|tinier|huge|tiny|normal size)$|^(?P<d2>shrink|grow)(?: yourself)?$|^(?:reset|normal) (?:your )?size$")
def _size(m):
    d = m["d"] or m["d2"] or "normal"
    v = {"larger": "bigger", "huge": "2", "grow": "bigger", "tinier": "smaller", "tiny": "0.6", "shrink": "smaller",
         "normal size": "default", "normal": "default"}.get(d, d)
    return "change_setting", {"setting": "size", "value": v}


@rule(r"^(?:speak|talk) (?P<d>louder|softer|quieter|more quietly|more loudly|at normal volume)$|^(?:raise|lower) your voice$")
def _voice_vol(m):
    d = m["d"] or ("louder" if m.string.startswith("raise") else "quieter")
    return "change_setting", {"setting": "voice_volume",
                              "value": "default" if "normal" in d else "up" if "loud" in d else "down"}


@rule(r"^(?:listen|wait) (?P<d>longer|shorter|less|more|a bit longer|a little longer)(?: (?:after|before) .+| for me)?$|^give me (?:more|a bit more) time(?: to .+)?$")
def _follow_len(m):
    d = m["d"] or "longer"
    return "change_setting", {"setting": "follow_up_seconds", "value": "up" if ("long" in d or "more" in d) else "down"}


@rule(r"^(?:be|act|talk|become|sound|answer) (?:a (?:bit|little) |more |a bit more |a little more |very )?(?P<p>funny|funnier|serious|formal|professional|brief|concise|detailed|sarcastic|calm|normal|polite|chill|relaxed)(?: from now on| please)?$"
      r"|^(?:keep|make) (?:your )?(?:answers|replies|responses) (?P<p2>short|shorter|brief|longer|detailed)$|^give (?:me )?(?P<p3>detailed|longer|shorter|short) answers$"
      r"|^(?:stop being|don't be) (?:so )?(?:funny|sarcastic|formal|serious|brief)$|^(?:switch to |use )?(?P<p4>funny|formal|sarcastic|calm|brief|detailed|normal) (?:mode|personality)$")
def _personality(m):
    p = m["p"] or m["p2"] or m["p3"] or m["p4"] or "normal"
    return "change_setting", {"setting": "personality", "value": p}


@rule(r"^(?:stop|no more|quit|cut out) (?:the )?(?:pranks|pranking(?: me)?|being playful|messing around|mischief|tricks)$|^(?:no|don't) prank(?:s| me)?$")
def _pranks_off(m):
    return "change_setting", {"setting": "pranks", "value": "off"}


@rule(r"^(?:you can |start |go ahead and )?(?:prank(?: me)?|be playful|pranks on|do (?:your )?pranks|play pranks)$")
def _pranks_on(m):
    return "change_setting", {"setting": "pranks", "value": "on"}


@rule(r"^(?:stop|no more|quit) (?:fidgeting|moving around randomly|the random moves|random moves|idle (?:moves|animations)|dancing)$")
def _idle_off(m):
    return "change_setting", {"setting": "idle_moves", "value": "off"}


@rule(r"^(?:mute|silence) (?:your )?(?:sounds|beeps|sound effects|effects)$")
def _sounds_off(m):
    return "change_setting", {"setting": "sounds", "value": "off"}


@rule(r"^(?:what are|show|list|tell me|read|say|open|show me) (?:me )?(?:your|all your|all of your|scar's) settings(?: panel)?$|^your settings$|^settings panel$")
def _list_settings(m):
    return "list_settings", {}


@rule(r"^reset (?:your |all (?:of )?(?:your )?)?settings(?: to default)?$|^(?:go back to|restore|use) (?:your )?default settings$|^factory reset$")
def _reset_settings(m):
    return "reset_settings", {}


@rule(r"^(?:change|set) your (?:name|wake word) (?:to )?(?P<n>.+)$|^(?:can i )?call you (?P<n2>.+)$")
def _rename(m):
    return "change_setting", {"setting": "name", "value": m["n"] or m["n2"]}


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


@rule(r"^(?:play|click(?: on)?|open|start|watch) (?:the )?(?:first|top|1st) (?:video|result|one|song|link)(?: on youtube)?$|^play (?:the )?(?:top|first) (?:search )?result$")
def _yt_first(m):
    return "play_youtube", {}


@rule(r"^(?:search|find|look up|look for)(?: for)? (?P<q>.+?) (?:on|in) spotify$|^(?:search )?spotify for (?P<q2>.+)$|^open (?P<q3>.+?) on spotify$")
def _spotify_search(m):
    return "spotify", {"action": "search", "query": m["q"] or m["q2"] or m["q3"]}


@rule(r"^(?:play|put on) (?P<q>.+?) (?:on|in|using) spotify$")
def _spotify_play(m):
    return "spotify", {"action": "play", "query": m["q"]}


@rule(r"^(?:search|look up|find|look for|type)(?: for)? (?P<q>.+?) (?:here|in here|over here|in (?:this|the) (?:app|search ?bar|search box|page|site|website|window|search field)"
      r"|(?:on|into|using) (?:this|the) (?:search ?bar|search box|search field|page|site|website|app)|in the search)$")
def _search_here(m):
    return "search_here", {"query": m["q"]}


@rule(r"^(?:play|put on|i (?:want|would like|wanna|'d like) to (?:watch|listen to|hear)|can i (?:watch|listen to)) (?P<q>.+?)(?: on youtube| video| song| music video)?$")
def _play_anything(m):
    q = m["q"].strip()
    if q in ("it", "that", "this", "again", "something", "music", "some music", "a song", "anything", "the music", "the video", "my music"):
        return None
    return "play_youtube", {"query": q}


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


# --- where Scar is: parking and placement -------------------------------------------
_SPOTS = {"top left": "top_left", "top right": "top_right", "bottom left": "bottom_left", "bottom right": "bottom_right",
          "upper left": "top_left", "upper right": "top_right", "lower left": "bottom_left", "lower right": "bottom_right",
          "left": "left", "right": "right", "top": "top", "bottom": "bottom", "center": "center", "centre": "center",
          "middle": "center"}
_SPOT_RE = "|".join(sorted(map(re.escape, _SPOTS), key=len, reverse=True))


@rule(r"^(?:stay|stay (?:here|there|put|right there|right here)|don't move|do not move|park (?:here|there|yourself)|stop (?:roaming|moving|wandering)|sit still)$")
def _stay(m):
    return "place", {"spot": "here"}


@rule(r"^(?:come here|come to me|come closer|come over here)$")
def _come(m):
    return "place", {"spot": "cursor"}


@rule(r"^(?:roam|roam around|go roam|start roaming|you can (?:roam|move|move around|go)|feel free to (?:roam|move)|move around|wander|go wander|free roam)$")
def _roam(m):
    return "place", {"spot": "roam"}


@rule(rf"^(?:go|move|fly|sit|stay|hover|park)(?: yourself| over)? (?:to |in |at |on |over )?(?:the )?(?P<s>{_SPOT_RE})(?: corner| side| of the screen| edge)?$")
def _go_spot(m):
    return "place", {"spot": _SPOTS[m["s"]]}


# --- extras: pomodoro, stopwatch, chance, conversion, news, dictionary, internet ------
@rule(r"^(?:start |begin )?(?:a )?pomodoro(?: timer| session| mode)?$")
def _pomo(m):
    return "pomodoro", {"action": "start"}


@rule(r"^(?:stop|cancel|end) (?:the )?pomodoro$")
def _pomo_stop(m):
    return "pomodoro", {"action": "stop"}


@rule(r"^(?P<a>start|stop|lap|check|reset|pause) (?:the |my )?stopwatch$|^(?:how long has it been|stopwatch(?: time| status)?)$"
      r"|^how (?:long|much time) (?:on|is on|has|is) (?:the |my )?stopwatch(?: been)?(?: running| going| at)?$"
      r"|^what(?:'s| is) (?:the |my )?stopwatch(?: at| time| saying)?$|^lap$")
def _stopwatch(m):
    a = {"reset": "start", "pause": "stop"}.get(m["a"], m["a"])
    return "stopwatch", {"action": a or ("lap" if m.string == "lap" else "check")}


@rule(r"^(?:flip|toss) a coin$|^heads or tails$")
def _coin(m):
    return "chance", {"kind": "coin"}


@rule(r"^roll (?:a |the )?(?:dice|die)$")
def _dice(m):
    return "chance", {"kind": "dice"}


@rule(r"^(?:pick|give me|choose) a (?:random )?number(?: between (?P<a>\d+) and (?P<b>\d+))?$")
def _rand(m):
    return "chance", {"kind": "number", "low": int(m["a"] or 1), "high": int(m["b"] or 100)}


@rule(r"^(?:convert |what(?:'s| is) |how much is |how many )?(?P<n>\d+(?:\.\d+)?) ?(?P<f>[a-z/°]+(?: [a-z]+)?) (?:in|to|into) (?P<t>[a-z/°]+(?: [a-z]+)?)$")
def _convert(m):
    return "convert", {"amount": float(m["n"]), "from_unit": m["f"].replace("°", ""), "to_unit": m["t"].replace("°", "")}


@rule(r"^(?:what(?:'s| is) the |tell me the |read me the |give me the |any )?(?:latest |top |today's )?(?:news|headlines)(?: today)?(?: (?:about|on) (?P<t>.+))?$")
def _news(m):
    return "news", {"topic": m["t"]}


@rule(r"^(?:define|what does (?P<w2>[a-z\-]+) mean|meaning of|what is the meaning of) ?(?P<w>[a-z\-]+)?$")
def _define(m):
    w = m["w2"] or m["w"]
    return ("define", {"word": w}) if w else None


@rule(r"^(?:am i (?:online|connected)|is (?:the |my )?internet (?:working|on)|check (?:the |my )?internet|what(?:'s| is) my ip(?: address)?)$")
def _net(m):
    return "internet_check", {}


# --- how Scar listens: always-on, mic, voice ID, dictation, suggestions, phone --------
@rule(r"^(?:always listen|listen all the time|always be listening|you don't need your name|no need to say your name|stay listening)$")
def _always(m):
    return "listen_always", {}


@rule(r"^(?:only (?:listen|respond|reply) when (?:i )?(?:call|say) (?:you|your name)|wait for your name|only when i call you)$")
def _on_call(m):
    return "listen_on_call", {}


@rule(r"^(?:mute|turn off|switch off|disable|stop) (?:the |your )?(?:mic|microphone)$|^(?:mic|microphone) off$|^stop listening(?: to me)?$")
def _mic_off(m):
    return "mic_off", {}


@rule(r"^(?:unmute|turn on|switch on|enable) (?:the |your )?(?:mic|microphone)$|^(?:mic|microphone) on$")
def _mic_on(m):
    return "mic_on", {}


# --- how Scar listens: voice ID, dictation, suggestions, phone -----------------------
@rule(r"^(?:learn|remember|recognize|register|enroll) my voice$|^(?:set up|setup) (?:voice (?:id|recognition)|my voice)$")
def _learn_voice(m):
    return "learn_voice", {}


@rule(r"^forget my voice$|^(?:delete|reset) my voice ?(?:print|profile)?$")
def _forget_voice(m):
    return "forget_voice", {}


@rule(r"^(?:only (?:listen|respond|reply) to me|ignore (?:other people|everyone else|others))$")
def _only_me(m):
    return "only_me", {}


@rule(r"^(?:listen to|respond to|reply to) (?:everyone|everybody|anyone|others)$")
def _everyone(m):
    return "everyone", {}


@rule(r"^(?:start |begin |turn on )?dictation(?: mode)?$|^(?:take dictation|type (?:what|everything) i say)$")
def _dictation(m):
    return "dictation", {}


@rule(r"^(?:stop|no more|turn off|disable) (?:the )?(?:suggestions|tips|break reminders|proactive (?:mode|help))$")
def _sugg_off(m):
    return "suggestions_off", {}


@rule(r"^(?:turn on|enable|start) (?:the )?(?:suggestions|tips|break reminders|proactive (?:mode|help))$")
def _sugg_on(m):
    return "suggestions_on", {}


@rule(r"^(?:enable|turn on|start|set up) (?:the )?phone (?:control|remote|access)$|^connect (?:my )?phone$")
def _phone_on(m):
    return "phone_on", {}


@rule(r"^(?:disable|turn off|stop) (?:the )?phone (?:control|remote|access)$|^disconnect (?:my )?phone$")
def _phone_off(m):
    return "phone_off", {}


# --- research, writing, media info, voice -----------------------------------------
@rule(r"^(?:research|look up online|search (?:the )?(?:web|internet|online) (?:for|about)|find (?:out|online) (?:about )?|what(?:'s| is) the latest (?:news )?(?:on|about)) (?P<q>.+)$")
def _research(m):
    return "web_research", {"query": m["q"]}


@rule(r"^(?:write|draft|compose|type out) (?:an? |the )?(?P<what>(?:email|mail|message|reply|letter|paragraph|essay|note|tweet|post|poem|summary|code|function|leave application|application).*)$")
def _write(m):
    return "write_text", {"instructions": "Write " + m["what"]}


@rule(r"^(?:what(?:'s| is) (?:this |the )?(?:song|track|music|video)(?: (?:playing|called|name))?|what(?:'s| is) playing(?: now| right now)?|which song is (?:this|playing)|name (?:of )?(?:this|the) song)$")
def _now_playing(m):
    return "now_playing", {}


@rule(r"^(?:change|switch|set|use) (?:your |the )?voice (?:to )?(?:an? )?(?P<v>.+?)(?: voice)?$|^(?:use|talk (?:in|with)) (?:an? )?(?P<v2>british|female|male|deep|butler|posh|american) (?:voice|accent)$")
def _voice(m):
    return "voice_style", {"voice": m["v"] or m["v2"]}


@rule(r"^(?:talk|speak) (?P<s>faster|slower|normally|at normal speed)$|^(?:speed up|slow down)$")
def _speed(m):
    s = m["s"] or ("faster" if m.string.startswith("speed") else "slower")
    return "voice_style", {"speed": "normal" if s.startswith(("normal", "at")) else s}


# --- memory ------------------------------------------------------------------------
@rule(r"^what (?:did|have) i (?:do|work on|been doing|been working on|spend (?:my )?time on)(?P<when> today| yesterday| this morning| this afternoon| last week| this week| on \w+day)?$|^how (?:long|much time) (?:did i spend|have i spent) (?:on (?:my )?)?(?:the )?(?:pc|computer|laptop|screen)(?P<when2> today| yesterday)?$")
def _activity(m):
    return "my_activity", {"when": ((m["when"] or m["when2"]) or "today").strip()}


@rule(r"^(?:do you remember|remind me what|what did (?:we|you|i) (?:say|talk|discuss)|what was (?:that|the)) (?P<q>.+?)(?P<when> today| yesterday| this morning| last week| on \w+day)?$")
def _recall(m):
    return "recall", {"query": m["q"], "when": (m["when"] or "").strip() or None}


# --- your files --------------------------------------------------------------------
@rule(r"^(?:search|look in|check) (?:my |in my )?(?:files|documents|notes|pdfs?|slides) (?:for|about) (?P<q>.+)$|^find (?:my )?(?:notes|documents|files) (?:on|about) (?P<q2>.+)$")
def _search_files(m):
    return "search_my_files", {"query": m["q"] or m["q2"]}


@rule(r"^(?:summari[sz]e|read|explain) (?:my |the )?(?:file |document |pdf )?(?P<f>[\w .\-]+?\.(?:pdf|docx|pptx|txt|md))$")
def _summarize_file(m):
    return "ask_file", {"file": m["f"], "question": "Summarize this file in a few spoken sentences."}


@rule(r"^what does (?:my |the )?(?P<f>[\w .\-]+?) (?:say|mention) about (?P<q>.+)$")
def _ask_file(m):
    return "ask_file", {"file": m["f"], "question": f"What does it say about {m['q']}?"}


# --- routines & schedule -----------------------------------------------------------
@rule(r"^(?:create|make|add|save|set up) (?:a |new )?routine (?:called |named )?(?P<n>[\w ]+?)\s*(?::|that will|that|to|which) (?P<s>.+)$")
def _create_routine(m):
    return "create_routine", {"name": m["n"], "steps": m["s"]}


@rule(r"^(?:what are my|list (?:my )?|show (?:my )?)routines$")
def _list_routines(m):
    return "list_routines", {}


@rule(r"^(?:run|start|begin|activate|do|enable|switch to|go into|enter) (?:the |my )?(?P<n>[\w ]+?)(?: routine| mode)?$|^(?P<n2>study mode|focus mode|break time|good night)$")
def _run_routine(m):
    from jarvis.skills import routines
    name = m["n"] or m["n2"]
    return ("run_routine", {"name": name}) if routines.find(name) else None


@rule(r"^(?:add|put|schedule|set) (?P<t>.+?) (?:to my schedule |in my schedule |on my calendar )?(?P<w>(?:every|each|on|at|tomorrow|today|this|next) .+)$")
def _add_event(m):
    t = re.sub(r"\b(?:to my (?:schedule|calendar)|class)\b", lambda x: "class" if x.group(0) == "class" else "", m["t"]).strip()
    if not re.search(r"\d|noon|midnight", m["w"]):
        return None
    if re.search(r"\b(?:alarm|timer|reminder|volume|brightness|window|chrome|opera|notepad)\b", m["t"]):
        return None      # "set an alarm at 7", "set volume at 40": other intents
    return "add_event", {"title": t or m["t"], "when": m["w"]}


@rule(r"^(?:remove|delete|cancel) (?P<t>.+?) (?:from my (?:schedule|calendar|timetable))$")
def _remove_event(m):
    return "remove_event", {"title": m["t"]}


@rule(r"^(?:what(?:'s| is) (?:on )?my (?:schedule|timetable|calendar)|what (?:classes|lectures) do i have|what do i have)(?: for)?(?P<d> today| tomorrow| on \w+day| \w+day)?$")
def _schedule(m):
    return "my_schedule", {"day": (m["d"] or "today").strip()}


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
@rule(r"^(?:tell me |give me |show me |check |what(?:'s| is) )?(?:the )?(?P<city>[a-z][a-z .]{1,30}?)(?:'s|s)? weather(?: like)?(?: today| now| tomorrow| forecast)?$"
      r"|^(?:tell me |give me |show me |check |what(?:'s| is) )?(?:the )?weather (?:of|for|at) (?P<city2>[a-z][a-z .]{1,30})$")
def _city_weather(m):
    city = (m["city"] or m["city2"]).strip()
    if re.fullmatch(r"(?:the|today|todays|today's|current|local|my|our|here|how|what|what's|whats|tomorrow|tomorrows|how's the|what's the|what is the)", city):
        return "weather", {"city": None}
    return "weather", {"city": city}


@rule(r"^(?:tell me |give me |show me |check )(?:the )?weather(?: like)?(?: today| now| tomorrow)?(?: in (?P<city>.+?))?(?: today| now| tomorrow)?$")
def _weather_tell(m):
    return "weather", {"city": m["city"]}


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


_TYPE_APPS = (r"command prompt|cmd|the terminal|terminal|powershell|notepad|word|whatsapp|chrome|the browser|browser|vs code|"
              r"visual studio code|discord|telegram|the search bar|search bar")


@rule(rf"^type (?:out )?(?P<t>.+?) (?:in|on|into|inside|in the) (?:the |my )?(?P<app>{_TYPE_APPS})$")
def _type_in(m):
    app = re.sub(r"^the ", "", m["app"])
    if app == "search bar":
        return "search_here", {"query": m["t"]}
    return "type_text", {"text": m["t"], "app": "command prompt" if app == "cmd" else app}


@rule(r"^(?:clear|clean|cls) (?:the |my )?(?:command prompt|terminal|cmd|console|powershell)(?: screen| window)?$")
def _cls(m):
    return "type_text", {"text": "cls", "enter": True}


@rule(r"^(?:set |turn |put )?(?:the )?(?:screen )?brightness (?:to |at |on )?(?P<w>max|maximum|full|min|minimum|lowest|highest|zero|half|fifty percent)$"
      r"|^(?:set |put )?(?P<w2>max|maximum|full|min|minimum|lowest|highest|zero|half) brightness$")
def _bright_words(m):
    w = m["w"] or m["w2"]
    return "set_brightness", {"level": {"max": 100, "maximum": 100, "full": 100, "highest": 100, "half": 50,
                                        "fifty percent": 50}.get(w, 0)}


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


@rule(r"^(?:open|show|find) (?:the |my )?(?:document|file|pdf|doc|word file|presentation|ppt|slides|sheet|spreadsheet|excel file) (?:called |named |titled )?(?P<n>.+)$")
def _open_doc(m):
    return "find_file", {"name": m["n"]}


@rule(r"^(?:open|launch|start|run|go to|take me to) (?P<target>.+)$")
def _open(m):
    t = re.sub(r"^(?:a|an|the|my|up)\s+", "", m["target"]).strip()
    t = re.sub(r"\s+(?:in|on) (?:a |the )?(?:new|another|separate) (?:tab|window)$", "", t)
    if re.match(r"^[\w-]+(\.[\w-]+)+(/\S*)?$", t):  # something.com
        return "open_url", {"url": t}
    return "open", {"name": t}


# --- windows ----------------------------------------------------------------------
@rule(r"^(?:minimi[sz]e|hide) (?:all|everything|all (?:the )?windows|all apps)$|^show (?:me )?(?:the )?desktop$")
def _min_all(m):
    return "minimize_all", {}


@rule(rf"^(?P<act>minimi[sz]e|maximi[sz]e|restore|close|quit|exit|kill|focus|bring up|switch to|show me|show) {_WIN}$")
def _win(m):
    if m["app"] in ("me", "you", "us", "yourself", "him", "her", "them", "me that", "me it", "up", "off", "around"):
        return None
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
        return "look_at_screen", {"question": t if len(t.split()) >= 5 else "What's on my screen? Describe it briefly."}
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
