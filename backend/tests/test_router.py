import time

import pytest

from jarvis.brain.router import route

CASES = [
    ("Hey Jarvis, minimize Chrome.", ("window_action", {"app": "chrome", "action": "minimize"})),
    ("Jarvis minimise the code editor please", ("window_action", {"app": "the code editor", "action": "minimize"})),
    ("Maximize Opera.", ("window_action", {"app": "opera", "action": "maximize"})),
    ("close spotify", ("window_action", {"app": "spotify", "action": "close"})),
    ("Can you close it?", ("window_action", {"app": "it", "action": "close"})),
    ("switch to VS Code", ("window_action", {"app": "vs code", "action": "focus"})),
    ("restore notepad", ("window_action", {"app": "notepad", "action": "restore"})),
    ("make chrome full screen", ("window_action", {"app": "chrome", "action": "maximize"})),
    ("Snap Chrome to the left.", ("move_window", {"app": "chrome", "where": "left"})),
    ("move youtube to the right side", ("move_window", {"app": "youtube", "where": "right"})),
    ("put spotify in the top right corner", ("move_window", {"app": "spotify", "where": "top_right"})),
    ("move discord to the other screen", ("move_window", {"app": "discord", "where": "next_monitor"})),
    ("drag notepad to the center", ("move_window", {"app": "notepad", "where": "center"})),
    ("make chrome 16:9", ("resize_window", {"app": "chrome", "preset": "16:9"})),
    ("Resize VS Code to 4 by 3.", ("resize_window", {"app": "vs code", "preset": "4:3"})),
    ("make opera sixteen by nine", ("resize_window", {"app": "opera", "preset": "16:9"})),
    ("make notepad bigger", ("resize_window", {"app": "notepad", "preset": "bigger"})),
    ("make it smaller", ("resize_window", {"app": "it", "preset": "smaller"})),
    ("Open YouTube.", ("open", {"name": "youtube"})),
    ("open github.com", ("open_url", {"url": "github.com"})),
    ("launch notepad", ("open", {"name": "notepad"})),
    ("search youtube for lofi beats", ("web_search", {"engine": "youtube", "query": "lofi beats"})),
    ("play interstellar soundtrack on youtube", ("web_search", {"engine": "youtube", "query": "interstellar soundtrack"})),
    ("google best pizza near me", ("web_search", {"engine": "google", "query": "best pizza near me"})),
    ("close this tab", ("browser_tab", {"action": "close"})),
    ("open a new tab", ("browser_tab", {"action": "new"})),
    ("switch to the gmail tab", ("browser_tab", {"action": "switch", "query": "gmail"})),
    ("volume up", ("media", {"action": "volume_up"})),
    ("turn it down", ("media", {"action": "volume_down"})),
    ("pause the music", ("media", {"action": "play_pause"})),
    ("next song", ("media", {"action": "next"})),
    ("mute", ("media", {"action": "mute"})),
    ("Switch to dark mode.", ("set_theme", {"mode": "dark"})),
    ("light mode", ("set_theme", {"mode": "light"})),
    ("What time is it?", ("get_time", {"what": "time"})),
    ("what's the date", ("get_time", {"what": "date"})),
    ("Never mind.", ("stop", {})),
    ("go to sleep", ("go_to_sleep", {})),
    ("minimize everything", ("minimize_all", {})),
    ("yes", ("confirm", {"answer": True})),
]

LLM_CASES = [
    "what is the capital of france",
    "tell me a joke",
    "how far is the moon",
    "write a haiku about rain",
    "why is the sky blue",
]


@pytest.mark.parametrize("text,expected", CASES)
def test_router(text, expected):
    assert route(text) == expected


@pytest.mark.parametrize("text", LLM_CASES)
def test_falls_through_to_llm(text):
    assert route(text) is None


def test_router_is_fast():
    t = time.perf_counter()
    for _ in range(20):
        for text, _ in CASES:
            route(text)
    per_call_ms = (time.perf_counter() - t) * 1000 / (20 * len(CASES))
    assert per_call_ms < 5
