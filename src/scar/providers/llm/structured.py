"""Structured output: JSON-schema constrained responses with one repair pass,
plus the JSON action protocol used when a model answers in text instead of
native tool calls."""

from __future__ import annotations

import json
import re
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from scar.core.types import TaskState
from scar.providers.base import ChatMessage, ChatRequest, ToolCall
from scar.providers.errors import ProviderError, ProviderErrorKind

M = TypeVar("M", bound=BaseModel)

_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


def extract_json(text: str) -> Any:
    """Parse the first JSON object/array in ``text`` (tolerates code fences and prose)."""
    text = text.strip()
    candidates = [m.group(1).strip() for m in _FENCE.finditer(text)] + [text]
    for cand in candidates:
        try:
            return json.loads(cand)
        except json.JSONDecodeError:
            pass
        for opener, closer in (("{", "}"), ("[", "]")):
            start = cand.find(opener)
            while start != -1:
                depth = 0
                in_str = False
                esc = False
                for i in range(start, len(cand)):
                    ch = cand[i]
                    if in_str:
                        if esc:
                            esc = False
                        elif ch == "\\":
                            esc = True
                        elif ch == '"':
                            in_str = False
                        continue
                    if ch == '"':
                        in_str = True
                    elif ch == opener:
                        depth += 1
                    elif ch == closer:
                        depth -= 1
                        if depth == 0:
                            try:
                                return json.loads(cand[start : i + 1])
                            except json.JSONDecodeError:
                                break
                start = cand.find(opener, start + 1)
    raise ValueError("no JSON value found in the response")


def validate_json(text: str, model_cls: type[M]) -> M:
    data = extract_json(text)
    return model_cls.model_validate(data)


async def structured_chat(
    router: Any,
    category: str,
    messages: list[ChatMessage],
    model_cls: type[M],
    *,
    data_classes: list[str] | None = None,
    task: TaskState | None = None,
    max_tokens: int = 2048,
    temperature: float = 0.1,
) -> M:
    schema = model_cls.model_json_schema()
    req = ChatRequest(messages=list(messages), json_schema=schema, json_schema_name=model_cls.__name__,
                      max_tokens=max_tokens, temperature=temperature)
    resp = await router.chat(category, req, data_classes=data_classes, task=task)
    try:
        return validate_json(resp.content, model_cls)
    except (ValueError, ValidationError) as exc:
        error = str(exc)[:800]
    repair = ChatRequest(
        messages=[*messages, ChatMessage(role="assistant", content=resp.content[:4000]),
                  ChatMessage(role="user", content=f"That output failed validation: {error}\nReturn ONLY a JSON value "
                                                   f"matching this schema, no prose:\n{json.dumps(schema)[:4000]}")],
        json_schema=schema, json_schema_name=model_cls.__name__, max_tokens=max_tokens, temperature=0.0,
    )
    resp2 = await router.chat(category, repair, data_classes=data_classes, task=task)
    try:
        return validate_json(resp2.content, model_cls)
    except (ValueError, ValidationError) as exc:
        raise ProviderError(ProviderErrorKind.MALFORMED, f"structured output invalid after repair: {str(exc)[:300]}",
                            provider=resp2.provider, model=resp2.model) from exc


_ACTION_KEYS = ("tool", "name", "action", "function")


_FINISH_KEYS = frozenset({"summary", "evidence", "status"})


def _is_bare_finish(item: dict[str, object]) -> bool:
    return isinstance(item.get("summary"), str) and set(item) <= _FINISH_KEYS


def parse_json_action(text: str, known_tools: set[str]) -> list[ToolCall]:
    """JSON action protocol: {"tool": "fs.read", "args": {...}} (or a list of them)."""
    try:
        data = extract_json(text)
    except ValueError:
        return []
    items = data if isinstance(data, list) else [data]
    calls: list[ToolCall] = []
    for i, item in enumerate(items):
        if not isinstance(item, dict):
            continue
        name = next((str(item[k]) for k in _ACTION_KEYS if isinstance(item.get(k), str)), "")
        if not name and _is_bare_finish(item) and "finish" in known_tools:
            # the model wrote finish's arguments as its reply: {"summary": …, "evidence": […], "status": "done"}
            calls.append(ToolCall(id=f"json_{i}", name="finish", arguments=item, raw_arguments=json.dumps(item)))
            continue
        wire = name.replace(".", "__")
        if name not in known_tools and wire not in known_tools:
            continue
        args = item.get("args") or item.get("arguments") or item.get("parameters") or item.get("input") or {}
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except json.JSONDecodeError:
                args = {}
        calls.append(ToolCall(id=f"json_{i}", name=wire if wire in known_tools else name, arguments=args if isinstance(args, dict) else {},
                              raw_arguments=json.dumps(args)))
    return calls
