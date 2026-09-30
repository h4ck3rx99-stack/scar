"""Clipboard history, macros storage, WhatsApp / recap / pointing routes, typing targets, coding-practice helpers."""
import pytest


def test_sample_io_and_output_matching():
    from jarvis.brain.tools_extra import output_matches, sample_io
    problem = ("Output\nprint Top\nSample Input 1\n3\nRavi 92\nAsha 67\nJohn 40\nSample Output 1\nRavi: A\nAsha: C\n"
               "John: F\nTop: Ravi\nConstraints\n1 <= N")
    sin, sout = sample_io(problem)
    assert sin == "3\nRavi 92\nAsha 67\nJohn 40" and sout == "Ravi: A\nAsha: C\nJohn: F\nTop: Ravi"
    assert output_matches(sout, "Output\nRavi: A\nAsha: C\nJohn: F\nTop: Ravi") == "exact"
    assert output_matches(sout, "Ravi: A\nAsha: C\nJohn: F\nTop : Ravi") == "loose"      # the bug that failed 0/4
    assert output_matches(sout, "Traceback (most recent call last)") == ""


@pytest.mark.parametrize("verdict,failed", [("3/5 test cases passed", True), ("5/5 test cases passed", False),
                                            ("All test cases passed", False), ("Test case 2 failed", True),
                                            ("Wrong Answer", True)])
def test_submit_verdicts(verdict, failed):
    from jarvis.brain.tools_extra import submit_failed
    assert submit_failed(verdict) is failed


def test_web_solution_is_split_into_tabs():
    from jarvis.brain.tools_extra import split_web_parts
    parts = split_web_parts("=== HTML ===\n<div class=\"card\">\n  <h1>Hi</h1>\n</div>\n=== CSS ===\n.card { color: red; }\n=== JS ===\n")
    assert parts["HTML"] == '<div class="card">\n  <h1>Hi</h1>\n</div>\n'
    assert parts["CSS"] == ".card { color: red; }\n" and not parts["JS"].strip()

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


def test_every_key_scar_presses_exists():
    """press() once crashed on "ctrl"+"a" (the letter wasn't in the key table): every key used anywhere must resolve."""
    import re
    from pathlib import Path
    from jarvis.hands import actions as A
    src = "\n".join(p.read_text(encoding="utf-8") for p in Path(A.__file__).parents[1].rglob("*.py"))
    for call in re.findall(r"\bpress\(([^)]*)\)", src):
        for key in re.findall(r'"([^"]+)"', call):
            assert A.vk(key) > 0, key


@pytest.mark.parametrize("text,problem", [
    ("First Negative Number\nWrite a program that reads N...\nInput\nThe first line of input is an integer N.\n"
     "Sample Input 1\n4\n3\nSample Output 1\n-5\nPYTHON Reset Run Submit", True),
    ("YOUR CONNECTION IS NOT PRIVATE Attackers might be trying to steal your information (for example, passwords). "
     "CERT AUTHORITY INVALID Back to safety", False),
    ("Claude Code conversation: I added a sample of the output to the README", False),
])
def test_only_real_problems_are_solved(text, problem):
    from jarvis.brain.tools_extra import looks_like_problem
    assert looks_like_problem(text) is problem


def test_submit_button_is_not_a_word_in_a_sentence(monkeypatch):
    """The web mock had "Submit to check your page." under the editor; Scar clicked that text instead of the button."""
    import asyncio
    from types import SimpleNamespace
    from jarvis.brain import tools_extra
    from jarvis.skills import screen
    words = [("Submit", (900, 980, 960, 1000)), ("to", (966, 980, 980, 1000)), ("check", (986, 980, 1030, 1000)),
             ("HTML", (820, 90, 870, 110)), ("CSS", (890, 90, 930, 110)), ("JS", (950, 90, 970, 110)),
             ("Submit", (1830, 90, 1900, 110))]

    async def fake_words(img):
        return words

    async def fake_shot(self, w):
        return None
    monkeypatch.setattr(screen, "ocr_words", fake_words)
    monkeypatch.setattr(tools_extra.CodeTools, "_shot", fake_shot)
    w = SimpleNamespace(frame=SimpleNamespace(left=0, top=0))
    assert asyncio.run(tools_extra.CodeTools()._find_button(w, "Submit")) == (1865, 100)
    words[:] = words[:3]                        # only the sentence is left: ask the vision model instead
    asked = []

    async def fake_eye(self, w, label):
        asked.append(label)
        return (1500, 60)
    monkeypatch.setattr(tools_extra.CodeTools, "_find_button_by_eye", fake_eye)
    assert asyncio.run(tools_extra.CodeTools()._find_button(w, "Submit")) == (1500, 60) and asked == ["Submit"]
    # a real button label with a second word on it ("Submit Code") is still a button
    words[:] = [("Submit", (1700, 90, 1760, 110)), ("Code", (1766, 90, 1810, 110))]
    assert asyncio.run(tools_extra.CodeTools()._find_button(w, "Submit")) == (1730, 100)


