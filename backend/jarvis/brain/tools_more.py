"""More of Scar's tools: its own settings, YouTube / Spotify playback, typing into an app's search bar,
smarter opening (Store apps, files, websites by name), repeat, self-check and "what went wrong"."""
import asyncio
import logging
import re
import time
import urllib.parse

import httpx

from jarvis import config, runtime
from jarvis.core import settings

log = logging.getLogger("tools")
S = {"type": "string"}


def _fn(name, desc, props=None, required=None):
    return {"type": "function", "function": {"name": name, "description": desc, "parameters": {
        "type": "object", "properties": props or {}, **({"required": required} if required else {})}}}


MORE_SCHEMA = [
    _fn("change_setting", "Change one of Scar's own settings: " + "; ".join(
        f"{k} ({s['label']}: " + ("on/off" if s["kind"] == "bool" else "/".join(s["choices"]) if s["kind"] == "choice"
                                  else "a name" if s["kind"] == "text" else f"{s['min']:g}-{s['max']:g} {s.get('unit', '')}") + ")"
        for k, s in settings.SPEC.items()) + ". Relative values like 'bigger', 'faster', 'louder', 'up', 'down' work too.",
        {"setting": {"type": "string", "enum": list(settings.SPEC)}, "value": S}, ["setting", "value"]),
    _fn("list_settings", "Say Scar's current settings."),
    _fn("reset_settings", "Put all of Scar's settings back to default."),
    _fn("play_youtube", "Play a video/song on YouTube right away (opens the top result). Without a query, plays the first "
                        "result of the last YouTube search.", {"query": S}),
    _fn("spotify", "Search or play music in the Spotify app.",
        {"action": {"type": "string", "enum": ["search", "play"]}, "query": S}, ["action", "query"]),
    _fn("search_here", "Type a search into the search bar of the app or site that's open (Spotify, YouTube, Settings, a shop...).",
        {"query": S}, ["query"]),
    _fn("capabilities", "Tell the user what Scar can do."),
    _fn("chit_chat", "Quick friendly reply to a greeting or small talk.",
        {"kind": {"type": "string", "enum": ["greeting", "how_are_you", "who_are_you", "who_made_you", "good", "bad", "love"]}}, ["kind"]),
    _fn("self_check", "Run a health check on Scar itself: microphone, voice, AI model, internet, errors."),
    _fn("what_went_wrong", "Explain why the last command failed."),
]
MORE_DATA_TOOLS = {"what_went_wrong"}

CAPABILITIES = [
    "open apps, websites and files, and control windows: close, minimize, snap, resize",
    "play anything on YouTube or Spotify, search in any app's search bar, and click buttons by name",
    "look at your screen and explain errors, code or pages",
    "timers, alarms, reminders, your schedule, notes and to-do lists",
    "weather, news, web research, definitions, maths and unit or currency conversion",
    "volume, brightness, battery, system health, screenshots and lock or shut down",
    "remember things about you, search your documents, and recall what you worked on",
    "dictation, writing emails, routines like study mode, pomodoro and a stopwatch",
    "and change my own settings: my size, voice, speed, volume, personality, pranks and how long I listen",
]


def find_spotify_play(w):
    """Screen point of Spotify's big green round Play button on a track / album / artist page, or None.
    Found by colour and shape (a solid #1ED760 circle), so it works whatever the page says; the green
    "Reload page" pill and the white player-bar button don't match."""
    import numpy as np
    from scipy import ndimage
    from jarvis.hands.capture import window_image
    img = np.asarray(window_image(w).convert("RGB")).astype(np.int16)
    r, g, b = img[..., 0], img[..., 1], img[..., 2]
    mask = (g > 170) & (r < 90) & (b < 140) & (g - r > 110)
    labels, n = ndimage.label(mask)
    best = None
    for i, sl in enumerate(ndimage.find_objects(labels), start=1):
        h, wd = sl[0].stop - sl[0].start, sl[1].stop - sl[1].start
        if not (36 <= h <= 110 and abs(h - wd) <= max(4, h * 0.12)):
            continue                                  # not a circle of play-button size
        fill = float((labels[sl] == i).sum()) / (h * wd)
        if not 0.55 <= fill <= 0.9:                   # a disc (~0.79) with a dark triangle in it
            continue
        cy, cx = (sl[0].start + sl[0].stop) // 2, (sl[1].start + sl[1].stop) // 2
        if best is None or h > best[0]:
            best = (h, cx, cy)
    if best is None:
        return None
    f = w.frame
    return f.left + best[1], f.top + best[2]


