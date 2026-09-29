import asyncio

import pytest

from jarvis.brain.router import route
from jarvis.skills import extras

CASES = [
    ("stay there", ("place", {"spot": "here"})),
    ("don't move", ("place", {"spot": "here"})),
    ("come here", ("place", {"spot": "cursor"})),
    ("roam around", ("place", {"spot": "roam"})),
    ("go to the top right corner", ("place", {"spot": "top_right"})),
    ("move to the left side", ("place", {"spot": "left"})),
    ("sit in the bottom left", ("place", {"spot": "bottom_left"})),
    ("always listen", ("listen_always", {})),
    ("only respond when i call you", ("listen_on_call", {})),
    ("mute the mic", ("mic_off", {})),
    ("turn on the microphone", ("mic_on", {})),
    ("start a pomodoro", ("pomodoro", {"action": "start"})),
    ("stop pomodoro", ("pomodoro", {"action": "stop"})),
    ("start the stopwatch", ("stopwatch", {"action": "start"})),
    ("flip a coin", ("chance", {"kind": "coin"})),
    ("roll a dice", ("chance", {"kind": "dice"})),
    ("pick a number between 1 and 10", ("chance", {"kind": "number", "low": 1, "high": 10})),
    ("convert 5 km to miles", ("convert", {"amount": 5.0, "from_unit": "km", "to_unit": "miles"})),
    ("100 dollars in rupees", ("convert", {"amount": 100.0, "from_unit": "dollars", "to_unit": "rupees"})),
    ("what's the news", ("news", {"topic": None})),
    ("latest news about cricket", ("news", {"topic": "cricket"})),
    ("define serendipity", ("define", {"word": "serendipity"})),
    ("what does ephemeral mean", ("define", {"word": "ephemeral"})),
    ("am i online", ("internet_check", {})),
]


@pytest.mark.parametrize("text,expected", CASES)
def test_more_routes(text, expected):
    assert route(text) == expected


def test_windows_still_route():
    assert route("move chrome to the left side") == ("move_window", {"app": "chrome", "where": "left"})
    assert route("switch to vs code") == ("window_action", {"app": "vs code", "action": "focus"})


@pytest.mark.parametrize("amount,f,t,expect", [
    (5, "km", "miles", "3.107"), (100, "celsius", "fahrenheit", "212.0"), (2, "gb", "mb", "2,000"), (10, "pounds", "kg", "4.536"),
])
def test_unit_conversion(amount, f, t, expect):
    assert expect in asyncio.run(extras.convert(amount, f, t))


def test_stopwatch_and_chance():
    assert extras.stopwatch("start") == "Stopwatch started."
    assert "so far" in extras.stopwatch("check")
    assert extras.chance("coin") in ("Heads.", "Tails.")


@pytest.mark.parametrize("text", ["how long on the stopwatch", "how long has the stopwatch been running",
                                  "what's the stopwatch at", "check my stopwatch", "stopwatch time"])
def test_stopwatch_check_phrasings(text):
    assert route(text) == ("stopwatch", {"action": "check"})