def test_html_instruction_tasks_count_as_problems():
    """CCBP web tasks have instructions, a design image and resources, not "Sample Input"."""
    from jarvis.brain.tools_extra import looks_like_problem
    text = ("Profile Card\nRefer to the below image.\nInstructions:\nThe page should have an HTML container element "
            "with the class name card\nResources\nCSS Colors used:\nBackground color Hex Code values: #e8f0fe")
    assert looks_like_problem(text)


def test_solve_loop_catches_a_wrong_answer_and_retries(monkeypatch):
    """The first candidate prints "Top : Ravi" (the real bug that failed 0/4); the local sample run must catch it and
    the corrected second candidate must be the one used."""
    import asyncio
    from jarvis.brain import tools_extra
    answers = ['n=int(input())\nfor _ in range(n):\n    a,b=input().split()\nprint("Top : Ravi")',
               'n=int(input())\nfor _ in range(n):\n    a,b=input().split()\nprint("Top: Ravi")']
    seen = []

    async def fake_llm(self, messages, max_tokens):
        seen.append(messages[-1]["content"])
        return answers[len(seen) - 1]
    monkeypatch.setattr(tools_extra.CodeTools, "_ask_llm_messages", fake_llm)
    code, ok = asyncio.run(tools_extra.CodeTools()._solve_with_tests(
        "Python 3", "solve", "problem", [("1\nRavi 92", "Top: Ravi")]))
    assert ok is True and 'print("Top: Ravi")' in code
    assert "'Top: Ravi' but the code printed 'Top : Ravi'" in seen[1]      # the exact difference was fed back


def test_local_runner_refuses_system_code():
    from jarvis.brain.tools_extra import run_python_locally
    assert run_python_locally("print(int(input()) * 2)", "21") == "42"
    assert run_python_locally("import os\nos.remove('x')", "") is None
    assert run_python_locally("open('x', 'w')", "") is None


def test_ccbp_web_task_is_split_into_page_text_and_rules():
    """The real Musical Night task: the model once put the Instructions and Note on the page and used a URL that OCR
    had misread ("-l-img")."""
    from jarvis.brain.tools_extra import structure_web_task
    lines = ["Musical Night Section", "In this assignment, let's build a Musical Night Section by applying the concepts we",
             "learned till now.", "Refer to the below image.", "Start Musical Night",
             "We bring you online experiences to make sure you've", "got an exciting plan every day.", "About the Event",
             "Instructions:", "• Should be able to see vertical scroll if more content is present.", "Note",
             "Try to achieve the design as close as possible.", "Should be able to see vertical scroll if more content is present.",
             "Resources", "Use the image URL given below.",
             "https://assets.ccbp.in/frontend/intermediate-rwd/musical-night-l-", "img.png", "CSS Colors used:", "#12022f",
             "Concepts Review", "Take a look at the Cheat Sheets."]
    out = structure_web_task(lines, ["https://assets.ccbp.in/frontend/intermediate-rwd/musical-night-1-img.png"])
    design = out.split("TEXT SHOWN IN THE DESIGN")[1].split("RULES")[0]
    rules = out.split("RULES")[1].split("RESOURCES")[0]
    assert "We bring you online experiences to make sure you've got an exciting plan every day." in design
    assert "Instructions" not in design and "Should be able to see vertical scroll" in rules
    assert rules.count("Should be able to see vertical scroll") == 1
    assert "musical-night-1-img.png" in out and "night-l-" not in out and "Cheat Sheets" not in out
    assert structure_web_task(["Write a program", "Sample Input 1"], []) is None


def test_css_colors_snap_to_the_task_palette():
    from jarvis.brain.tools_extra import snap_colors
    css = ".card { background-color: #e5e7eb; }\n.role { color: #6b7280; }\n.x { border: 1px solid #fff; }"
    out = snap_colors(css, ["#e8f0fe", "#6b7280", "#ffffff"])
    assert "#e8f0fe" in out and "#6b7280" in out and "#e5e7eb" not in out and "#ffffff" in out
    assert snap_colors(css, ["#e8f0fe"]) == css            # a single listed colour isn't a full palette: leave it
