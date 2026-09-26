"""Provider routing and every branch of the fallback error taxonomy (C6, D2)."""

from __future__ import annotations

import time

import httpx
import pytest
import respx
from pydantic import BaseModel, SecretStr

from scar.core.events import EventBus
from scar.providers.base import ChatMessage, ChatRequest
from scar.providers.errors import AllProvidersFailed, ProviderErrorKind, classify_http
from scar.providers.health import HealthTracker
from scar.providers.llm.gemini import GeminiClient
from scar.providers.llm.openai_compat import OpenAICompatClient, strip_think
from scar.providers.llm.structured import extract_json, parse_json_action, structured_chat
from scar.providers.ratelimit import parse_duration, parse_retry_after
from tests.helpers import ScriptedChatClient, install_scripted_router, perr, reply

REQ = ChatRequest(messages=[ChatMessage(role="user", content="hi")])


@pytest.fixture
def router(services):  # type: ignore[no-untyped-def]
    return services.router


async def _route(services, steps_by_provider, order=None, **kw):  # type: ignore[no-untyped-def]
    clients = {p: ScriptedChatClient(p, steps) for p, steps in steps_by_provider.items()}
    install_scripted_router(services, clients, order)
    events = []
    services.bus.add_listener(lambda e: events.append(e) if e.kind == "provider_fallback" else None)
    resp = await services.router.chat("reasoning", REQ, **kw)
    return resp, clients, events


async def test_success_first_provider(services) -> None:
    resp, clients, events = await _route(services, {"a": [reply(content="ok")], "b": [reply(content="b")]})
    assert resp.content == "ok" and resp.provider == "a"
    assert not events
    assert services.health.get("a", "a-model").latency_ewma_ms is not None


async def test_auth_error_falls_through_and_marks_unavailable(services) -> None:
    resp, clients, events = await _route(services, {"a": [perr(ProviderErrorKind.AUTH)], "b": [reply(content="b")]})
    assert resp.provider == "b"
    assert services.health.get("a", "a-model").state == "unavailable"
    assert events and events[0].to_provider == "b/b-model"


async def test_rate_limit_sets_cooldown_and_falls_over_immediately(services) -> None:
    t0 = time.monotonic()
    resp, *_ = await _route(services, {"a": [perr(ProviderErrorKind.RATE_LIMIT, retry_after=42)], "b": [reply(content="b")]})
    assert resp.provider == "b"
    assert time.monotonic() - t0 < 2  # did not sleep 42 s
    h = services.health.get("a", "a-model")
    assert 40 < h.cooldown_until - time.time() <= 42
    # the cooled-down provider is skipped on the next call
    resp2, clients, _ = await _route(services, {"a": [reply(content="a")], "b": [reply(content="b2")]})
    assert resp2.content == "b2" and not clients["a"].requests


async def test_quota_long_cooldown(services) -> None:
    await _route(services, {"a": [perr(ProviderErrorKind.QUOTA)], "b": [reply()]})
    assert services.health.get("a", "a-model").cooldown_until - time.time() > 3000


async def test_transient_retries_once_then_falls_through(services) -> None:
    resp, clients, _ = await _route(services, {"a": [perr(ProviderErrorKind.TRANSIENT), perr(ProviderErrorKind.TRANSIENT)],
                                               "b": [reply(content="b")]})
    assert len(clients["a"].requests) == 2 and resp.provider == "b"
    resp, clients, _ = await _route(services, {"a": [perr(ProviderErrorKind.TRANSIENT), reply(content="second try")]})
    assert resp.content == "second try"


async def test_circuit_breaker_opens_after_repeated_failures(services) -> None:
    for _ in range(2):
        await _route(services, {"a": [perr(ProviderErrorKind.TRANSIENT)] * 2, "b": [reply()]})
    h = services.health.get("a", "a-model")
    assert h.state == "open" and h.cooldown_until > time.time()


async def test_model_unavailable_removed_for_session(services) -> None:
    await _route(services, {"a": [perr(ProviderErrorKind.MODEL_UNAVAILABLE)], "b": [reply()]})
    assert services.health.get("a", "a-model").state == "removed"


async def test_context_overflow_compacts_once(services) -> None:
    calls = []

    async def compactor(req: ChatRequest) -> ChatRequest:
        calls.append(req)
        return req.model_copy(update={"messages": req.messages[-1:]})

    resp, *_ = await _route(services, {"a": [perr(ProviderErrorKind.CONTEXT_OVERFLOW), reply(content="fits")]}, compactor=compactor)
    assert resp.content == "fits" and len(calls) == 1


