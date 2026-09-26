"""Provider error taxonomy (C6)."""

from __future__ import annotations

from enum import StrEnum

from scar.core.errors import ScarError


class ProviderErrorKind(StrEnum):
    UNAVAILABLE = "unavailable"  # missing credentials / not configured / server not running
    AUTH = "auth"
    RATE_LIMIT = "rate_limit"
    QUOTA = "quota"
    TRANSIENT = "transient"  # timeout / network / 5xx / outage
    MODEL_UNAVAILABLE = "model_unavailable"
    MALFORMED = "malformed"  # malformed response / invalid tool call
    CONTEXT_OVERFLOW = "context_overflow"
    PRIVACY = "privacy"  # data class not allowed to leave the machine
    RESOURCE = "resource"  # local admission refused
    BAD_REQUEST = "bad_request"


class ProviderError(ScarError):
    def __init__(
        self,
        kind: ProviderErrorKind,
        message: str,
        *,
        provider: str = "",
        model: str = "",
        retry_after: float | None = None,
        status: int | None = None,
    ) -> None:
        self.kind = kind
        self.provider = provider
        self.model = model
        self.retry_after = retry_after
        self.status = status
        super().__init__(f"{provider or 'provider'}{'/' + model if model else ''}: {kind.value}: {message}")


class AllProvidersFailed(ScarError):
    def __init__(self, category: str, attempts: list[ProviderError]) -> None:
        self.category = category
        self.attempts = attempts
        detail = "; ".join(f"{a.provider}: {a.kind.value}" for a in attempts[-6:]) or "no provider configured"
        super().__init__(f"no {category} provider available ({detail})")


def classify_http(status: int, body: str, provider: str, model: str, retry_after: float | None) -> ProviderError:
    low = body.lower()
    if status in (401, 403):
        if "quota" in low or "billing" in low or "credit" in low:
            return ProviderError(ProviderErrorKind.QUOTA, body[:300], provider=provider, model=model, status=status)
        return ProviderError(ProviderErrorKind.AUTH, body[:300], provider=provider, model=model, status=status)
    if status == 402:
        return ProviderError(ProviderErrorKind.QUOTA, body[:300], provider=provider, model=model, status=status)
    if status == 429:
        if any(w in low for w in ("quota", "exhausted", "per day", "daily", "limit reached for the day", "tokens per day",
                                  "requests per day", "billing", "insufficient")) and (retry_after is None or retry_after > 600):
            return ProviderError(ProviderErrorKind.QUOTA, body[:300], provider=provider, model=model, status=status,
                                 retry_after=retry_after)
        return ProviderError(ProviderErrorKind.RATE_LIMIT, body[:300], provider=provider, model=model, status=status,
                             retry_after=retry_after)
    if status == 404 or (status == 400 and ("model" in low and ("not found" in low or "does not exist" in low
                                                                 or "decommissioned" in low or "not supported" in low))):
        return ProviderError(ProviderErrorKind.MODEL_UNAVAILABLE, body[:300], provider=provider, model=model, status=status)
    if status in (400, 413, 422) and any(w in low for w in ("context", "too long", "maximum", "token limit", "too many tokens",
                                                              "reduce the length", "exceeds")):
        return ProviderError(ProviderErrorKind.CONTEXT_OVERFLOW, body[:300], provider=provider, model=model, status=status)
    if status >= 500 or status == 408:
        return ProviderError(ProviderErrorKind.TRANSIENT, body[:300], provider=provider, model=model, status=status,
                             retry_after=retry_after)
    return ProviderError(ProviderErrorKind.BAD_REQUEST, body[:300], provider=provider, model=model, status=status)
