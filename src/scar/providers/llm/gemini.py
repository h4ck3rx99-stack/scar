"""Native Gemini adapter (generateContent) with function calling and image input."""

from __future__ import annotations

import base64
import json
import time
from typing import Any

import httpx
from pydantic import SecretStr

from scar.core.ids import new_id
from scar.providers.base import ChatMessage, ChatRequest, ChatResponse, ToolCall, Usage
from scar.providers.errors import ProviderError, ProviderErrorKind, classify_http
from scar.providers.ratelimit import parse_retry_after

_UNSUPPORTED_SCHEMA_KEYS = {"title", "additionalProperties", "$defs", "definitions", "default", "examples", "$schema",
                            "const", "discriminator"}


def _clean_schema(schema: Any, defs: dict[str, Any] | None = None) -> Any:
    """Gemini accepts an OpenAPI subset: inline $refs, drop unsupported keys, flatten anyOf-null."""
    if defs is None and isinstance(schema, dict):
        defs = {**schema.get("$defs", {}), **schema.get("definitions", {})}
    if isinstance(schema, dict):
        if "$ref" in schema and defs is not None:
            ref = str(schema["$ref"]).split("/")[-1]
            return _clean_schema(defs.get(ref, {}), defs)
        if "anyOf" in schema:
            options = [o for o in schema["anyOf"] if not (isinstance(o, dict) and o.get("type") == "null")]
            if len(options) == 1:
                merged = {k: v for k, v in schema.items() if k != "anyOf"}
                merged.update(options[0] if isinstance(options[0], dict) else {})
                out = _clean_schema(merged, defs)
                if isinstance(out, dict):
                    out["nullable"] = True
                return out
        out: dict[str, Any] = {}
        for k, v in schema.items():
            if k in _UNSUPPORTED_SCHEMA_KEYS:
                continue
            out[k] = _clean_schema(v, defs)
        if out.get("type") == "object" and not out.get("properties"):
            out.pop("required", None)
        return out
    if isinstance(schema, list):
        return [_clean_schema(s, defs) for s in schema]
    return schema


