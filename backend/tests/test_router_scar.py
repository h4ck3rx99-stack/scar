"""Commands from real sessions that went wrong, plus Scar's own settings."""
import pytest

from jarvis.brain.agent import looks_like_request
from jarvis.brain.router import route
from jarvis.core import settings

CASES = [
    # from the logs
    ("Start standby.", ("standby", {})),
    ("go into standby mode", ("standby", {})),
    ("do not disturb me for 30 minutes", ("standby", {"minutes": 30.0})),
    ("Search Linkin Park on Spotify.", ("spotify", {"action": "search", "query": "linkin park"})),
    ("play numb on spotify", ("spotify", {"action": "play", "query": "numb"})),
    ("Search Linkin Park on this search bar.", ("search_here", {"query": "linkin park"})),
    ("search lofi beats here", ("search_here", {"query": "lofi beats"})),
    ("Can you tell me Bangalore's weather?", ("weather", {"city": "bangalore"})),
    ("what's the weather", ("weather", {"city": None})),
    ("weather in Mumbai", ("weather", {"city": "mumbai"})),
    ("weather of delhi", ("weather", {"city": "delhi"})),
    ("Tell me what you can do.", ("capabilities", {})),
    ("Play in the end.", ("play_youtube", {"query": "in the end"})),
    ("Play in the end by Linkin Park on YouTube.", ("play_youtube", {"query": "in the end by linkin park"})),
    ("I would like to watch in the end", ("play_youtube", {"query": "in the end"})),
    ("Play the first video.", ("play_youtube", {})),
    ("Click on the first video.", ("play_youtube", {})),
    ("Open CCBP.in in a new tab.", ("open_url", {"url": "ccbp.in"})),
    ("Can you open the document named Pass Abandoned Lighthouse?", ("find_file", {"name": "pass abandoned lighthouse"})),
    ("open WhatsApp", ("open", {"name": "whatsapp"})),
    # still media
    ("play", ("media", {"action": "play_pause"})),
    ("play the next song", ("media", {"action": "next"})),
    # repeat / self check
    ("say that again", ("repeat", {"what": "said"})),
    ("do that again", ("repeat", {"what": "action"})),
    ("run a self check", ("self_check", {})),
    ("are you okay", ("self_check", {})),
    ("what went wrong", ("what_went_wrong", {})),
    # settings
    ("set your follow up time to 10 seconds", ("change_setting", {"setting": "follow up time", "value": "10 seconds"})),
    ("change your personality to funny", ("change_setting", {"setting": "personality", "value": "funny"})),
    ("be funnier", ("change_setting", {"setting": "personality", "value": "funnier"})),
    ("keep your answers short", ("change_setting", {"setting": "personality", "value": "short"})),
    ("make yourself bigger", ("change_setting", {"setting": "size", "value": "bigger"})),
    ("shrink", ("change_setting", {"setting": "size", "value": "smaller"})),
    ("turn off the pranks", ("change_setting", {"setting": "pranks", "value": "off"})),
    ("stop pranking me", ("change_setting", {"setting": "pranks", "value": "off"})),
    ("turn sound effects off", ("change_setting", {"setting": "sound effects", "value": "off"})),
    ("disable speech bubbles", ("change_setting", {"setting": "speech bubbles", "value": "disable"})),
    ("speak louder", ("change_setting", {"setting": "voice_volume", "value": "up"})),
    ("listen longer", ("change_setting", {"setting": "follow_up_seconds", "value": "up"})),
    ("set my city to Bangalore", ("change_setting", {"setting": "city", "value": "bangalore"})),
    ("increase your roaming speed", ("change_setting", {"setting": "roaming speed", "value": "up"})),
    ("what are your settings", ("list_settings", {})),
    ("reset your settings", ("reset_settings", {})),
    ("change your name to friday", ("change_setting", {"setting": "name", "value": "friday"})),
    # typing into an app, terminals, brightness words
    ("Can you type dir slash s on command prompt?", ("type_text", {"text": "dir slash s", "app": "command prompt"})),
    ("type hello in notepad", ("type_text", {"text": "hello", "app": "notepad"})),
    ("type hello world", ("type_text", {"text": "hello world"})),
    ("clear command prompt", ("type_text", {"text": "cls", "enter": True})),
    ("minimum brightness", ("set_brightness", {"level": 0})),
    ("maximum brightness", ("set_brightness", {"level": 100})),
    ("brightness to half", ("set_brightness", {"level": 50})),
    ("standby", ("standby", {})),
    # settings words must not steal system commands
    ("volume up", ("media", {"action": "volume_up"})),
    ("louder", ("media", {"action": "volume_up"})),
    ("talk faster", ("voice_style", {"speed": "faster"})),
    ("open settings", ("open", {"name": "settings"})),
]


@pytest.mark.parametrize("text,expected", CASES)
def test_routes(text, expected):
    assert route(text) == expected


@pytest.mark.parametrize("said,command,typed", [
    ("dir slash s", True, "dir /s"), ("cd src slash main", True, "cd src/main"), ("google dot com", False, "google.com"),
    ("c colon backslash users", True, "c:\\users"), ("ls dash la", True, "ls -la"), ("hello there", False, "hello there"),
    ("see you at the door", False, "see you at the door"),
])
def test_spoken_symbols(said, command, typed):
    from jarvis.brain.tools import spoken_symbols
    assert spoken_symbols(said, command) == typed


@pytest.mark.parametrize("text,unfinished", [
    ("Can you open Chrome and search about", True), ("Can you type", True), ("open the", True), ("play some", True),
    ("Close this.", False), ("What is this video about?", False), ("what does it look like", False),
    ("turn it on", False), ("open notepad", False), ("Can you open YouTube and search about Linkin Park?", False),
])
def test_unfinished_sentences(text, unfinished):
    from jarvis.core.session import _DANGLING
    assert bool(_DANGLING.search(text)) is unfinished


def test_show_me_is_not_a_window():
    assert route("Show me.") is None


@pytest.mark.parametrize("text,ok", [
    ("I would like to watch in the end.", True), ("can you tell me Bangalore sweder", True),
    ("Add a new document in notepad", True), ("Start. Press on reload.", True),
    ("You are good.", False), ("Sky", False), ("and then he said it was fine", False),
])
def test_follow_up_request_shape(text, ok):
    assert looks_like_request(text) is ok


@pytest.mark.parametrize("name,key", [("follow up time", "follow_up_seconds"), ("speech bubbles", "bubbles"),
                                      ("roaming speed", "roam_speed"), ("sound effects", "sounds"), ("pranks", "pranks"),
                                      ("your size", "size"), ("tone", "personality"), ("nonsense thing", None)])
def test_resolve_key(name, key):
    assert settings.resolve_key(name) == key


def test_parse_values(monkeypatch):
    monkeypatch.setattr(settings, "get", lambda k: settings.SPEC[k]["default"])
    assert settings.parse_value("follow_up_seconds", "10 seconds") == (10.0, None)
    assert settings.parse_value("follow_up_seconds", "1 minute") == (None, "follow-up time goes from 2 to 30 seconds")
    assert settings.parse_value("sleep_minutes", "30 seconds")[0] == 0.5 or settings.parse_value("sleep_minutes", "30 seconds")[1]
    assert settings.parse_value("size", "bigger")[0] > 1.0
    assert settings.parse_value("size", "150 percent") == (1.5, None)
    assert settings.parse_value("pranks", "off") == (False, None)
    assert settings.parse_value("personality", "funnier") == ("funny", None)
    assert settings.parse_value("voice_volume", "up")[0] > 100
    assert settings.parse_value("personality", "grumpy")[1]
