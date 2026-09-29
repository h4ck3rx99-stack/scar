import pytest

from jarvis.core.session import name_call


@pytest.mark.parametrize("text,rest", [
    ("Scar.", ""),
    ("Hey Scar!", ""),
    ("Scar, open YouTube.", "open YouTube"),
    ("hey skar what's the time", "what's the time"),
    ("Scarr minimize chrome", "minimize chrome"),
])
def test_name_call(text, rest):
    assert name_call(text) == rest


@pytest.mark.parametrize("text", ["Oscar is coming", "I was scared", "the scar on my hand", "open youtube", "",
                                  "Scary movie tonight"])
def test_not_a_name_call(text):
    assert name_call(text) is None
