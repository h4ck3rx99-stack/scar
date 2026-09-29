"""Transcripts copied from real sessions (logs/jarvis.log) that were mis-handled before, and what they should do."""
import pytest

from jarvis.brain.router import route, route_all

CASES = [
    ("Stand, Stand, stand by", ("standby", {})),
    ("Watch Weather in Bangalore", ("weather", {"city": "bangalore"})),
    ("Watch on my screen.", ("look_at_screen", {"question": "What's on my screen? Describe it briefly."})),
    ("Take screenshots", ("screenshot", {})),
    ("Close this. Close notepad.", ("window_action", {"app": "notepad", "action": "close"})),
    ("Can you close it? Can you Close this Notepad?", ("window_action", {"app": "this notepad", "action": "close"})),
    ("Close the Notepad. Exactly. Close the notepad", ("window_action", {"app": "the notepad", "action": "close"})),
    ("Can you open a note card?", ("open", {"name": "notepad"})),
    ("Close this.", ("window_action", {"app": "this", "action": "close"})),
    ("Can you open Notepad?", ("open", {"name": "notepad"})),
    ("Choose this", ("window_action", {"app": "this", "action": "close"})),
]


@pytest.mark.parametrize("text,expected", CASES)
def test_real_transcript(text, expected):
    assert route(text) == expected


def test_run_on_open_offers_the_clean_phrasing_too():
    hits = route_all("Can you open Opera? Open Opera, Bukhachot.")
    assert ("open", {"name": "opera"}) in hits


def test_weather_without_city():
    assert route("what's the weather") == ("weather", {"city": None})
