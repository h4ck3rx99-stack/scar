"""Per-invocation risk assessment (C4.1).

Risk is computed from the tool, its arguments and its context, never as a
single static number per tool. Tools contribute a ``RiskAssessment`` through
their ``risk_fn``; the pipeline then layers on taint and scope escalations.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from scar.core.types import PolicyDecision, RiskLevel


@dataclass
class ActionFacts:
    """Normalised facts about an action, used for policy rules and grants."""

    paths: list[str] = field(default_factory=list)
    command: str | None = None
    app: str | None = None
    recipients: list[str] = field(default_factory=list)
    recipient_names: list[str] = field(default_factory=list)
    domains: list[str] = field(default_factory=list)
    file_count: int = 0
    data_classes: list[str] = field(default_factory=list)
    external_destination: bool = False


@dataclass
class RiskAssessment:
    level: RiskLevel
    reasons: list[str] = field(default_factory=list)
    floor: PolicyDecision | None = None  # DENY floor from path/command guards
    deny_key: str | None = None  # hard-deny category (see policy.HARD_DENY_KEYS)
    facts: ActionFacts = field(default_factory=ActionFacts)

    def raise_to(self, level: RiskLevel, reason: str) -> None:
        if level > self.level:
            self.level = level
        if reason and reason not in self.reasons:
            self.reasons.append(reason)

    def deny(self, reason: str, key: str | None = None) -> None:
        self.floor = PolicyDecision.DENY
        self.level = RiskLevel.CRITICAL
        self.deny_key = key or self.deny_key
        if reason not in self.reasons:
            self.reasons.append(reason)

    def merge(self, other: RiskAssessment) -> None:
        self.raise_to(other.level, "")
        for r in other.reasons:
            if r not in self.reasons:
                self.reasons.append(r)
        if other.floor == PolicyDecision.DENY:
            self.floor = PolicyDecision.DENY
            self.deny_key = other.deny_key or self.deny_key
        f, o = self.facts, other.facts
        f.paths += [p for p in o.paths if p not in f.paths]
        f.recipients += [r for r in o.recipients if r not in f.recipients]
        f.recipient_names += [r for r in o.recipient_names if r not in f.recipient_names]
        f.domains += [d for d in o.domains if d not in f.domains]
        f.data_classes += [d for d in o.data_classes if d not in f.data_classes]
        f.command = f.command or o.command
        f.app = f.app or o.app
        f.file_count = max(f.file_count, o.file_count)
        f.external_destination = f.external_destination or o.external_destination


def bulk_escalation(assessment: RiskAssessment, count: int, threshold: int) -> None:
    if count > threshold:
        assessment.raise_to(assessment.level.escalate(1), f"bulk operation on {count} items (> {threshold})")
