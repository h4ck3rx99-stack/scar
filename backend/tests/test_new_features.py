"""Clipboard history, macros storage, WhatsApp / recap / pointing routes, typing targets."""
import pytest

from jarvis.brain.router import route
from jarvis.skills import cliphistory, macros


@pytest.mark.parametrize("text,expected", [
    ("Send a WhatsApp to Rahul saying I will be late", ("whatsapp", {"contact": "rahul", "message": "i will be late"})),
    ("message mom saying call me when you're free", ("whatsapp", {"contact": "mom", "message": "call me when you're free"})),
    ("tell priya on whatsapp that the meeting moved", ("whatsapp", {"contact": "priya", "message": "the meeting moved"})),
    ("save Rahul's number as 98765 43210", ("save_contact", {"name": "rahul", "number": "98765 43210"})),
    ("what did I copy earlier", ("clipboard_history", {"action": "list"})),
    ("paste the link I copied", ("clipboard_history", {"action": "paste", "which": None, "query": "link"})),
    ("paste what I copied before", ("clipboard_history", {"action": "paste", "which": "previous", "query": None})),
    ("paste the third thing I copied", ("clipboard_history", {"action": "paste", "which": "third", "query": None})),
    ("paste what I copied about the invoice", ("clipboard_history", {"action": "paste", "which": None, "query": "the invoice"})),
    ("clear my clipboard history", ("clipboard_history", {"action": "clear"})),
    ("watch me do this", ("macro", {"action": "start", "name": None})),
    ("start recording a macro called morning setup", ("macro", {"action": "start", "name": "morning setup"})),
    ("stop recording and call it morning setup", ("macro", {"action": "stop", "name": "morning setup"})),
    ("call it morning setup", ("macro", {"action": "save", "name": "morning setup"})),
    ("run the morning setup macro", ("macro", {"action": "play", "name": "morning setup"})),
    ("list my macros", ("macro", {"action": "list"})),
    ("how was my day", ("evening_recap", {})),
    ("give me an evening recap", ("evening_recap", {})),
    ("click that", ("point_click", {"target": "that"})),
    ("double click the recycle bin", ("point_click", {"target": "recycle bin", "action": "double_click"})),
    ("How are you?", ("chit_chat", {"kind": "how_are_you"})),
    ("hello scar", ("chit_chat", {"kind": "greeting"})),
    ("good morning", ("briefing", {})),
    ("paste", ("press_keys", {"action": "paste"})),
])
def test_routes(text, expected):
    assert route(text) == expected


@pytest.mark.parametrize("text,secret", [
    ("sk-proj-abc123DEF456ghi789JKL", True), ("ghp_16C7e42F292c6912E7710c838347Ae178B4a", True),
    ("Tr0ub4dor&3xyz", True), ("hello world", False), ("https://youtube.com/watch?v=abc", False),
    ("def f(x):\n    return x", False), ("Meeting notes for Monday", False),
])
def test_secrets_are_not_kept(text, secret):
    assert cliphistory.looks_secret(text) is secret


def test_clipboard_pick():
    cliphistory.clear()
    for t in ["first thing", "https://example.com/page", "invoice 4417 total 2300", "latest copy"]:
        cliphistory._add(t)
    assert cliphistory.pick("last")["text"] == "latest copy"
    assert cliphistory.pick("previous")["text"] == "invoice 4417 total 2300"
    assert cliphistory.pick(query="link")["text"] == "https://example.com/page"
    assert cliphistory.pick(query="the invoice")["text"].startswith("invoice")
    cliphistory._add("sk-proj-abc123DEF456ghi789JKL")
    assert cliphistory.items()[0]["text"] == "latest copy"
    cliphistory.clear()


def test_macro_save_find_delete(tmp_path, monkeypatch):
    monkeypatch.setattr(macros, "DIR", tmp_path)
    rec = macros.Recorder()
    rec.pending = [{"t": 0.1, "type": "click", "x": 10, "y": 20, "button": "left", "down": True}]
    rec.save("Morning Setup")
    assert macros.names() == ["Morning Setup"]
    assert macros.find("morning setup") == "Morning Setup"
    assert macros.find("evening thing") is None
    assert macros.delete("morning setup") and macros.names() == []


def test_settings_recap_hour(monkeypatch):
    from jarvis.core import settings
    monkeypatch.setattr(settings, "get", lambda k: settings.SPEC[k]["default"])
    assert settings.parse_value("recap_hour", "9 pm") == (21, None)
    assert settings.parse_value("recap_hour", "8") == (20, None)
    assert settings.describe("recap_hour", 21) == "recap time is 9 PM"
