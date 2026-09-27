"""Streaming: SSE parsing, tool-call reassembly, <think> filtering, and the bus sink's batching/reset."""

from __future__ import annotations

import json

import httpx
import respx

from scar.agent.streaming import BusSink, streaming
from scar.core.events import EventBus
from scar.providers.base import ChatMessage, ChatRequest, ThinkFilter
from scar.providers.llm.openai_compat import OpenAICompatClient


def _sse(chunks: list[dict]) -> str:
    return "".join(f"data: {json.dumps(c)}\n\n" for c in chunks) + "data: [DONE]\n\n"


def test_think_filter_across_chunk_boundaries() -> None:
    f = ThinkFilter()
    out = "".join(f.feed(p) for p in ["Hel", "lo <thi", "nk>secret plan</th", "ink> world", "<", "b>"])
    assert out == "Hello  world<b>"
    assert "secret" not in out


class _Collect:
    def __init__(self) -> None:
        self.parts: list[str] = []
        self.begins = 0

    def begin(self) -> None:
        self.begins += 1

    def delta(self, text: str) -> None:
        self.parts.append(text)


async def test_openai_compat_streams_text_and_reassembles_tool_calls() -> None:
    chunks = [
        {"model": "m", "choices": [{"delta": {"content": "Open"}}]},
        {"choices": [{"delta": {"content": "ing <think>hidden</think>now."}}]},
        {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "c1", "function": {"name": "apps__launch", "arguments": "{\"na"}}]}}]},
        {"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"arguments": "me\": \"code\"}"}}]}, "finish_reason": "tool_calls"}]},
        {"choices": [], "usage": {"prompt_tokens": 50, "completion_tokens": 9}},
    ]
    with respx.mock() as mock:
        mock.post("https://api.example.test/v1/chat/completions").mock(
            return_value=httpx.Response(200, text=_sse(chunks), headers={"content-type": "text/event-stream"}))
        client = OpenAICompatClient("groq", "https://api.example.test/v1", None)
        sink = _Collect()
        from scar.providers.base import STREAM_SINK

        token = STREAM_SINK.set(sink)
        try:
            resp = await client.chat("m", ChatRequest(messages=[ChatMessage(role="user", content="open code")]))
        finally:
            STREAM_SINK.reset(token)
        body = json.loads(mock.calls.last.request.content)
        await client.aclose()
    assert body["stream"] is True
    assert "".join(sink.parts) == "Opening now." and sink.begins == 1
    assert resp.content == "Opening now."
    assert resp.tool_calls[0].name == "apps__launch" and resp.tool_calls[0].arguments == {"name": "code"}
    assert resp.usage.prompt_tokens == 50 and resp.first_token_ms is not None


async def test_stream_error_status_is_classified() -> None:
    with respx.mock() as mock:
        mock.post("https://api.example.test/v1/chat/completions").mock(
            return_value=httpx.Response(429, text='{"error":{"message":"rate limited"}}', headers={"retry-after": "3"}))
        client = OpenAICompatClient("groq", "https://api.example.test/v1", None)
        from scar.providers.base import STREAM_SINK
        from scar.providers.errors import ProviderError, ProviderErrorKind

        token = STREAM_SINK.set(_Collect())
        try:
            try:
                await client.chat("m", ChatRequest(messages=[ChatMessage(role="user", content="hi")]))
                raise AssertionError("expected a rate-limit error")
            except ProviderError as err:
                assert err.kind == ProviderErrorKind.RATE_LIMIT
        finally:
            STREAM_SINK.reset(token)
            await client.aclose()


def test_bus_sink_batches_and_resets_on_retry() -> None:
    bus = EventBus()
    seen: list = []
    bus.add_listener(lambda e: seen.append(e) if e.kind == "assistant_delta" else None)
    with streaming(bus, "task_1") as sink:
        sink.begin()
        for ch in "abcdefghij":
            sink.delta(ch)  # 10 chars < batch size: nothing published yet unless time passed
        sink.begin()  # provider fallback: partial text must be discarded by clients
        sink.delta("final answer")
    texts = [e.text for e in seen if not e.reset]
    resets = [e for e in seen if e.reset]
    assert resets, "a retry publishes a reset"
    assert texts[-1].endswith("final answer")
    assert isinstance(sink, BusSink)
