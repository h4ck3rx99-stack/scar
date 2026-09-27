"""Knowledge questions take the no-tools path; anything about the user's state, live facts, files or URLs does not."""

from __future__ import annotations

import pytest

from scar.agent.intent import is_knowledge_question


@pytest.mark.parametrize("text", [
    "In one sentence, what is RAM?", "What is a GPU?", "Give me three tips for writing clear commit messages.",
    "explain recursion like I'm five", "how do I undo a git commit", "write me a haiku about rain",
    "what is the capital of France", "who wrote Hamlet?",
])
def test_knowledge_questions(text: str) -> None:
    assert is_knowledge_question(text)


@pytest.mark.parametrize("text", [
    "what's using my RAM", "what's on my calendar today?", "what's the weather in Pune", "tell me when the build finishes",
    "open vs code", r"what is in C:\Users\x\notes.txt", "summarize https://example.com", "what time is it",
    "latest news about AI", "run the tests in this project", "how much RAM is in use", "find the failing test and fix it",
    "which apps are running", "remind me in 10 minutes to stretch",
])
def test_not_knowledge_questions(text: str) -> None:
    assert not is_knowledge_question(text)
