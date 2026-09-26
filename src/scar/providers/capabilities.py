"""Provider catalog: capability descriptors loaded from providers_default.yaml (+ user overrides)."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from scar.config import paths as cfg_paths
from scar.config.settings import Settings
from scar.core.errors import ConfigError

CHAT_CATEGORIES = ("reasoning", "fast", "vision")


@dataclass
class ProviderSpec:
    id: str
    kind: str
    base_url: str = ""
    api_key_env: list[str] = field(default_factory=list)
    requires_env: list[str] = field(default_factory=list)
    tier: str = "free"  # free | local | paid
    privacy: str = "cloud"  # cloud | local
    data_terms: str = ""
    setup_doc: str = "docs/providers.md"
    headers: dict[str, str] = field(default_factory=dict)
    list_models: bool = True
    optional_local: bool = False

    @property
    def local(self) -> bool:
        return self.privacy == "local"


@dataclass
class Candidate:
    provider: str
    models: list[str]
    local: bool = False
    size_mb: float = 0.0
    vlm: bool = False
    context_tokens: int = 128_000


@dataclass
class Catalog:
    providers: dict[str, ProviderSpec]
    categories: dict[str, list[Candidate]]

    @classmethod
    def load(cls, user_file: Path | None = None) -> Catalog:
        default = yaml.safe_load((cfg_paths.package_dir() / "providers_default.yaml").read_text(encoding="utf-8")) or {}
        path = user_file or cfg_paths.user_providers_file()
        user: dict[str, Any] = {}
        if path.exists():
            try:
                user = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            except yaml.YAMLError as exc:
                raise ConfigError(f"cannot parse {path}: {exc}") from exc
        prov_raw = {**(default.get("providers") or {}), **(user.get("providers") or {})}
        cat_raw = {**(default.get("categories") or {}), **(user.get("categories") or {})}
        providers: dict[str, ProviderSpec] = {}
        for pid, spec in prov_raw.items():
            try:
                providers[pid] = ProviderSpec(
                    id=pid,
                    kind=str(spec["kind"]),
                    base_url=str(spec.get("base_url", "")),
                    api_key_env=list(spec.get("api_key_env") or []),
                    requires_env=list(spec.get("requires_env") or []),
                    tier=str(spec.get("tier", "free")),
                    privacy=str(spec.get("privacy", "cloud")),
                    data_terms=str(spec.get("data_terms", "")),
                    setup_doc=str(spec.get("setup_doc", "docs/providers.md")),
                    headers=dict(spec.get("headers") or {}),
                    list_models=bool(spec.get("list_models", True)),
                    optional_local=bool(spec.get("optional_local", False)),
                )
            except KeyError as exc:
                raise ConfigError(f"provider {pid!r} is missing {exc}") from exc
        categories: dict[str, list[Candidate]] = {}
        for cat, entries in cat_raw.items():
            categories[cat] = [
                Candidate(
                    provider=str(e["provider"]),
                    models=[str(m) for m in e.get("models") or []],
                    local=bool(e.get("local", False)),
                    size_mb=float(e.get("size_mb", 0.0)),
                    vlm=bool(e.get("vlm", False)),
                    context_tokens=int(e.get("context_tokens", 128_000)),
                )
                for e in entries or []
            ]
        return cls(providers, categories)


def _parse_override(item: str) -> tuple[str, str | None]:
    provider, _, model = item.partition(":")
    # allow provider:model where model itself contains ':' (e.g. ollama qwen3:8b)
    return provider.strip(), model.strip() or None


def ordered_candidates(catalog: Catalog, category: str, settings: Settings) -> list[Candidate]:
    """Apply user overrides and the local-inference policy to a category's candidate list."""
    base = [Candidate(c.provider, list(c.models), c.local, c.size_mb, c.vlm, c.context_tokens)
            for c in catalog.categories.get(category, [])]
    preferred: list[tuple[str, str | None]] = []
    if category == "reasoning":
        if settings.llm_provider and settings.llm_provider != "auto":
            preferred.append((settings.llm_provider, settings.llm_model or None))
        preferred += [_parse_override(f) for f in settings.llm_fallbacks]
    elif category == "fast" and settings.fast_llm_provider and settings.fast_llm_provider != "auto":
        preferred.append((settings.fast_llm_provider, settings.fast_llm_model or None))
    elif category == "vision" and settings.vision_provider and settings.vision_provider != "auto":
        preferred.append((settings.vision_provider, settings.vision_model or None))
    front: list[Candidate] = []
    for provider, model in preferred:
        existing = next((c for c in base if c.provider == provider), None)
        spec = catalog.providers.get(provider)
        local = existing.local if existing else bool(spec and spec.local)
        models = [model] if model else (existing.models if existing else [])
        if model and existing:
            models = [model] + [m for m in existing.models if m != model]
        front.append(Candidate(provider, models, local, existing.size_mb if existing else 0.0,
                               existing.vlm if existing else False))
        if existing in base:
            base.remove(existing)
    ordered = front + base
    policy = settings.local_inference_policy
    if policy == "never":
        ordered = [c for c in ordered if not c.local]
    elif policy == "always":
        ordered = [c for c in ordered if c.local]
    elif policy == "prefer":
        ordered = [c for c in ordered if c.local] + [c for c in ordered if not c.local]
    else:  # fallback_only: local after all cloud candidates, unless the user explicitly put it first
        explicit = {p for p, _ in preferred}
        ordered = [c for c in ordered if not c.local or c.provider in explicit] + [
            c for c in ordered if c.local and c.provider not in explicit]
    return ordered
