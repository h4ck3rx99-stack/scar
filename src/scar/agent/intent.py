"""Cheap request classification (no model call), used to pick the lightest path that can answer honestly."""

from __future__ import annotations

import re

from scar.agent.executor import STATE_QUESTION

# phrased as a question or a request for an explanation/list/text
_QUESTION = re.compile(
    r"^(please\s+)?(what|what's|whats|why|how|who|whom|whose|when|which|where|explain|describe|define|tell me (about|what|why|how)|"
    r"give me (a|an|some|\d+|three|five|ten|two|few)|can you explain|could you explain|is it|are there any (good|ways)|"
    r"should i|compare|suggest|recommend|in (one|a few|two|\d+) (sentence|sentences|words)|summari[sz]e (the concept|how)|"
    r"write (me )?(a|an) (poem|haiku|joke|limerick|story|short))\b", re.I)
# needs live data or the user's machine/accounts, or names something to act on
_LIVE = re.compile(
    r"\b(weather|news|headlines|latest|today|tonight|tomorrow|yesterday|current(ly)?|right now|this (week|month|year)|"
    r"price|prices|stock|score|scores|who won|trending|time is it|what time|what date|what day|exchange rate)\b", re.I)
_TARGET = re.compile(r"[A-Za-z]:[\\/]|\\\\\w|(^|\s)/[\w.-]+/|https?://|www\.|"
                     r"\b\S+\.(com|org|net|io|dev|py|js|ts|md|txt|pdf|docx|csv|json|exe)\b|~[/\\]", re.I)
# about things on this computer even without "my": "how many files are in the Downloads folder?"
_LOCAL = re.compile(
    r"\bhow many (files|folders|windows|tabs|processes|apps|programs|tasks|reminders)\b|"
    r"\b(in|on|inside|under|from|of) (the |this |that |these )?[\w.-]*\s?(folder|folders|directory|directories|drive|"
    r"desktop|downloads|documents|disk|repo|repository|project)\b", re.I)
_NOT_A_QUESTION = re.compile(r"^(tell me when|let me know when|remind|notify)\b", re.I)


def is_knowledge_question(text: str) -> bool:
    """A general-knowledge or writing request that needs no tools: "what is RAM?", "give me three tips for commit
    messages", "explain recursion". Anything about the user's own state, live facts, files or URLs is excluded."""
    t = text.strip()
    if len(t) > 400 or _NOT_A_QUESTION.search(t):
        return False
    if not _QUESTION.search(t):
        return False
    return not (STATE_QUESTION.search(t) or _LIVE.search(t) or _TARGET.search(t) or _LOCAL.search(t))
