"""Web search chain: Brave → Tavily → SearXNG → ddgs (unofficial) → cache → unavailable."""

from __future__ import annotations

import asyncio
import time
from collections import OrderedDict
from typing import Any

import httpx
import structlog

from scar.config.settings import Settings
from scar.core.events import EventBus, ProviderFallback
from scar.providers.base import SearchHit
from scar.providers.capabilities import Catalog
from scar.providers.errors import AllProvidersFailed, ProviderError, ProviderErrorKind, classify_http
from scar.providers.health import HealthTracker
from scar.providers.ratelimit import parse_retry_after
from scar.security.privacy import EgressTracker
from scar.security.secrets import SecretStore

log = structlog.get_logger("scar.search")

CACHE_TTL = 3600.0
CACHE_MAX = 200


class SearchService:
    def __init__(self, settings: Settings, secrets: SecretStore, health: HealthTracker, bus: EventBus,
                 egress: EgressTracker, catalog: Catalog | None = None,
                 transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.settings = settings
        self.secrets = secrets
        self.health = health
        self.bus = bus
        self.egress = egress
        self.catalog = catalog or Catalog.load()
        self._cache: OrderedDict[str, tuple[float, str, list[SearchHit]]] = OrderedDict()
        self._transport = transport
        self.last_provider = ""

    def _chain(self) -> list[str]:
        chain = [c.provider for c in self.catalog.categories.get("search", [])]
        pref = self.settings.search_provider
        if pref and pref != "auto":
            chain.sort(key=lambda p: 0 if p == pref else 1)
        return chain

    def configured(self, provider: str) -> tuple[bool, str]:
        if provider == "brave":
            return (self.secrets.has("BRAVE_API_KEY"), "BRAVE_API_KEY")
        if provider == "tavily":
            return (self.secrets.has("TAVILY_API_KEY"), "TAVILY_API_KEY")
        if provider == "searxng":
            return (bool(self.settings.searxng_url), "SCAR_SEARXNG_URL")
        if provider == "ddgs":
            try:
                import ddgs  # noqa: F401
            except ImportError:
                return False, "ddgs package"
            return True, ""
        return False, "unknown provider"

    async def search(self, query: str, max_results: int = 8) -> tuple[str, list[SearchHit]]:
        key = f"{query.strip().lower()}|{max_results}"
        attempts: list[ProviderError] = []
        previous = ""
        for provider in self._chain():
            ok, need = self.configured(provider)
            if not ok:
                attempts.append(ProviderError(ProviderErrorKind.UNAVAILABLE, f"missing {need}", provider=provider))
                continue
            if not self.health.get(provider, "search").usable():
                continue
            if previous:
                self.bus.publish(ProviderFallback(category="search", from_provider=previous, to_provider=provider,
                                                  reason=attempts[-1].kind.value if attempts else ""))
            t0 = time.perf_counter()
            try:
                hits = await self._search(provider, query, max_results)
            except ProviderError as err:
                err.provider = provider
                self.health.failure(err)
                attempts.append(err)
                previous = provider
                continue
            self.health.success(provider, "search", (time.perf_counter() - t0) * 1000)
            self.egress.record(provider, ["general"])
            self.last_provider = provider
            self._cache[key] = (time.time(), provider, hits)
            self._cache.move_to_end(key)
            while len(self._cache) > CACHE_MAX:
                self._cache.popitem(last=False)
            return provider, hits
        cached = self._cache.get(key)
        if cached is not None:
            self.last_provider = f"cache({cached[1]})"
            return f"cache:{cached[1]}", cached[2]
        raise AllProvidersFailed("search", attempts)

    def cached(self, query: str, max_results: int = 8) -> list[SearchHit] | None:
        hit = self._cache.get(f"{query.strip().lower()}|{max_results}")
        if hit and time.time() - hit[0] < CACHE_TTL:
            return hit[2]
        return None

    async def _get_json(self, url: str, provider: str, **kwargs: Any) -> Any:
        try:
            async with httpx.AsyncClient(timeout=15.0, transport=self._transport) as c:
                r = await c.request(kwargs.pop("method", "GET"), url, **kwargs)
        except httpx.HTTPError as exc:
            raise ProviderError(ProviderErrorKind.TRANSIENT, str(exc), provider=provider) from exc
        if r.status_code >= 400:
            raise classify_http(r.status_code, r.text, provider, "search", parse_retry_after(r.headers, r.text))
        try:
            return r.json()
        except ValueError as exc:
            raise ProviderError(ProviderErrorKind.MALFORMED, r.text[:200], provider=provider) from exc

    async def _search(self, provider: str, query: str, n: int) -> list[SearchHit]:
        if provider == "brave":
            key = self.secrets.get("BRAVE_API_KEY")
            assert key is not None
            data = await self._get_json("https://api.search.brave.com/res/v1/web/search", "brave",
                                        params={"q": query, "count": min(n, 20)},
                                        headers={"X-Subscription-Token": key.get_secret_value(), "Accept": "application/json"})
            results = (data.get("web") or {}).get("results") or []
            return [SearchHit(title=str(r.get("title", "")), url=str(r.get("url", "")),
                              snippet=str(r.get("description", "")), source="brave") for r in results[:n]]
        if provider == "tavily":
            key = self.secrets.get("TAVILY_API_KEY")
            assert key is not None
            data = await self._get_json("https://api.tavily.com/search", "tavily", method="POST",
                                        json={"query": query, "max_results": min(n, 20), "search_depth": "basic"},
                                        headers={"Authorization": f"Bearer {key.get_secret_value()}"})
            return [SearchHit(title=str(r.get("title", "")), url=str(r.get("url", "")),
                              snippet=str(r.get("content", ""))[:500], source="tavily") for r in (data.get("results") or [])[:n]]
        if provider == "searxng":
            data = await self._get_json(self.settings.searxng_url.rstrip("/") + "/search", "searxng",
                                        params={"q": query, "format": "json"})
            return [SearchHit(title=str(r.get("title", "")), url=str(r.get("url", "")),
                              snippet=str(r.get("content", "")), source="searxng") for r in (data.get("results") or [])[:n]]
        if provider == "ddgs":
            def run() -> list[dict[str, Any]]:
                from ddgs import DDGS

                return list(DDGS(timeout=15).text(query, max_results=n) or [])

            try:
                results = await asyncio.wait_for(asyncio.to_thread(run), timeout=30)
            except TimeoutError as exc:
                raise ProviderError(ProviderErrorKind.TRANSIENT, "ddgs timed out", provider="ddgs") from exc
            except Exception as exc:  # noqa: BLE001 - unofficial scraper raises many error types
                msg = str(exc)
                kind = ProviderErrorKind.RATE_LIMIT if "ratelimit" in msg.lower() or "202" in msg else ProviderErrorKind.TRANSIENT
                raise ProviderError(kind, msg[:200], provider="ddgs") from exc
            return [SearchHit(title=str(r.get("title", "")), url=str(r.get("href") or r.get("url") or ""),
                              snippet=str(r.get("body", "")), source="ddgs") for r in results[:n]]
        raise ProviderError(ProviderErrorKind.UNAVAILABLE, f"unknown search provider {provider}", provider=provider)
