"""Autonomy levels (C4.6) mapped to base policy decisions."""

from __future__ import annotations

from dataclasses import dataclass

from scar.core.types import PolicyDecision, RiskLevel

AUTONOMY_DESCRIPTIONS: dict[int, str] = {
    0: "Conversation only; no tool execution.",
    1: "Suggest actions and plans; no execution.",
    2: "Auto-execute LOW; ask for MEDIUM and above.",
    3: "Auto-execute LOW and MEDIUM; ask for HIGH; CRITICAL always needs confirmation.",
    4: "Auto-execute up to HIGH only where an explicit allow rule or grant matches; CRITICAL always needs confirmation.",
}


@dataclass(frozen=True)
class AutonomyDecision:
    decision: PolicyDecision
    reason: str


def autonomy_decision(level: int, risk: RiskLevel, *, allow_rule_or_grant: bool = False) -> AutonomyDecision:
    if level <= 0:
        return AutonomyDecision(PolicyDecision.DENY, "autonomy level 0: conversation only")
    if level == 1:
        return AutonomyDecision(PolicyDecision.DENY, "autonomy level 1: suggestions only, no execution")
    if risk == RiskLevel.CRITICAL:
        return AutonomyDecision(PolicyDecision.ASK, "CRITICAL actions always require explicit confirmation")
    if level == 2:
        if risk == RiskLevel.LOW:
            return AutonomyDecision(PolicyDecision.ALLOW, "LOW risk auto-allowed at level 2")
        return AutonomyDecision(PolicyDecision.ASK, f"{risk.name} risk needs approval at level 2")
    if level == 3:
        if risk <= RiskLevel.MEDIUM:
            return AutonomyDecision(PolicyDecision.ALLOW, f"{risk.name} risk auto-allowed at level 3")
        return AutonomyDecision(PolicyDecision.ASK, "HIGH risk needs approval at level 3")
    # level 4
    if risk <= RiskLevel.MEDIUM:
        return AutonomyDecision(PolicyDecision.ALLOW, f"{risk.name} risk auto-allowed at level 4")
    if allow_rule_or_grant:
        return AutonomyDecision(PolicyDecision.ALLOW, "HIGH risk allowed by explicit rule/grant at level 4")
    return AutonomyDecision(PolicyDecision.ASK, "HIGH risk without a matching allow rule needs approval")
