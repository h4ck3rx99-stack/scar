"""Streaming OpenAI-compatible chat client with tool calls.

Local llama-server is the default. If a cloud endpoint is configured (JARVIS_CLOUD_LLM_*), it is tried first
and any failure before the first token (rate limit, offline, bad key) silently falls back to local.
"""
import json
import logging

import httpx

from jarvis import config

log = logging.getLogger("llm")
_locks = {}


def _slot_lock(slot):
    import asyncio
    if slot not in _locks:
        _locks[slot] = asyncio.Lock()
    return _locks[slot]


class Endpoint:
    def __init__(self, name, url, model, key=None, local=False):
        self.name, self.url, self.model, self.key, self.local = name, url.rstrip("/"), model, key, local

    def headers(self):
        return {"Authorization": f"Bearer {self.key}"} if self.key else {}


class LLM:
    def __init__(self):
        self.client = httpx.AsyncClient(timeout=httpx.Timeout(30, connect=3))
        self.local = Endpoint("local", config.LLM_URL, config.LLM_MODEL, local=True)
        self.cloud = None
        if config.CLOUD_LLM_URL and config.CLOUD_LLM_KEY and config.CLOUD_LLM_MODEL:
            self.cloud = Endpoint("cloud", config.CLOUD_LLM_URL, config.CLOUD_LLM_MODEL, key=config.CLOUD_LLM_KEY)
            log.info("cloud LLM enabled: %s (%s), local fallback", config.CLOUD_LLM_URL, config.CLOUD_LLM_MODEL)

    async def stream(self, messages, tools=None, max_tokens=400):
        """Yields ("text", str) chunks as they arrive, then ("tool_calls", [...]) if the model called tools."""
        last_err = None
        for ep in [e for e in (self.cloud, self.local) if e]:
            started = False
            try:
                async for item in self._stream(ep, messages, tools, max_tokens):
                    started = True
                    yield item
                return
            except (httpx.HTTPError, ValueError, KeyError) as e:
                if started:
                    raise
                last_err = e
                log.warning("%s LLM failed before first token (%s); trying next", ep.name, e)
        raise RuntimeError(f"no LLM reachable: {last_err}")

    async def _stream(self, ep, messages, tools, max_tokens):
        body = {"model": ep.model, "messages": messages, "stream": True, "max_tokens": max_tokens, "temperature": 0.4}
        if tools:
            body["tools"] = tools
        if ep.local:
            body["chat_template_kwargs"] = {"enable_thinking": False}
            # Two llama.cpp slots: 0 keeps the big system-prompt + tool-list prefix cached for real turns; 1 takes the
            # side calls ("was that meant for me?", answer phrasing, vision, code) so they never evict it. llama.cpp
            # mixes up two requests pinned to the same busy slot, so each slot is used by one request at a time.
            body["id_slot"] = 0 if tools else 1
            body["cache_prompt"] = True
            async with _slot_lock(body["id_slot"]):
                async for item in self._post(ep, body):
                    yield item
            return
        async for item in self._post(ep, body):
            yield item

    async def _post(self, ep, body):
        calls = {}
        async with self.client.stream("POST", f"{ep.url}/chat/completions", json=body, headers=ep.headers()) as r:
            if r.status_code != 200:
                raise httpx.HTTPStatusError(f"HTTP {r.status_code}: {(await r.aread())[:200]!r}", request=r.request, response=r)
            async for line in r.aiter_lines():
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                choice = json.loads(data)["choices"]
                if not choice:
                    continue
                delta = choice[0].get("delta", {})
                if delta.get("content"):
                    yield "text", delta["content"]
                for tc in delta.get("tool_calls") or []:
                    slot = calls.setdefault(tc.get("index", 0), {"id": "", "name": "", "arguments": ""})
                    slot["id"] = tc.get("id") or slot["id"]
                    fn = tc.get("function") or {}
                    slot["name"] += fn.get("name") or ""
                    slot["arguments"] += fn.get("arguments") or ""
        if calls:
            out = []
            for i in sorted(calls):
                c = calls[i]
                try:
                    args = json.loads(c["arguments"] or "{}")
                except ValueError:
                    args = {}
                out.append({"id": c["id"] or f"call_{i}", "name": c["name"], "arguments": args, "raw": c["arguments"] or "{}"})
            yield "tool_calls", out

    async def prime(self, messages, tools):
        """Pre-process slot 0 up to exactly where the next user message will start.

        Qwen3.5 is a hybrid (attention + recurrent) model, so llama.cpp can only resume a cached prompt from a saved
        checkpoint, and it saves one a few tokens before the end of what it processed. Priming with the rendered
        prompt cut right at "<|im_start|>user\\n" puts that checkpoint inside the part every next question shares."""
        if self.local is None:
            return
        base = self.local.url.rsplit("/v1", 1)[0]
        mark = "\u0001NEXT"
        body = {"messages": [*messages, {"role": "user", "content": mark}], "tools": tools,
                "chat_template_kwargs": {"enable_thinking": False}}
        r = await self.client.post(f"{base}/apply-template", json=body)
        prompt = r.json().get("prompt", "")
        cut = prompt.find(mark)
        if cut <= 0:
            return
        async with _slot_lock(0):
            await self.client.post(f"{base}/completion", json={"prompt": prompt[:cut], "n_predict": 1, "id_slot": 0,
                                                              "cache_prompt": True, "temperature": 0})

    async def health(self):
        try:
            r = await self.client.get(config.LLM_URL.replace("/v1", "") + "/health", timeout=2)
            return r.status_code == 200
        except httpx.HTTPError:
            return False

    async def aclose(self):
        await self.client.aclose()
