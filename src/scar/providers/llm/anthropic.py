"""Optional paid Anthropic Messages API adapter (never required)."""

from __future__ import annotations

import base64
import time
from typing import Any

import httpx
from pydantic import SecretStr

from scar.providers.base import ChatRequest, ChatResponse, ToolCall, Usage
from scar.providers.errors import ProviderError, ProviderErrorKind, classify_http
from scar.providers.ratelimit import parse_retry_after


class AnthropicClient:
    def __init__(self, base_url: str, api_key: SecretStr | None, *, timeout: float = 120.0,
                 transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.provider = "anthropic"
        headers = {"anthropic-version": "2023-06-01", "content-type": "application/json"}
        if api_key is not None:
            headers["x-api-key"] = api_key.get_secret_value()
        self._client = httpx.AsyncClient(base_url=base_url.rstrip("/"), headers=headers,
                                         timeout=httpx.Timeout(timeout, connect=10.0), transport=transport)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def chat(self, model: str, request: ChatRequest) -> ChatResponse:
        t0 = time.perf_counter()
        system = "\n\n".join(m.content for m in request.messages if m.role == "system")
        msgs: list[dict[str, Any]] = []
        for m in request.messages:
            if m.role == "system":
                continue
            if m.role == "tool":
                msgs.append({"role": "user", "content": [{"type": "tool_result", "tool_use_id": m.tool_call_id, "content": m.content}]})
                continue
            content: list[dict[str, Any]] = []
            if m.content:
                content.append({"type": "text", "text": m.content})
            for img in m.images:
                content.append({"type": "image", "source": {"type": "base64", "media_type": img.mime,
                                                              "data": base64.b64encode(img.data).decode("ascii")}})
            for tc in m.tool_calls:
                content.append({"type": "tool_use", "id": tc.id, "name": tc.name, "input": tc.arguments})
            msgs.append({"role": m.role, "content": content or [{"type": "text", "text": ""}]})
        payload: dict[str, Any] = {"model": model, "max_tokens": request.max_tokens, "messages": msgs,
                                   "temperature": request.temperature}
        if system:
            payload["system"] = system
        if request.tools:
            payload["tools"] = [
                {"name": t["function"]["name"], "description": t["function"].get("description", ""),
                 "input_schema": t["function"].get("parameters") or {"type": "object", "properties": {}}}
                for t in request.tools
            ]
            if request.tool_choice == "required":
                payload["tool_choice"] = {"type": "any"}
        try:
            resp = await self._client.post("/messages", json=payload)
        except httpx.HTTPError as exc:
            raise ProviderError(ProviderErrorKind.TRANSIENT, str(exc), provider="anthropic", model=model) from exc
        if resp.status_code >= 400:
            raise classify_http(resp.status_code, resp.text, "anthropic", model, parse_retry_after(resp.headers, resp.text))
        data = resp.json()
        text = "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")
        calls = [ToolCall(id=b["id"], name=b["name"], arguments=b.get("input") or {})
                 for b in data.get("content", []) if b.get("type") == "tool_use"]
        u = data.get("usage") or {}
        return ChatResponse(content=text.strip(), tool_calls=calls, finish_reason=str(data.get("stop_reason") or ""),
                            usage=Usage(prompt_tokens=int(u.get("input_tokens") or 0), completion_tokens=int(u.get("output_tokens") or 0)),
                            provider="anthropic", model=model, latency_ms=(time.perf_counter() - t0) * 1000.0)

    async def list_models(self) -> list[str] | None:
        try:
            resp = await self._client.get("/models", timeout=15.0)
        except httpx.HTTPError as exc:
            raise ProviderError(ProviderErrorKind.TRANSIENT, str(exc), provider="anthropic") from exc
        if resp.status_code >= 400:
            raise classify_http(resp.status_code, resp.text, "anthropic", "", None)
        return [str(m.get("id")) for m in resp.json().get("data", [])]