class MoreTools:
    """Mixed into Tools (uses self.speak, self.ui, self.memory, self.call)."""

    last_yt_query = None
    failures = None

    def note_result(self, name, args, result):
        """Remember failures for "what went wrong?"."""
        if self.failures is None:
            self.failures = []
        if isinstance(result, str) and re.match(r"(?i)^(error|no |not found|unknown|bad |couldn't|could not|sorry|nothing)", result):
            self.failures.append({"at": time.time(), "tool": name, "args": args, "result": result})
            del self.failures[:-10]

    # ------------------------------------------------------------ settings
    async def push_frontend_settings(self):
        await self.ui.send("settings", values=settings.values())

    async def t_change_setting(self, setting, value, from_router=False):
        if setting in ("name", "wake word"):
            await self.speak(f"My name is also my wake word, so it's set when I start: put JARVIS_NAME={str(value).title()} "
                             "in backend dot env and restart me. Until then, I'm still Scar.")
            return "name is fixed until restart"
        key = settings.resolve_key(setting) if setting not in settings.SPEC else setting
        if key is None:
            await self.speak(f"I don't have a setting called {setting}. Say: what are your settings, to hear them.")
            return "unknown setting"
        val, err = settings.parse_value(key, value)
        if err:
            await self.speak(err[0].upper() + err[1:] + ".")
            return "bad value: " + err
        settings.set_value(key, val)
        if settings.SPEC[key]["side"] == "frontend":
            await self.push_frontend_settings()
        text = settings.describe(key, val)
        await self.speak(f"Done, {text} now." if not text.endswith(" now") else f"Done, {text}.")
        return text

    async def t_list_settings(self, from_router=False):
        v = settings.values()
        parts = [settings.describe(k, v[k]) for k in settings.SPEC]
        await self.ui.send("settings", values=v, open=True)     # pops the Settings panel on the robot
        await self.speak("Here are my settings: " + "; ".join(parts[:5]) + ". The rest are in the panel next to me. "
                         "Change any of them there, or just tell me, like: set your follow-up time to 10 seconds.")
        return "listed"

    async def t_reset_settings(self, from_router=False):
        settings.reset()
        await self.push_frontend_settings()
        await self.speak("All my settings are back to default.")
        return "reset"

    # ------------------------------------------------------------ media
    async def _youtube_top(self, query):
        """The top real video (not an ad or Short) for a query, via YouTube's own results page."""
        async with httpx.AsyncClient(timeout=httpx.Timeout(8, connect=5), follow_redirects=True,
                                     headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                                              "(KHTML, like Gecko) Chrome/126.0 Safari/537.36",
                                              "Accept-Language": "en-US,en;q=0.9"}) as c:
            r = await c.get("https://www.youtube.com/results", params={"search_query": query})
        m = re.search(r'"videoRenderer":\{"videoId":"([\w-]{11})"', r.text)
        if not m:
            return None, None
        title = re.search(r'"title":\{"runs":\[\{"text":"(.*?)"\}', r.text[m.start():m.start() + 4000])
        return m.group(1), (title.group(1).encode().decode("unicode_escape", "ignore") if title else query)

    async def t_play_youtube(self, query=None, from_router=False):
        from jarvis.hands import actions as A
        q = (query or "").strip() or self.last_yt_query
        if not q:
            # "Play the first video" on a page Scar didn't search itself: point at it and click.
            return await self.t_point_click("the first video")
        try:
            vid, title = await self._youtube_top(q)
        except httpx.HTTPError as e:
            log.warning("youtube lookup failed: %s", e)
            vid = title = None
        if not vid:
            await self.speak(f"I couldn't reach YouTube directly, so here are the results for {q}.")
            A.open_url(A.search_url("youtube", q))
            return "opened search instead"
        self.last_yt_query = q
        await self.speak(f"Playing {title[:80]}.")
        A.open_url(f"https://www.youtube.com/watch?v={vid}")
        return f"playing {title}"

    async def _spotify_front(self, wait_s=6.0):
        """The Spotify window, restored and in front (opening a spotify: link doesn't un-minimize it)."""
        from jarvis.hands import actions as A
        from jarvis.hands.windows import list_windows
        w = None
        for _ in range(int(wait_s / 0.25)):
            w = next((x for x in list_windows() if x.exe == "spotify.exe"), None)
            if w:
                break
            await asyncio.sleep(0.25)
        if w is None:
            return None
        if w.minimized:
            A.restore(w)
            await asyncio.sleep(0.3)
        A.focus(w)
        return w

    async def _spotify_error(self, w):
        """Spotify sometimes shows its own "Something went wrong / Reload page" screen instead of content."""
        from jarvis.hands.capture import window_image
        from jarvis.skills.screen import ocr_image
        try:
            text = await ocr_image(await asyncio.get_running_loop().run_in_executor(None, window_image, w))
        except Exception:  # noqa: BLE001
            return False
        return "something went wrong" in text.lower()

    async def t_spotify(self, action, query, from_router=False):
        import os
        from jarvis.hands import actions as A
        uri = None
        if action == "play":
            try:
                from jarvis.skills import research
                for r in await research.search(f"{query} site:open.spotify.com", n=8):
                    m = re.search(r"open\.spotify\.com/(?:intl-\w+/)?(track|album|playlist|artist)/([A-Za-z0-9]{22})", r["url"])
                    if m:
                        uri = f"spotify:{m.group(1)}:{m.group(2)}"
                        break
            except Exception as e:  # noqa: BLE001 - fall back to a plain search
                log.warning("spotify lookup failed: %s", e)
        os.startfile(uri or "spotify:search:" + urllib.parse.quote(query))
        w = await self._spotify_front()
        if w is None:
            await self.speak("Spotify didn't open. Is it installed?")
            return "no spotify window"
        await asyncio.sleep(2.0)                      # let the page render
        if await self._spotify_error(w):
            await self.speak("Spotify is showing its own error page. Click Reload page in Spotify, then ask me again.")
            return "spotify error page"
        if not uri:
            await self.speak(f"Here's {query} on Spotify." if action == "search" else
                             f"I couldn't find {query} directly, so here are Spotify's results.")
            return "searched spotify"
        spot = await asyncio.get_running_loop().run_in_executor(None, find_spotify_play, w)
        if spot:
            await self.ui.fly_to(*spot, purpose="click", button={"x": spot[0] - 28, "y": spot[1] - 28, "width": 56, "height": 56})
            A.focus(w)
            await asyncio.sleep(0.1)
            A.real_click(*spot)
            await self.speak(f"Playing {query} on Spotify.")
            return f"playing {uri}"
        await self.speak(f"{query} is up on Spotify. Press the green play button.")
        return f"opened {uri}"

    async def t_search_here(self, query, from_router=False):
        from jarvis.hands import actions as A
        from jarvis.hands import uia
        from jarvis.hands.windows import foreground_window
        w = foreground_window()
        if w is None:
            await self.speak("Which app should I search in?")
            return "no window"
        if w.exe == "spotify.exe":
            return await self.t_spotify("search", query)
        rect = await uia.search_in(w, query)
        if rect is None:
            if from_router:
                from jarvis.brain.tools import FALLTHROUGH
                return FALLTHROUGH
            await self.speak(f"I can't find a search bar in {w.spoken_name}.")
            return "no search bar"
        await self.ui.fly_to(rect.left + rect.width // 2, rect.top + rect.height // 2, purpose="click", button=rect.as_dict())
        A.focus(w)
        await self.speak(f"Searching {w.spoken_name} for {query}.")
        return "done"

    # ------------------------------------------------------------ small talk (instant, no LLM round trip)
    async def t_chit_chat(self, kind, from_router=False):
        import datetime
        import random
        h = datetime.datetime.now().hour
        part = "morning" if h < 12 else "afternoon" if h < 17 else "evening"
        funny = settings.get("personality") in ("funny", "sarcastic")
        lines = {
            "greeting": [f"Hey! Good {part}.", "Hi there! What can I do for you?", "Hello! I'm all ears.",
                         "Hey hey! What's up?"] + (["Oh, it's you. My favourite human.", "Reporting for duty, captain!"] if funny else []),
            "how_are_you": ["All systems green, thanks for asking! How about you?", "Running smooth and feeling helpful. You?",
                            "Great! My fans are quiet and my circuits are happy. How are you doing?"]
                           + (["Honestly? Better now that someone's talking to me.", "Fantastic. I just defragmented my feelings."] if funny else []),
            "who_are_you": ["I'm Scar, your desktop assistant. I live on your screen, run entirely on this PC, and I can "
                            "open things, answer questions, look at your screen and a lot more. Ask me what I can do!"],
            "who_made_you": ["I was built right here on this PC, by you and a lot of late-night code. I run on local models, "
                             "so nothing leaves your computer unless you ask me to look something up."],
            "good": ["Glad to hear it!", "Love that.", "Nice! Anything I can help with?"],
            "bad": ["Sorry to hear that. Want me to put on some music, or set a break reminder?",
                    "That's rough. I'm here if you need anything, even just a timer for a break."],
            "love": ["Aww, right back at you!", "You're making my visor glow."],
        }
        await self.speak(random.choice(lines.get(kind, lines["greeting"])))
        return "said hello"

    # ------------------------------------------------------------ about Scar
    async def t_capabilities(self, from_router=False):
        await self.ui.send("say", text="🤖 I can: " + "; ".join(CAPABILITIES))
        await self.speak("Quite a lot! I can " + CAPABILITIES[0] + "; " + CAPABILITIES[1] + "; " + CAPABILITIES[2] +
                         "; plus timers, weather, news, notes, research and system control. You can even change my own "
                         "settings, like my size, voice or personality. The full list is on my bubble.")
        return "listed"

    async def t_self_check(self, from_router=False):
        await self.speak("Running a quick self check.")
        ok, bad = [], []
        s = runtime.session
        # microphone
        if s is not None:
            if not s.mic_on:
                bad.append("the microphone is switched off")
            elif time.monotonic() - getattr(s, "last_audio", time.monotonic()) > 3:
                bad.append("no audio is coming from the microphone")
            else:
                ok.append("microphone")
            if s.aec is not None:
                ok.append("echo cancelling" + (f" ({s.aec.erle_db:.0f} dB)" if s.aec.erle_db >= 3 else ""))
        # brain
        try:
            async with httpx.AsyncClient(timeout=3) as c:
                r = await c.get(f"http://127.0.0.1:{config.LLM_PORT}/health")
            (ok if r.status_code == 200 else bad).append("AI model" if r.status_code == 200 else "the AI model isn't healthy")
        except httpx.HTTPError:
            bad.append("the AI model isn't responding")
        # internet
        from jarvis.skills import extras
        net = await extras.internet_check()
        (ok if net.startswith("Online") else bad).append("internet" if net.startswith("Online") else "there's no internet")
        # voice / ears
        if runtime.tts is not None:
            ok.append("voice")
        vp = runtime.voiceprint
        if vp is not None and not vp.enrolled:
            bad.append("I haven't learned your voice yet (say: learn my voice)")
        # recent errors in the log
        errors = 0
        try:
            lines = (config.LOG_DIR / "jarvis.log").read_text(encoding="utf-8", errors="ignore").splitlines()[-400:]
            cutoff = time.strftime("%H:%M", time.localtime(time.time() - 3600))
            errors = sum(1 for ln in lines if (" E " in ln[:12] or "Traceback" in ln) and ln[:5] >= cutoff)
        except OSError:
            pass
        if errors:
            bad.append(f"{errors} errors in my log in the last hour")
        report = "All good: " + ", ".join(ok) + "." if not bad else \
            "Working: " + ", ".join(ok) + ". Problems: " + "; ".join(bad) + "."
        await self.ui.send("say", text="🩺 " + report)
        await self.speak(report)
        return report

    async def t_what_went_wrong(self, from_router=False):
        recent = [f for f in (self.failures or []) if time.time() - f["at"] < 1800]
        if not recent:
            return "No failed commands in the last 30 minutes. If something didn't do what you expected, it probably " \
                   "went to the wrong command; tell the user to rephrase and it will be logged."
        f = recent[-1]
        return f"The last failure: tool {f['tool']} with {f['args']} returned: {f['result']}. Explain simply and suggest " \
               f"a rephrase or fix."

    # ------------------------------------------------------------ smarter open
    async def open_fallback(self, name):
        """`open X` found no app: try a file with that name, then the website of that name (first web result)."""
        from jarvis.hands import actions as A
        from rapidfuzz import fuzz
        from jarvis.skills import files
        hits = await asyncio.get_running_loop().run_in_executor(None, files.find, name)
        hits = [p for p in hits if fuzz.token_set_ratio(name.lower(), p.stem.lower().replace("_", " ").replace("-", " ")) >= 90]
        if hits:
            await self.speak(f"Opening {hits[0].name}.")
            files.open_path(hits[0])
            return f"opened file {hits[0]}"
        try:
            from jarvis.skills import research
            results = await research.search(f"{name} official site", n=5)
        except Exception:  # noqa: BLE001
            results = []
        words = [w for w in re.findall(r"[a-z0-9]+", name.lower()) if len(w) > 1]
        for r in results:
            host = urllib.parse.urlparse(r["url"]).netloc.lower().removeprefix("www.")
            flat = host.replace("-", "").replace(".", "")
            if words and all(w in flat or w in r["title"].lower().replace(" ", "") for w in words):
                if host.split(".")[0].replace("-", "") == "".join(words):
                    await self.speak(f"Opening {host}.")        # "open notion" -> notion.so: clearly it
                    A.open_url(r["url"])
                    return f"opened {r['url']}"
                url = r["url"]

                async def go():
                    await self.speak(f"Opening {host}.")
                    A.open_url(url)
                self.pending_confirm = go                         # a near miss (speech-to-text guesses): ask first
                await self.speak(f"I couldn't find an app called {name}. The closest website is {host}. Open it?")
                return "asked for confirmation"
        return None
