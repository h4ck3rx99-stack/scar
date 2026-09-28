"""Generic OpenAI-compatible chat adapter.

Covers Groq, Cerebras, OpenRouter, Mistral, NVIDIA NIM, Cloudflare Workers AI
(OpenAI endpoint), Hugging Face Inference Providers, llama.cpp llama-server,
Ollama, LM Studio and OpenAI itself. Normalises tool calls, call ids, finish
reasons and errors into the provider-neutral types.
"""

from __future__ import annotations

import json
import time
from typing import Any

import httpx
from pydantic import SecretStr

from scar.core.ids import new_id
from scar.providers.base import STREAM_SINK, ChatMessage, ChatRequest, ChatResponse, StreamSink, ThinkFilter, ToolCall, Usage
from scar.providers.errors import ProviderError, ProviderErrorKind, classify_http
from scar.providers.ratelimit import parse_retry_after

_THINK = ("<think>", "</think>")


def strip_think(text: str) -> str:
    """Remove <think>…</think> reasoning blocks (Qwen3 etc.); reasoning is never shown or logged."""
    while _THINK[0] in text:
        start = text.index(_THINK[0])
        end = text.find(_THINK[1], start)
        if end == -1:
            text = text[:start]
            break
        text = text[:start] + text[end + len(_THINK[1]) :]
    return text.strip()


def message_to_openai(m: ChatMessage) -> dict[str, Any]:
    if m.role == "tool":
        return {"role": "tool", "tool_call_id": m.tool_call_id or "", "content": m.content}
    if m.role == "assistant" and m.tool_calls:
        return {
            "role": "assistant",
            "content": m.content or None,
            "tool_calls": [
                {"id": tc.id, "type": "function",
                 "function": {"name": tc.name, "arguments": tc.raw_arguments or json.dumps(tc.arguments)}}
                for tc in m.tool_calls
            ],
        }
    if m.images:
        parts: list[dict[str, Any]] = [{"type": "text", "text": m.content}] if m.content else []
        parts += [{"type": "image_url", "image_url": {"url": img.data_url()}} for img in m.images]
        return {"role": m.role, "content": parts}
    return {"role": m.role, "content": m.content}


def parse_tool_calls(raw_calls: list[dict[str, Any]] | None) -> list[ToolCall]:
    out: list[ToolCall] = []
    for rc in raw_calls or []:
        fn = rc.get("function") or {}
        raw = fn.get("arguments")
        name = str(fn.get("name") or "")
        call_id = str(rc.get("id") or new_id("call"))
        if isinstance(raw, dict):
            out.append(ToolCall(id=call_id, name=name, arguments=raw, raw_arguments=json.dumps(raw)))
            continue
        raw_s = str(raw or "{}")
        try:
            parsed = json.loads(raw_s) if raw_s.strip() else {}
            if not isinstance(parsed, dict):
                raise ValueError("arguments are not a JSON object")
            out.append(ToolCall(id=call_id, name=name, arguments=parsed, raw_arguments=raw_s))
        except (json.JSONDecodeError, ValueError) as exc:
            out.append(ToolCall(id=call_id, name=name, raw_arguments=raw_s, parse_error=str(exc)))
    return out