class GeminiClient:
    def __init__(self, base_url: str, api_key: SecretStr | None, *, timeout: float = 90.0,
                 transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.provider = "gemini"
        self.base_url = base_url.rstrip("/")
        self._key = api_key
        headers = {"Content-Type": "application/json"}
        if api_key is not None:
            headers["x-goog-api-key"] = api_key.get_secret_value()
        self._client = httpx.AsyncClient(base_url=self.base_url, headers=headers,
                                         timeout=httpx.Timeout(timeout, connect=10.0), transport=transport)

    async def aclose(self) -> None:
        await self._client.aclose()

    @staticmethod
    def _contents(messages: list[ChatMessage]) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
        system_parts: list[str] = []
        contents: list[dict[str, Any]] = []
        call_names: dict[str, str] = {}
        for m in messages:
            if m.role == "system":
                system_parts.append(m.content)
                continue
            if m.role == "tool":
                name = call_names.get(m.tool_call_id or "", m.name or "tool")
                contents.append({"role": "user", "parts": [{"functionResponse": {"name": name, "response": {"content": m.content}}}]})
                continue
            parts: list[dict[str, Any]] = []
            if m.content:
                parts.append({"text": m.content})
            for img in m.images:
                parts.append({"inlineData": {"mimeType": img.mime, "data": base64.b64encode(img.data).decode("ascii")}})
            for tc in m.tool_calls:
                call_names[tc.id] = tc.name
                parts.append({"functionCall": {"name": tc.name, "args": tc.arguments}})
            if not parts:
                parts.append({"text": ""})
            contents.append({"role": "model" if m.role == "assistant" else "user", "parts": parts})
        system = {"parts": [{"text": "\n\n".join(system_parts)}]} if system_parts else None
        return system, contents

    async def chat(self, model: str, request: ChatRequest) -> ChatResponse:
        t0 = time.perf_counter()
        system, contents = self._contents(request.messages)
        payload: dict[str, Any] = {
            "contents": contents,
            "generationConfig": {"temperature": request.temperature, "maxOutputTokens": request.max_tokens},
        }
        if system:
            payload["systemInstruction"] = system
        if request.tools:
            decls = []
            for t in request.tools:
                fn = t.get("function", t)
                params = _clean_schema(fn.get("parameters") or {"type": "object", "properties": {}})
                decl: dict[str, Any] = {"name": fn["name"], "description": fn.get("description", "")}
                if params.get("properties"):
                    decl["parameters"] = params
                decls.append(decl)
            payload["tools"] = [{"functionDeclarations": decls}]
            mode = {"required": "ANY", "none": "NONE"}.get(request.tool_choice or "auto", "AUTO")
            payload["toolConfig"] = {"functionCallingConfig": {"mode": mode}}
        if request.json_schema is not None and not request.tools:
            payload["generationConfig"]["responseMimeType"] = "application/json"
            payload["generationConfig"]["responseSchema"] = _clean_schema(request.json_schema)
        try:
            resp = await self._client.post(f"/models/{model}:generateContent", json=payload)
        except httpx.TimeoutException as exc:
            raise ProviderError(ProviderErrorKind.TRANSIENT, f"timeout: {exc}", provider="gemini", model=model) from exc
        except httpx.HTTPError as exc:
            raise ProviderError(ProviderErrorKind.TRANSIENT, f"network error: {exc}", provider="gemini", model=model) from exc
        if resp.status_code >= 400:
            body = resp.text
            if resp.status_code == 400 and "API key not valid" in body:
                raise ProviderError(ProviderErrorKind.AUTH, body[:200], provider="gemini", model=model, status=400)
            if resp.status_code == 429 and "RESOURCE_EXHAUSTED" in body and "PerDay" in body:
                raise ProviderError(ProviderErrorKind.QUOTA, body[:300], provider="gemini", model=model, status=429,
                                    retry_after=parse_retry_after(resp.headers, body))
            raise classify_http(resp.status_code, body, "gemini", model, parse_retry_after(resp.headers, body))
        try:
            data = resp.json()
            cand = (data.get("candidates") or [{}])[0]
            parts = (cand.get("content") or {}).get("parts") or []
        except (ValueError, AttributeError, IndexError) as exc:
            raise ProviderError(ProviderErrorKind.MALFORMED, resp.text[:200], provider="gemini", model=model) from exc
        text_parts: list[str] = []
        calls: list[ToolCall] = []
        for p in parts:
            if "text" in p and not p.get("thought"):
                text_parts.append(str(p["text"]))
            if "functionCall" in p:
                fc = p["functionCall"]
                args = fc.get("args") or {}
                calls.append(ToolCall(id=new_id("call"), name=str(fc.get("name", "")), arguments=args, raw_arguments=json.dumps(args)))
        finish = str(cand.get("finishReason") or "")
        if not parts and finish in ("SAFETY", "RECITATION", "PROHIBITED_CONTENT", "BLOCKLIST"):
            raise ProviderError(ProviderErrorKind.MALFORMED, f"response blocked ({finish})", provider="gemini", model=model)
        usage = data.get("usageMetadata") or {}
        return ChatResponse(
            content="".join(text_parts).strip(),
            tool_calls=calls,
            finish_reason="tool_calls" if calls else finish.lower(),
            usage=Usage(prompt_tokens=int(usage.get("promptTokenCount") or 0),
                        completion_tokens=int(usage.get("candidatesTokenCount") or 0)),
            provider="gemini",
            model=model,
            latency_ms=(time.perf_counter() - t0) * 1000.0,
        )

    async def list_models(self) -> list[str] | None:
        try:
            resp = await self._client.get("/models", params={"pageSize": 200}, timeout=15.0)
        except httpx.HTTPError as exc:
            raise ProviderError(ProviderErrorKind.TRANSIENT, f"cannot list models: {exc}", provider="gemini") from exc
        if resp.status_code >= 400:
            if resp.status_code == 400 and "API key not valid" in resp.text:
                raise ProviderError(ProviderErrorKind.AUTH, "invalid API key", provider="gemini", status=400)
            raise classify_http(resp.status_code, resp.text, "gemini", "", None)
        models = resp.json().get("models") or []
        return [str(m.get("name", "")).removeprefix("models/") for m in models]
