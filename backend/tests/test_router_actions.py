import pytest

from jarvis.brain.router import route
from jarvis.hands.keys import ACTIONS

CASES = [
    ("copy that", ("press_keys", {"action": "copy"})),
    ("paste it", ("press_keys", {"action": "paste"})),
    ("undo", ("press_keys", {"action": "undo"})),
    ("select all", ("press_keys", {"action": "select_all"})),
    ("save this", ("press_keys", {"action": "save"})),
    ("refresh the page", ("press_keys", {"action": "refresh"})),
    ("go back", ("press_keys", {"action": "back"})),
    ("zoom in", ("press_keys", {"action": "zoom_in"})),
    ("full screen", ("press_keys", {"action": "fullscreen"})),
    ("switch windows", ("press_keys", {"action": "switch_window"})),
    ("reopen closed tab", ("press_keys", {"action": "reopen_tab"})),
    ("next tab", ("press_keys", {"action": "next_tab"})),
    ("open task manager", ("press_keys", {"action": "task_manager"})),
    ("press enter", ("press_keys", {"action": "enter", "times": 1})),
    ("hit escape twice", ("press_keys", {"action": "escape", "times": 2})),
    ("scroll down", ("scroll", {"direction": "down", "amount": 6})),
    ("scroll up a lot", ("scroll", {"direction": "up", "amount": 15})),
    ("open bluetooth settings", ("open_settings", {"page": "bluetooth"})),
    ("wifi settings", ("open_settings", {"page": "wifi"})),
    ("shut down the pc", ("power", {"action": "shutdown"})),
    ("restart my laptop", ("power", {"action": "restart"})),
    ("put the pc to sleep", ("power", {"action": "sleep"})),
    ("click sign in", ("click_element", {"name": "sign in"})),
    ("Scar, click on the subscribe button", ("click_element", {"name": "subscribe"})),
    ("press the like button", ("click_element", {"name": "like"})),
    ("tap on downloads", ("click_element", {"name": "downloads"})),
]


@pytest.mark.parametrize("text,expected", CASES)
def test_action_routes(text, expected):
    assert route(text) == expected


def test_go_to_sleep_is_still_scar_not_the_pc():
    assert route("go to sleep") == ("go_to_sleep", {})


def test_every_routed_key_action_exists():
    for _, (tool, args) in CASES:
        if tool == "press_keys":
            assert args["action"] in ACTIONS