class OpenAICompatClient:
    def __init__(
        self,
        provider: str,
        base_url: str,
        api_key: SecretStr | None,
        *,
        headers: dict[str, str] | None = None,
        timeout: float = 90.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.provider = provider
        self.base_url = base_url.rstrip("/")
        h = {"Content-Type": "application/json", **(headers or {})}
        if api_key is not None:
            h["Authorization"] = f"Bearer {api_key.get_secret_value()}"
        self._client = httpx.AsyncClient(base_url=self.base_url, headers=h, timeout=httpx.Timeout(timeout, connect=10.0),
                                         transport=transport)

    async def aclose(self) -> None:
        await self._client.aclose()

    def _payload(self, model: str, req: ChatRequest) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": model,
            "messages": [message_to_openai(m) for m in req.messages],
            "temperature": req.temperature,
            "max_tokens": req.max_tokens,
        }
        if req.tools:
            payload["tools"] = req.tools
            payload["tool_choice"] = req.tool_choice or "auto"
        if req.json_schema is not None:
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": req.json_schema_name, "schema": req.json_schema, "strict": False},
            }
        if self.provider == "llamacpp":
            payload["repeat_penalty"] = 1.15  # small local models loop without it
            # hidden "thinking" (Qwen3 etc.) is discarded anyway; skipping it makes local steps several times faster
            payload["chat_template_kwargs"] = {"enable_thinking": False}
        if req.reasoning_effort and self.provider in ("groq", "openai", "cerebras", "openrouter"):
            payload["reasoning_effort"] = req.reasoning_effort
        return payload

    async def _post(self, path: str, payload: dict[str, Any], model: str) -> httpx.Response:
        try:
            resp = await self._client.post(path, json=payload)
        except httpx.TimeoutException as exc:
            raise ProviderError(ProviderErrorKind.TRANSIENT, f"timeout: {exc}", provider=self.provider, model=model) from exc
        except httpx.ConnectError as exc:
            local = any(h in self.base_url for h in ("127.0.0.1", "localhost"))
            kind = ProviderErrorKind.UNAVAILABLE if local else ProviderErrorKind.TRANSIENT
            raise ProviderError(kind, f"cannot connect: {exc}", provider=self.provider, model=model) from exc
        except httpx.HTTPError as exc:
            raise ProviderError(ProviderErrorKind.TRANSIENT, f"network error: {exc}", provider=self.provider, model=model) from exc
        if resp.status_code >= 400:
            body = resp.text
            raise classify_http(resp.status_code, body, self.provider, model, parse_retry_after(resp.headers, body))
        return resp

    async def chat(self, model: str, request: ChatRequest) -> ChatResponse:
        sink = STREAM_SINK.get()
        if sink is not None and request.json_schema is None:
            return await self._chat_stream(model, request, sink)
        t0 = time.perf_counter()
        payload = self._payload(model, request)
        try:
            resp = await self._post("/chat/completions", payload, model)
        except ProviderError as err:
            # some servers reject response_format / tools: retry once without the optional feature
            if err.kind == ProviderErrorKind.BAD_REQUEST and "response_format" in payload and (
                "response_format" in str(err) or "json_schema" in str(err)
            ):
                payload.pop("response_format")
                resp = await self._post("/chat/completions", payload, model)
            else:
                raise
        try:
            data = resp.json()
            choice = data["choices"][0]
            msg = choice.get("message") or {}
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise ProviderError(ProviderErrorKind.MALFORMED, f"unexpected response: {resp.text[:200]}",
                                provider=self.provider, model=model) from exc
        content = msg.get("content") or ""
        if isinstance(content, list):
            content = "".join(p.get("text", "") for p in content if isinstance(p, dict))
        usage = data.get("usage") or {}
        return ChatResponse(
            content=strip_think(str(content)),
            tool_calls=parse_tool_calls(msg.get("tool_calls")),
            finish_reason=str(choice.get("finish_reason") or ""),
            usage=Usage(prompt_tokens=int(usage.get("prompt_tokens") or 0),
                        completion_tokens=int(usage.get("completion_tokens") or 0)),
            provider=self.provider,
            model=str(data.get("model") or model),
            latency_ms=(time.perf_counter() - t0) * 1000.0,
        )

    async def _chat_stream(self, model: str, request: ChatRequest, sink: StreamSink) -> ChatResponse:
        """Server-sent-events streaming: visible text goes to the sink as it arrives; tool calls are reassembled."""
        t0 = time.perf_counter()
        payload = {**self._payload(model, request), "stream": True}
        if self.provider in ("openai", "groq", "cerebras", "openrouter", "llamacpp", "mistral"):
            payload["stream_options"] = {"include_usage": True}
        text: list[str] = []
        calls: dict[int, dict[str, Any]] = {}
        finish = ""
        usage: dict[str, Any] = {}
        resolved_model = model
        think = ThinkFilter()
        first_token_ms: float | None = None
        sink.begin()
        try:
            async with self._client.stream("POST", "/chat/completions", json=payload) as resp:
                if resp.status_code >= 400:
                    body = (await resp.aread()).decode("utf-8", "replace")
                    raise classify_http(resp.status_code, body, self.provider, model, parse_retry_after(resp.headers, body))
                async for line in resp.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    data_s = line[5:].strip()
                    if data_s == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data_s)
                    except ValueError:
                        continue
                    if chunk.get("error"):
                        raise ProviderError(ProviderErrorKind.TRANSIENT, f"stream error: {str(chunk['error'])[:200]}",
                                            provider=self.provider, model=model)
                    resolved_model = str(chunk.get("model") or resolved_model)
                    if chunk.get("usage"):
                        usage = chunk["usage"]
                    elif (chunk.get("x_groq") or {}).get("usage"):
                        usage = chunk["x_groq"]["usage"]
                    for choice in chunk.get("choices") or []:
                        delta = choice.get("delta") or {}
                        piece = delta.get("content")
                        if isinstance(piece, str) and piece:
                            text.append(piece)
                            visible = think.feed(piece)
                            if visible:
                                if first_token_ms is None:
                                    first_token_ms = (time.perf_counter() - t0) * 1000.0
                                sink.delta(visible)
                        for tc in delta.get("tool_calls") or []:
                            slot = calls.setdefault(int(tc.get("index", 0)), {"id": "", "name": "", "arguments": ""})
                            if tc.get("id"):
                                slot["id"] = tc["id"]
                            fn = tc.get("function") or {}
                            if fn.get("name"):
                                slot["name"] += fn["name"]
                            if fn.get("arguments"):
                                slot["arguments"] += fn["arguments"]
                        if choice.get("finish_reason"):
                            finish = str(choice["finish_reason"])
        except httpx.TimeoutException as exc:
            raise ProviderError(ProviderErrorKind.TRANSIENT, f"timeout: {exc}", provider=self.provider, model=model) from exc
        except httpx.ConnectError as exc:
            local = any(h in self.base_url for h in ("127.0.0.1", "localhost"))
            kind = ProviderErrorKind.UNAVAILABLE if local else ProviderErrorKind.TRANSIENT
            raise ProviderError(kind, f"cannot connect: {exc}", provider=self.provider, model=model) from exc
        except httpx.HTTPError as exc:
            raise ProviderError(ProviderErrorKind.TRANSIENT, f"network error: {exc}", provider=self.provider, model=model) from exc
        raw_calls = [{"id": c["id"] or None, "function": {"name": c["name"], "arguments": c["arguments"]}}
                     for _, c in sorted(calls.items())]
        return ChatResponse(
            content=strip_think("".join(text)),
            tool_calls=parse_tool_calls(raw_calls),
            finish_reason=finish,
            usage=Usage(prompt_tokens=int(usage.get("prompt_tokens") or 0),
                        completion_tokens=int(usage.get("completion_tokens") or 0)),
            provider=self.provider,
            model=resolved_model,
            latency_ms=(time.perf_counter() - t0) * 1000.0,
            first_token_ms=first_token_ms,
        )

    async def list_models(self) -> list[str] | None:
        try:
            resp = await self._client.get("/models", timeout=15.0)
        except httpx.HTTPError as exc:
            local = any(h in self.base_url for h in ("127.0.0.1", "localhost"))
            kind = ProviderErrorKind.UNAVAILABLE if local else ProviderErrorKind.TRANSIENT
            raise ProviderError(kind, f"cannot list models: {exc}", provider=self.provider) from exc
        if resp.status_code >= 400:
            raise classify_http(resp.status_code, resp.text, self.provider, "", parse_retry_after(resp.headers, resp.text))
        try:
            data = resp.json()
        except ValueError:
            return None
        items = data.get("data") if isinstance(data, dict) else data
        if not isinstance(items, list):
            return None
        return [str(i.get("id") or i.get("name")) for i in items if isinstance(i, dict)]

    async def transcribe(self, model: str, audio_wav: bytes, language: str | None = None) -> dict[str, Any]:
        """OpenAI-compatible /audio/transcriptions (Groq Whisper)."""
        files = {"file": ("audio.wav", audio_wav, "audio/wav")}
        form: dict[str, str] = {"model": model, "response_format": "verbose_json"}
        if language:
            form["language"] = language
        # built without the client's defaults: they carry Content-Type: application/json, which would override the
        # multipart boundary header (httpx merges client headers into build_request)
        headers = {k: v for k, v in self._client.headers.items() if k.lower() not in ("content-type", "content-length")}
        req = httpx.Request("POST", f"{self.base_url.rstrip('/')}/audio/transcriptions", files=files, data=form,
                            headers=headers, extensions={"timeout": httpx.Timeout(60.0).as_dict()})
        try:
            resp = await self._client.send(req)
        except httpx.HTTPError as exc:
            raise ProviderError(ProviderErrorKind.TRANSIENT, f"network error: {exc}", provider=self.provider, model=model) from exc
        if resp.status_code >= 400:
            raise classify_http(resp.status_code, resp.text, self.provider, model, parse_retry_after(resp.headers, resp.text))
        try:
            return dict(resp.json())
        except ValueError as exc:
            raise ProviderError(ProviderErrorKind.MALFORMED, "bad transcription response", provider=self.provider, model=model) from exc
