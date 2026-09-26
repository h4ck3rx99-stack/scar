"""Native Ollama adapter (/api/chat).

The OpenAI-compatible endpoint cannot set the context window, which is too
small for tool-heavy prompts, so SCAR uses the native API: ``num_ctx``,
tool calling, image input, JSON-schema ``format`` and ``keep_alive``.
"""

from __future__ import annotations

import base64
import json
import time
from typing import Any

import httpx

from scar.core.ids import new_id
from scar.providers.base import ChatRequest, ChatResponse, ToolCall, Usage
from scar.providers.errors import ProviderError, ProviderErrorKind, classify_http
from scar.providers.llm.openai_compat import strip_think


class OllamaClient:
    def __init__(self, base_url: str, *, num_ctx: int = 16384, keep_alive_s: float = 300.0, timeout: float = 300.0,
                 transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.provider = "ollama"
        self.base_url = base_url.rstrip("/").removesuffix("/v1")
        self.num_ctx = num_ctx
        self.keep_alive_s = keep_alive_s
        self._client = httpx.AsyncClient(base_url=self.base_url, timeout=httpx.Timeout(timeout, connect=5.0),
                                         transport=transport)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def chat(self, model: str, request: ChatRequest) -> ChatResponse:
        t0 = time.perf_counter()
        msgs: list[dict[str, Any]] = []
        for m in request.messages:
            entry: dict[str, Any] = {"role": m.role, "content": m.content}
            if m.images:
                entry["images"] = [base64.b64encode(i.data).decode("ascii") for i in m.images]
            if m.tool_calls:
                entry["tool_calls"] = [{"function": {"name": tc.name, "arguments": tc.arguments}} for tc in m.tool_calls]
            if m.role == "tool" and m.name:
                entry["tool_name"] = m.name
            msgs.append(entry)
        payload: dict[str, Any] = {
            "model": model,
            "messages": msgs,
            "stream": False,
            "keep_alive": f"{int(self.keep_alive_s)}s",
            "options": {"num_ctx": self.num_ctx, "temperature": request.temperature, "num_predict": request.max_tokens},
            "think": False,
        }
        if request.tools:
            payload["tools"] = request.tools
        if request.json_schema is not None and not request.tools:
            payload["format"] = request.json_schema
        try:
            resp = await self._client.post("/api/chat", json=payload)
            if resp.status_code == 400 and "think" in resp.text.lower():
                payload.pop("think", None)
                resp = await self._client.post("/api/chat", json=payload)
        except httpx.ConnectError as exc:
            raise ProviderError(ProviderErrorKind.UNAVAILABLE, f"Ollama is not running: {exc}", provider="ollama", model=model) from exc
        except httpx.TimeoutException as exc:
            raise ProviderError(ProviderErrorKind.TRANSIENT, f"timeout: {exc}", provider="ollama", model=model) from exc
        except httpx.HTTPError as exc:
            raise ProviderError(ProviderErrorKind.TRANSIENT, str(exc), provider="ollama", model=model) from exc
        if resp.status_code >= 400:
            body = resp.text
            if resp.status_code == 404 or "not found" in body.lower():
                raise ProviderError(ProviderErrorKind.MODEL_UNAVAILABLE, body[:200], provider="ollama", model=model)
            if "does not support tools" in body.lower():
                raise ProviderError(ProviderErrorKind.MODEL_UNAVAILABLE, body[:200], provider="ollama", model=model)
            raise classify_http(resp.status_code, body, "ollama", model, None)
        try:
            data = resp.json()
            msg = data.get("message") or {}
        except ValueError as exc:
            raise ProviderError(ProviderErrorKind.MALFORMED, resp.text[:200], provider="ollama", model=model) from exc
        calls: list[ToolCall] = []
        for tc in msg.get("tool_calls") or []:
            fn = tc.get("function") or {}
            args = fn.get("arguments") or {}
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except json.JSONDecodeError:
                    calls.append(ToolCall(id=new_id("call"), name=str(fn.get("name", "")), raw_arguments=args,
                                          parse_error="arguments are not valid JSON"))
                    continue
            calls.append(ToolCall(id=new_id("call"), name=str(fn.get("name", "")), arguments=args, raw_arguments=json.dumps(args)))
        return ChatResponse(
            content=strip_think(str(msg.get("content") or "")),
            tool_calls=calls,
            finish_reason="tool_calls" if calls else str(data.get("done_reason") or "stop"),
            usage=Usage(prompt_tokens=int(data.get("prompt_eval_count") or 0), completion_tokens=int(data.get("eval_count") or 0)),
            provider="ollama",
            model=model,
            latency_ms=(time.perf_counter() - t0) * 1000.0,
        )

    async def list_models(self) -> list[str] | None:
        try:
            resp = await self._client.get("/api/tags", timeout=5.0)
        except httpx.HTTPError as exc:
            raise ProviderError(ProviderErrorKind.UNAVAILABLE, f"Ollama is not running: {exc}", provider="ollama") from exc
        if resp.status_code >= 400:
            raise classify_http(resp.status_code, resp.text, "ollama", "", None)
        return [str(m.get("name")) for m in resp.json().get("models", [])]

    async def model_size_mb(self, model: str) -> float | None:
        try:
            resp = await self._client.get("/api/tags", timeout=5.0)
            for m in resp.json().get("models", []):
                if m.get("name") == model:
                    return float(m.get("size", 0)) / 2**20
        except (httpx.HTTPError, ValueError):
            return None
        return None