async def test_all_failed_raises_degraded(services) -> None:
    with pytest.raises(AllProvidersFailed) as ei:
        await _route(services, {"a": [perr(ProviderErrorKind.AUTH)], "b": [perr(ProviderErrorKind.QUOTA)]})
    assert {a.kind for a in ei.value.attempts} >= {ProviderErrorKind.AUTH, ProviderErrorKind.QUOTA}


async def test_short_rate_limit_only_option_waits_and_retries(services) -> None:
    resp, clients, _ = await _route(services, {"a": [perr(ProviderErrorKind.RATE_LIMIT, retry_after=0.2), reply(content="later")]})
    assert resp.content == "later" and len(clients["a"].requests) == 2


async def test_privacy_local_only_skips_cloud(services) -> None:
    services.privacy.overrides["screen"] = "local_only"
    with pytest.raises(AllProvidersFailed) as ei:
        await _route(services, {"a": [reply()]}, data_classes=["screen"])
    assert ei.value.attempts[0].kind == ProviderErrorKind.PRIVACY


async def test_missing_credentials_skipped_and_models_resolved(services) -> None:
    router = services.router
    spec = router.catalog.providers["groq"]
    assert router.credential_status(spec) == (False, "missing GROQ_API_KEY")
    assert router.resolve_from_list(["m1", "m2"], ["m2", "x"]) == ["m2"]
    assert router.resolve_from_list(["m1"], None) == ["m1"]


async def test_cloud_requests_are_redacted(services) -> None:
    from scar.security.redaction import global_redactor

    global_redactor().add_secret("TEST_SECRET", "supersecretvalue123456")
    clients = {"a": ScriptedChatClient("a", [reply(content="ok")])}
    install_scripted_router(services, clients)
    await services.router.chat("reasoning", ChatRequest(messages=[ChatMessage(role="user", content="key supersecretvalue123456")]))
    sent = clients["a"].requests[0][1].messages[0].content
    assert "supersecretvalue123456" not in sent and "REDACTED" in sent


async def test_egress_recorded(services) -> None:
    await _route(services, {"a": [reply()]}, data_classes=["files"])
    assert services.egress.summary() == {"a": ["files"]}


class _Answer(BaseModel):
    answer: int


async def test_structured_output_repair(services) -> None:
    clients = {"a": ScriptedChatClient("a", [reply(content="the answer is forty-two"), reply(content='{"answer": 42}')])}
    install_scripted_router(services, clients)
    out = await structured_chat(services.router, "reasoning", [ChatMessage(role="user", content="q")], _Answer)
    assert out.answer == 42
    assert "failed validation" in clients["a"].requests[1][1].messages[-1].content


def test_extract_json_and_json_action_protocol() -> None:
    assert extract_json('Sure!\n```json\n{"a": 1}\n```') == {"a": 1}
    assert extract_json('prefix {"a": {"b": "}"}} suffix') == {"a": {"b": "}"}}
    calls = parse_json_action('{"tool": "fs.read", "args": {"path": "x"}}', {"fs__read"})
    assert calls[0].name == "fs__read" and calls[0].arguments == {"path": "x"}
    assert parse_json_action('{"tool": "evil.tool"}', {"fs__read"}) == []


def test_strip_think() -> None:
    assert strip_think("<think>secret reasoning</think>Answer") == "Answer"
    assert strip_think("A<think>unterminated") == "A"


def test_ratelimit_parsing() -> None:
    assert parse_duration("2m59.56s") == pytest.approx(179.56)
    assert parse_duration("250ms") == pytest.approx(0.25)
    assert parse_retry_after({"Retry-After": "7"}) == 7
    assert parse_retry_after({"retry-after-ms": "1500"}) == 1.5
    assert parse_retry_after({"x-ratelimit-remaining-requests": "0", "x-ratelimit-reset-requests": "12s",
                              "x-ratelimit-reset-tokens": "2m"}) == 12
    assert parse_retry_after({}, '{"error": {"details": [{"retryDelay": "42s"}]}}') == 42


