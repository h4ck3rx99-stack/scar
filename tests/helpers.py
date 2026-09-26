"""Test doubles. These live only under tests/ and are never importable from src/scar (enforced by a test)."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from typing import Any

import numpy as np

from scar.core.ids import new_id
from scar.providers.base import ChatRequest, ChatResponse, ToolCall, Usage
from scar.providers.errors import ProviderError, ProviderErrorKind

Step = ChatResponse | ProviderError | Callable[[ChatRequest], ChatResponse | ProviderError]


def call(name: str, **args: Any) -> ToolCall:
    return ToolCall(id=new_id("call"), name=name.replace(".", "__"), arguments=args, raw_arguments=json.dumps(args))


def reply(*calls: ToolCall, content: str = "") -> ChatResponse:
    return ChatResponse(content=content, tool_calls=list(calls), finish_reason="tool_calls" if calls else "stop",
                        usage=Usage(prompt_tokens=100, completion_tokens=20), provider="scripted", model="scripted-1")


class ScriptedChatClient:
    """Returns a fixed sequence of responses (or raises scripted provider errors). Records every request."""

    def __init__(self, provider: str, steps: list[Step], *, models: list[str] | None = None,
                 list_error: ProviderError | None = None) -> None:
        self.provider = provider
        self.steps = list(steps)
        self.requests: list[tuple[str, ChatRequest]] = []
        self.models = models
        self.list_error = list_error

    async def chat(self, model: str, request: ChatRequest) -> ChatResponse:
        self.requests.append((model, request))
        if not self.steps:
            return reply(call("finish", summary="(script exhausted)", status="failed"))
        step = self.steps.pop(0)
        if callable(step) and not isinstance(step, ChatResponse | ProviderError):
            step = step(request)
        if isinstance(step, ProviderError):
            step.provider = step.provider or self.provider
            step.model = step.model or model
            raise step
        return step.model_copy(update={"provider": self.provider, "model": model})

    async def list_models(self) -> list[str] | None:
        if self.list_error is not None:
            raise self.list_error
        return self.models

    async def aclose(self) -> None:
        return None


def perr(kind: ProviderErrorKind, msg: str = "scripted", retry_after: float | None = None) -> ProviderError:
    return ProviderError(kind, msg, retry_after=retry_after)


class FakeEmbeddings:
    """Deterministic bag-of-words hashing embeddings (no model download)."""

    enabled = True
    model_name = "fake-hash-64"

    def embed_sync(self, texts: list[str]) -> np.ndarray:
        out = np.zeros((len(texts), 64), dtype=np.float32)
        for i, t in enumerate(texts):
            for w in t.lower().replace("\\", " ").replace("/", " ").split():
                h = int(hashlib.md5(w.strip(".,!?'\"").encode()).hexdigest(), 16)  # noqa: S324 - test hashing
                out[i, h % 64] += 1.0
            n = np.linalg.norm(out[i])
            if n:
                out[i] /= n
        return out

    async def embed(self, texts: list[str]) -> np.ndarray:
        return self.embed_sync(texts)


def install_scripted_router(services: Any, clients: dict[str, ScriptedChatClient], order: list[str] | None = None) -> None:
    """Point the router at scripted clients. ``order`` = provider ids tried for reasoning/fast/vision."""
    from scar.providers.capabilities import Candidate, ProviderSpec

    router = services.router
    order = order or list(clients)
    for pid in order:
        router.catalog.providers[pid] = ProviderSpec(id=pid, kind="scripted", tier="free", privacy="cloud")
    for cat in ("reasoning", "fast", "vision"):
        router.catalog.categories[cat] = [Candidate(provider=pid, models=[f"{pid}-model"]) for pid in order]
    router._client_factory = lambda spec, url: clients[spec.id]
    router._clients.clear()
    router._model_lists.clear()
