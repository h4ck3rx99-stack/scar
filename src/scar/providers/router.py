"""Provider router with fallback (C6).

1. Map the task class to its category and required capabilities.
2. Order candidates by configured priority (free cloud first; local per policy).
3. Filter: credentials, circuit breaker, cooldown, privacy class, context size,
   and (for local) resource admission.
4. Try the best remaining candidate.
5. On failure classify the error and act: fall through, cool down, retry once,
   remove the model, repair once, or compact once.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import random
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

import structlog

from scar.config.settings import Settings
from scar.core.events import EventBus, ProviderFallback
from scar.core.types import TaskState
from scar.providers.base import ChatClient, ChatRequest, ChatResponse
from scar.providers.capabilities import CHAT_CATEGORIES, Candidate, Catalog, ProviderSpec, ordered_candidates
from scar.providers.errors import AllProvidersFailed, ProviderError, ProviderErrorKind
from scar.providers.health import HealthTracker
from scar.providers.llm.anthropic import AnthropicClient
from scar.providers.llm.gemini import GeminiClient
from scar.providers.llm.ollama import OllamaClient
from scar.providers.llm.openai_compat import OpenAICompatClient
from scar.providers.local.model_lifecycle import LocalModelManager
from scar.security.privacy import EgressTracker, PrivacyPolicy, Routing
from scar.security.secrets import SecretStore

log = structlog.get_logger("scar.router")

MODEL_LIST_TTL = 6 * 3600.0
Compactor = Callable[[ChatRequest], Awaitable[ChatRequest]]

# Only rate-limited candidates left: wait this long at most (once) rather than fail an interactive request.
RATE_LIMIT_MAX_WAIT_S = 30.0
_OTPM = re.compile(r"output tokens per minute[^:]*:\s*Limit (\d+), Requested (\d+)", re.I)


def _output_cap(err: ProviderError) -> int | None:
    """The max_tokens a provider will accept when it refused a request for asking too many output tokens."""
    if err.kind != ProviderErrorKind.RATE_LIMIT:
        return None
    m = _OTPM.search(str(err))
    return max(256, int(int(m.group(1)) * 0.9)) if m else None


@dataclass
class Attempt:
    provider: str
    model: str
    outcome: str
    latency_ms: float = 0.0


@dataclass
class RouteResult:
    response: ChatResponse
    attempts: list[Attempt] = field(default_factory=list)


class ProviderRouter:
    def __init__(
        self,
        settings: Settings,
        secrets: SecretStore,
        health: HealthTracker,
        bus: EventBus,
        privacy: PrivacyPolicy,
        egress: EgressTracker,
        local: LocalModelManager | None,
        catalog: Catalog | None = None,
        client_factory: Callable[[ProviderSpec, str], ChatClient] | None = None,
    ) -> None:
        self.settings = settings
        self.secrets = secrets
        self.health = health
        self.bus = bus
        self.privacy = privacy
        self.egress = egress
        self.local = local
        self.catalog = catalog or Catalog.load()
        self._client_factory = client_factory
        self._clients: dict[str, ChatClient] = {}
        self._model_lists: dict[str, tuple[float, list[str] | None]] = {}
        self._list_locks: dict[str, asyncio.Lock] = {}
        self.last_route: dict[str, str] = {}

    # ------------------------------------------------------------------ credentials & clients
    def credential_status(self, spec: ProviderSpec) -> tuple[bool, str]:
        if spec.local:
            return True, "local"
        for env in spec.requires_env:
            if not self.secrets.get_plain(env):
                return False, f"missing {env}"
        if not spec.api_key_env:
            return True, "no key needed"
        for env in spec.api_key_env:
            if self.secrets.has(env):
                return True, env
        return False, f"missing {' or '.join(spec.api_key_env)}"

    def credential_fingerprint(self, spec: ProviderSpec) -> str:
        """Short hash of the credentials in use, so a changed key is noticed (never logged or stored in clear)."""
        if spec.local:
            return ""
        values = [self.secrets.get_plain(e) or "" for e in (*spec.api_key_env, *spec.requires_env)]
        return hashlib.sha256("\x1f".join(values).encode()).hexdigest()[:12] if any(values) else ""

    def _base_url(self, spec: ProviderSpec) -> str:
        url = spec.base_url.replace("{ollama_url}", self.settings.ollama_url.rstrip("/"))
        url = url.replace("{local_llm_url}", self.settings.local_llm_url.rstrip("/"))
        for env in spec.requires_env:
            url = url.replace("{" + env + "}", self.secrets.get_plain(env) or "")
        return url

    def client(self, spec: ProviderSpec, base_url: str | None = None) -> ChatClient:
        key = f"{spec.id}|{base_url or ''}|{self.credential_fingerprint(spec)}"  # a new key gets a new client
        if key in self._clients:
            return self._clients[key]
        url = base_url or self._base_url(spec)
        if self._client_factory is not None:
            c = self._client_factory(spec, url)
        else:
            api_key = next((self.secrets.get(e) for e in spec.api_key_env if self.secrets.has(e)), None)
            if spec.kind == "gemini":
                c = GeminiClient(url, api_key)
            elif spec.kind == "anthropic":
                c = AnthropicClient(url, api_key)
            elif spec.kind == "ollama":
                c = OllamaClient(url, keep_alive_s=self.settings.model_idle_timeout)
            else:
                c = OpenAICompatClient(spec.id, url, api_key, headers=spec.headers)
        self._clients[key] = c
        return c

    async def aclose(self) -> None:
        for c in self._clients.values():
            with contextlib.suppress(Exception):
                await c.aclose()
        self._clients.clear()

    # ------------------------------------------------------------------ model resolution
    async def available_models(self, spec: ProviderSpec, client: ChatClient) -> list[str] | None:
        """Live /models list (cached). None means 'cannot list; assume configured models exist'."""
        if not spec.list_models:
            return None
        cached = self._model_lists.get(spec.id)
        if cached and time.time() - cached[0] < MODEL_LIST_TTL:
            return cached[1]
        lock = self._list_locks.setdefault(spec.id, asyncio.Lock())
        async with lock:
            cached = self._model_lists.get(spec.id)
            if cached and time.time() - cached[0] < MODEL_LIST_TTL:
                return cached[1]
            try:
                models = await asyncio.wait_for(client.list_models(), timeout=20)
            except ProviderError as err:
                if err.kind in (ProviderErrorKind.AUTH, ProviderErrorKind.UNAVAILABLE):
                    raise
                models = None
            except TimeoutError:
                models = None
            self._model_lists[spec.id] = (time.time(), models)
            return models

    def resolve_from_list(self, candidates: list[str], live: list[str] | None) -> list[str]:
        if live is None:
            return candidates
        live_set = set(live)
        return [m for m in candidates if m in live_set or m in ("local", "local-vlm")]

    # ------------------------------------------------------------------ routing
    async def chat(
        self,
        category: str,
        request: ChatRequest,
        *,
        data_classes: list[str] | None = None,
        task: TaskState | None = None,
        compactor: Compactor | None = None,
    ) -> ChatResponse:
        try:
            return await self._chat_once(category, request, data_classes=data_classes, task=task, compactor=compactor)
        except AllProvidersFailed as exc:
            waits = [a.retry_after for a in exc.attempts if a.kind == ProviderErrorKind.RATE_LIMIT and a.retry_after is not None]
            # a transient error was already retried once; it doesn't make waiting for a rate limit pointless
            usable = [a for a in exc.attempts if a.kind not in (ProviderErrorKind.UNAVAILABLE, ProviderErrorKind.AUTH,
                                                                ProviderErrorKind.PRIVACY, ProviderErrorKind.RATE_LIMIT,
                                                                ProviderErrorKind.TRANSIENT)]
            if waits and not usable and min(waits) <= RATE_LIMIT_MAX_WAIT_S:
                # only rate-limited candidates remain and the wait is short: sleep once, then retry
                await asyncio.sleep(min(waits) + 0.25)
                return await self._chat_once(category, request, data_classes=data_classes, task=task, compactor=compactor)
            raise

    async def _chat_once(
        self,
        category: str,
        request: ChatRequest,
        *,
        data_classes: list[str] | None,
        task: TaskState | None,
        compactor: Compactor | None,
    ) -> ChatResponse:
        if category not in CHAT_CATEGORIES:
            raise ValueError(f"not a chat category: {category}")
        classes = data_classes or ["general"]
        routing = self.privacy.most_restrictive(classes)
        attempts: list[ProviderError] = []
        tried: list[Attempt] = []
        previous: str | None = None
        compacted = False
        needs_vision = request.has_images or category == "vision"
        cands = ordered_candidates(self.catalog, category, self.settings)

        for cand in cands:
            spec = self.catalog.providers.get(cand.provider)
            if spec is None:
                continue
            ok, why = self.credential_status(spec)
            if not ok:
                attempts.append(ProviderError(ProviderErrorKind.UNAVAILABLE, why, provider=spec.id))
                continue
            fp = self.credential_fingerprint(spec)
            self.health.forget_if_credential_changed(spec.id, fp)
            if routing == Routing.LOCAL_ONLY and not spec.local:
                attempts.append(ProviderError(ProviderErrorKind.PRIVACY, f"{', '.join(classes)} must stay local", provider=spec.id))
                continue
            if needs_vision and cand.provider == "llamacpp" and not cand.vlm:
                continue
            try:
                base_url = await self._local_endpoint(cand, spec)
            except ProviderError as err:
                attempts.append(err)
                if err.kind != ProviderErrorKind.RESOURCE:
                    self.health.failure(err)
                log.info("provider_error", provider=spec.id, kind=err.kind.value, stage="start")
                previous = previous or spec.id
                continue
            client = self.client(spec, base_url)
            try:
                live = await self.available_models(spec, client)
            except ProviderError as err:
                self.health.failure(err, fp)
                attempts.append(err)
                log.info("provider_error", provider=spec.id, kind=err.kind.value, stage="models")
                previous = previous or spec.id  # the next candidate that answers is reported as a fallback
                continue
            models = self.resolve_from_list(cand.models, live)
            if not models:
                attempts.append(ProviderError(ProviderErrorKind.MODEL_UNAVAILABLE,
                                              f"none of {cand.models} offered", provider=spec.id))
                continue
            for model in models:
                h = self.health.get(spec.id, model)
                if not h.usable():
                    attempts.append(ProviderError(ProviderErrorKind.RATE_LIMIT if h.cooldown_until > time.time() else
                                                  ProviderErrorKind.UNAVAILABLE, h.last_error or h.state,
                                                  provider=spec.id, model=model))
                    continue
                if cand.local and cand.provider == "ollama":
                    decision = self._admit_ollama(cand)
                    if decision is not None:
                        attempts.append(decision)
                        break
                if previous is not None and previous != f"{spec.id}/{model}":
                    self.bus.publish(ProviderFallback(task_id=task.task_id if task else None, category=category,
                                                      from_provider=previous, to_provider=f"{spec.id}/{model}",
                                                      reason=str(attempts[-1].kind.value) if attempts else ""))
                previous = f"{spec.id}/{model}"
                outgoing = request if spec.local else self._redacted(request)
                result = await self._try(spec, model, client, outgoing, attempts, tried, compactor, compacted)
                if isinstance(result, ChatRequest):  # compacted request: retry same model once
                    compacted = True
                    request = result
                    outgoing = request if spec.local else self._redacted(request)
                    result = await self._try(spec, model, client, outgoing, attempts, tried, None, True)
                if isinstance(result, ChatResponse):
                    self._record(spec, model, result, classes, task, category)
                    return result
                last = attempts[-1] if attempts else None
                if last and last.kind in (ProviderErrorKind.AUTH, ProviderErrorKind.UNAVAILABLE, ProviderErrorKind.QUOTA):
                    break  # provider-level problem: skip its other models
        raise AllProvidersFailed(category, attempts)

    def _admit_ollama(self, cand: Candidate) -> ProviderError | None:
        """Local candidates are only reached after cloud ones (per policy ordering), so this is a fallback call."""
        if self.local is None:
            return None
        d = self.local.admission.local_inference(cand.size_mb or 4000.0, purpose="ollama", fallback=True)
        if not d.admitted:
            return ProviderError(ProviderErrorKind.RESOURCE, d.reason, provider="ollama")
        return None

    async def _local_endpoint(self, cand: Candidate | None, spec: ProviderSpec) -> str | None:
        if spec.kind != "llamacpp":
            return None
        if self.local is None:
            raise ProviderError(ProviderErrorKind.UNAVAILABLE, "local model manager not running", provider="llamacpp")
        return await self.local.ensure_llamacpp(vlm=bool(cand and cand.vlm), fallback=True)

    def _redacted(self, request: ChatRequest) -> ChatRequest:
        msgs = [m.model_copy(update={"content": self.privacy.prepare_for_cloud(m.content, "general")}) for m in request.messages]
        return request.model_copy(update={"messages": msgs})

    async def _try(
        self,
        spec: ProviderSpec,
        model: str,
        client: ChatClient,
        request: ChatRequest,
        attempts: list[ProviderError],
        tried: list[Attempt],
        compactor: Compactor | None,
        compacted: bool,
    ) -> ChatResponse | ChatRequest | None:
        for attempt in range(2):
            t0 = time.perf_counter()
            try:
                if self.local is not None and spec.kind == "llamacpp":
                    with self.local.serving("vlm" if model == "local-vlm" else "llm"):
                        resp = await client.chat(model, request)
                else:
                    resp = await client.chat(model, request)
                self.health.success(spec.id, model, resp.latency_ms or (time.perf_counter() - t0) * 1000)
                tried.append(Attempt(spec.id, model, "ok", resp.latency_ms))
                if self.local is not None:
                    if spec.kind == "llamacpp":
                        self.local.touch("vlm" if model == "local-vlm" else "llm")
                    elif spec.kind == "ollama":
                        self.local.ollama_used(model)
                return resp
            except ProviderError as err:
                err.provider = err.provider or spec.id
                err.model = err.model or model
                attempts.append(err)
                tried.append(Attempt(spec.id, model, err.kind.value, (time.perf_counter() - t0) * 1000))
                log.info("provider_error", provider=spec.id, model=model, kind=err.kind.value, retry_after=err.retry_after,
                         detail=str(err)[:300])
                cap = _output_cap(err)
                if cap is not None and attempt == 0 and request.max_tokens > cap:
                    # "Request too large … output tokens per minute (OTPM): Limit 1000, Requested 1374": waiting never
                    # helps; ask this model for fewer output tokens instead
                    request = request.model_copy(update={"max_tokens": cap})
                    continue
                if err.kind == ProviderErrorKind.TRANSIENT and attempt == 0:
                    self.health.failure(err)
                    await asyncio.sleep(0.5 + random.random())
                    continue
                if err.kind == ProviderErrorKind.CONTEXT_OVERFLOW:
                    if compactor is not None and not compacted:
                        return await compactor(request)
                    return None
                if err.kind == ProviderErrorKind.BAD_REQUEST and request.has_images:
                    return None  # this model rejects images: next candidate
                self.health.failure(err, self.credential_fingerprint(spec))
                return None
        return None

    def _record(self, spec: ProviderSpec, model: str, resp: ChatResponse, classes: list[str], task: TaskState | None,
                category: str) -> None:
        self.last_route[category] = f"{spec.id}/{model}"
        if not spec.local:
            self.egress.record(spec.id, classes)
        if task is not None:
            task.record_usage(spec.id, model, resp.usage.prompt_tokens, resp.usage.completion_tokens, ",".join(classes))

    def cloud_available(self, category: str) -> bool:
        """True if some non-local candidate for the category has credentials and is not cooling down."""
        for cand in ordered_candidates(self.catalog, category, self.settings):
            spec = self.catalog.providers.get(cand.provider)
            if spec is None or spec.local or not self.credential_status(spec)[0]:
                continue
            if not cand.models or any(self.health.get(spec.id, m).usable() for m in cand.models):
                return True
        return False

    # ------------------------------------------------------------------ diagnostics
    async def probe(self, provider_id: str, category: str = "reasoning") -> dict[str, Any]:
        """Doctor / `scar providers test`: credentials, reachability, model availability, tiny completion."""
        spec = self.catalog.providers.get(provider_id)
        if spec is None:
            return {"provider": provider_id, "ok": False, "detail": "unknown provider"}
        ok, why = self.credential_status(spec)
        if not ok:
            return {"provider": provider_id, "ok": False, "detail": why, "setup": spec.setup_doc}
        cand = next((c for c in self.catalog.categories.get(category, []) if c.provider == provider_id), None)
        try:
            base = await self._local_endpoint(cand, spec)
            client = self.client(spec, base)
            live = await self.available_models(spec, client)
        except ProviderError as err:
            return {"provider": provider_id, "ok": False, "detail": f"{err.kind.value}: {err}", "setup": spec.setup_doc}
        models = self.resolve_from_list(cand.models if cand else [], live)
        if not models:
            return {"provider": provider_id, "ok": False, "detail": f"none of the configured models are offered "
                    f"({', '.join(cand.models) if cand else '-'})", "live_models": (live or [])[:20]}
        from scar.providers.base import ChatMessage

        try:
            resp = await client.chat(models[0], ChatRequest(messages=[ChatMessage(role="user", content="Reply with the word OK.")],
                                                            max_tokens=16, temperature=0.0))
        except ProviderError as err:
            self.health.failure(err)
            return {"provider": provider_id, "ok": False, "model": models[0], "detail": f"{err.kind.value}: {str(err)[:160]}"}
        self.health.success(spec.id, models[0], resp.latency_ms)
        return {"provider": provider_id, "ok": True, "model": models[0], "latency_ms": round(resp.latency_ms),
                "reply": resp.content[:40]}