def test_http_classification() -> None:
    assert classify_http(401, "bad key", "p", "m", None).kind == ProviderErrorKind.AUTH
    assert classify_http(429, "slow down", "p", "m", 5).kind == ProviderErrorKind.RATE_LIMIT
    assert classify_http(429, "tokens per day limit reached", "p", "m", None).kind == ProviderErrorKind.QUOTA
    assert classify_http(404, "model not found", "p", "m", None).kind == ProviderErrorKind.MODEL_UNAVAILABLE
    assert classify_http(400, "context length exceeds maximum", "p", "m", None).kind == ProviderErrorKind.CONTEXT_OVERFLOW
    assert classify_http(503, "overloaded", "p", "m", None).kind == ProviderErrorKind.TRANSIENT


async def test_openai_compat_request_and_tool_call_parsing(respx_mock) -> None:
    route = respx_mock.post("https://api.example.test/v1/chat/completions").mock(return_value=httpx.Response(200, json={
        "model": "m", "choices": [{"finish_reason": "tool_calls", "message": {"content": None, "tool_calls": [
            {"id": "c1", "type": "function", "function": {"name": "fs__read", "arguments": '{"path": "a.txt"}'}},
            {"id": "c2", "type": "function", "function": {"name": "fs__read", "arguments": "{bad json"}}]}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5}}))
    c = OpenAICompatClient("x", "https://api.example.test/v1", SecretStr("k123"))
    resp = await c.chat("m", ChatRequest(messages=[ChatMessage(role="user", content="hi")],
                                         tools=[{"type": "function", "function": {"name": "fs__read", "parameters": {}}}]))
    sent = route.calls[0].request
    assert sent.headers["authorization"] == "Bearer k123"
    body = __import__("json").loads(sent.content)
    assert body["tool_choice"] == "auto" and body["model"] == "m"
    assert resp.tool_calls[0].arguments == {"path": "a.txt"}
    assert resp.tool_calls[1].parse_error
    await c.aclose()


async def test_openai_compat_429_retry_after(respx_mock) -> None:
    respx_mock.post("https://api.example.test/v1/chat/completions").mock(
        return_value=httpx.Response(429, headers={"retry-after": "9"}, text="rate limited"))
    c = OpenAICompatClient("x", "https://api.example.test/v1", SecretStr("k"))
    with pytest.raises(Exception) as ei:
        await c.chat("m", REQ)
    assert ei.value.kind == ProviderErrorKind.RATE_LIMIT and ei.value.retry_after == 9
    await c.aclose()


async def test_gemini_request_shape_and_function_call(respx_mock) -> None:
    route = respx_mock.post("https://gl.test/v1beta/models/g1:generateContent").mock(return_value=httpx.Response(200, json={
        "candidates": [{"content": {"parts": [{"functionCall": {"name": "fs__read", "args": {"path": "p"}}}]}, "finishReason": "STOP"}],
        "usageMetadata": {"promptTokenCount": 3, "candidatesTokenCount": 2}}))
    c = GeminiClient("https://gl.test/v1beta", SecretStr("gk"))
    tools = [{"type": "function", "function": {"name": "fs__read", "description": "d", "parameters": {
        "type": "object", "properties": {"path": {"type": "string", "title": "Path"}}, "required": ["path"], "title": "X"}}}]
    resp = await c.chat("g1", ChatRequest(messages=[ChatMessage(role="system", content="sys"), ChatMessage(role="user", content="u")],
                                          tools=tools))
    body = __import__("json").loads(route.calls[0].request.content)
    assert body["systemInstruction"]["parts"][0]["text"] == "sys"
    decl = body["tools"][0]["functionDeclarations"][0]
    assert "title" not in decl["parameters"] and "title" not in decl["parameters"]["properties"]["path"]
    assert route.calls[0].request.headers["x-goog-api-key"] == "gk"
    assert resp.tool_calls[0].name == "fs__read" and resp.tool_calls[0].arguments == {"path": "p"}
    await c.aclose()


def test_health_persistence(tmp_path) -> None:
    from scar.providers.errors import ProviderError
    from scar.storage.db import Database

    db = Database(tmp_path / "h.db")
    ht = HealthTracker(db)
    ht.failure(ProviderError(ProviderErrorKind.RATE_LIMIT, "x", provider="p", model="m", retry_after=300))
    ht2 = HealthTracker(db)
    assert not ht2.get("p", "m").usable()
    db.close()


def test_eventbus_bounded() -> None:
    import asyncio

    async def go() -> int:
        bus = EventBus(queue_size=5)
        from scar.core.events import TaskProgress

        async with bus.subscribe() as q:
            for i in range(50):
                bus.publish(TaskProgress(message=str(i)))
            return q.qsize()

    assert asyncio.run(go()) == 5
