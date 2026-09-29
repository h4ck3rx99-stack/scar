import pytest

from jarvis.brain.router import route

CASES = [
    ("learn my voice", ("learn_voice", {})),
    ("forget my voice", ("forget_voice", {})),
    ("only listen to me", ("only_me", {})),
    ("listen to everyone", ("everyone", {})),
    ("start dictation", ("dictation", {})),
    ("stop the suggestions", ("suggestions_off", {})),
    ("enable phone control", ("phone_on", {})),
    ("research best laptops under 60000", ("web_research", {"query": "best laptops under 60000"})),
    ("search the web for ipl scores", ("web_research", {"query": "ipl scores"})),
    ("write an email to my professor asking for a deadline extension",
     ("write_text", {"instructions": "Write email to my professor asking for a deadline extension"})),
    ("what's this song", ("now_playing", {})),
    ("what's playing", ("now_playing", {})),
    ("change your voice to british", ("voice_style", {"voice": "british"})),
    ("talk faster", ("voice_style", {"speed": "faster"})),
    ("what did i work on today", ("my_activity", {"when": "today"})),
    ("what did i do yesterday", ("my_activity", {"when": "yesterday"})),
    ("what was that error yesterday", ("recall", {"query": "error", "when": "yesterday"})),
    ("search my files for thermodynamics", ("search_my_files", {"query": "thermodynamics"})),
    ("summarize syllabus.pdf", ("ask_file", {"file": "syllabus.pdf", "question": "Summarize this file in a few spoken sentences."})),
    ("create a routine called gaming mode: close vs code, open steam, volume 80",
     ("create_routine", {"name": "gaming mode", "steps": "close vs code open steam volume 80"})),
    ("study mode", ("run_routine", {"name": "study mode"})),
    ("start focus mode", ("run_routine", {"name": "focus"})),
    ("list my routines", ("list_routines", {})),
    ("add maths class every monday at 9 am", ("add_event", {"title": "maths class", "when": "every monday at 9 am"})),
    ("what's my schedule tomorrow", ("my_schedule", {"day": "tomorrow"})),
    ("remove maths class from my schedule", ("remove_event", {"title": "maths class"})),
]


@pytest.mark.parametrize("text,expected", CASES)
def test_feature_routes(text, expected):
    assert route(text) == expected


@pytest.mark.parametrize("text,not_tool", [
    ("set an alarm at 7 am", "add_event"), ("set volume at 40", "add_event"),
    ("switch to vs code", "run_routine"), ("start a timer for 5 minutes", "run_routine"),
])
def test_no_misroutes(text, not_tool):
    hit = route(text)
    assert hit is None or hit[0] != not_tool


def test_routine_steps_split():
    from jarvis.skills.routines import split_steps
    assert split_steps("close youtube, open vs code and start a 50 minute timer") == \
        ["close youtube", "open vs code", "start a 50 minute timer"]
    assert split_steps("close vs code open steam volume 80") == ["close vs code", "open steam", "volume 80"]
    assert split_steps("turn the volume down then go on standby") == ["turn the volume down", "go on standby"]


def test_schedule_parse():
    from jarvis.skills.schedule import _parse_when
    days, date, hhmm = _parse_when("every monday and wednesday at 9 am")
    assert days == [0, 2] and date is None and hhmm == "09:00"
    days, date, hhmm = _parse_when("friday at 4:30 pm")
    assert days is None and date and hhmm == "16:30"
