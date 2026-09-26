"""Deterministic policy engine (C4.2).

Inputs: tool, capabilities, validated args + hash, computed risk (with facts),
autonomy level, task scope, taint, active grants, and policy rules. Output:
ALLOW / ASK / DENY with a human-readable reason. The LLM is not consulted.
"""

from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from scar.config import paths as cfg_paths
from scar.core.errors import ConfigError
from scar.core.types import PolicyDecision, RiskLevel, SideEffect
from scar.security.autonomy import autonomy_decision
from scar.security.grants import Grant, GrantStore
from scar.security.path_guard import normcase
from scar.security.risk import ActionFacts, RiskAssessment

HARD_DENY_KEYS = (
    "captcha_bypass",
    "disable_defender",
    "disable_firewall",
    "disable_uac",
    "credential_extraction",
    "exfiltrate_scar_secrets",
    "self_elevation",
    "clear_event_logs",
    "delete_shadow_copies",
    "kill_critical_process",
    "secret_paths",
)

# map command/path guard reasons onto hard-deny keys
_REASON_TO_KEY: list[tuple[str, str]] = [
    ("disables microsoft defender", "disable_defender"),
    ("disables the windows firewall", "disable_firewall"),
    ("disables uac", "disable_uac"),
    ("self-elevation", "self_elevation"),
    ("attempts elevation", "self_elevation"),
    ("credential extraction", "credential_extraction"),
    ("captcha", "captcha_bypass"),
    ("exfiltrates scar secrets", "exfiltrate_scar_secrets"),
    ("clears event logs", "clear_event_logs"),
    ("deletes shadow copies", "delete_shadow_copies"),
    ("system-critical process", "kill_critical_process"),
    ("secret", "secret_paths"),
    ("device", "secret_paths"),
]


def deny_key_for(reasons: list[str]) -> str | None:
    text = " ".join(reasons).lower()
    for needle, key in _REASON_TO_KEY:
        if needle in text:
            return key
    return None


@dataclass
class PolicyRule:
    id: str
    effect: PolicyDecision
    reason: str = ""
    tool: list[str] = field(default_factory=list)
    capability: list[str] = field(default_factory=list)
    min_risk: RiskLevel | None = None
    max_risk: RiskLevel | None = None
    path: list[str] = field(default_factory=list)
    command: list[re.Pattern[str]] = field(default_factory=list)
    app: list[str] = field(default_factory=list)
    recipient: list[str] = field(default_factory=list)
    domain: list[str] = field(default_factory=list)
    escalate_to: RiskLevel | None = None
    source: str = "default"

    def matches(self, tool: str, capabilities: list[str], risk: RiskLevel, facts: ActionFacts) -> bool:
        if self.tool and not any(fnmatch.fnmatchcase(tool, t) for t in self.tool):
            return False
        if self.capability and not any(fnmatch.fnmatchcase(c, p) for c in capabilities for p in self.capability):
            return False
        if self.min_risk is not None and risk < self.min_risk:
            return False
        if self.max_risk is not None and risk > self.max_risk:
            return False
        if self.path:
            if not facts.paths:
                return False
            if not all(any(fnmatch.fnmatch(normcase(p), normcase(g)) for g in self.path) for p in facts.paths):
                return False
        if self.command:
            cmd = (facts.command or "").strip()
            if not cmd or not any(rx.search(cmd) for rx in self.command):
                return False
            if self.effect == PolicyDecision.ALLOW and re.search(r"[;&|`\n]|\$\(", cmd):
                return False  # allow rules never cover chained commands
        if self.app and (not facts.app or facts.app.lower() not in [a.lower() for a in self.app]):
            return False
        if self.recipient:
            if not facts.recipients:
                return False
            if not all(any(fnmatch.fnmatch(r.lower(), g.lower()) for g in self.recipient) for r in facts.recipients):
                return False
        if self.domain:
            if not facts.domains:
                return False
            if not all(any(fnmatch.fnmatch(d.lower(), g.lower()) for g in self.domain) for d in facts.domains):
                return False
        return True


@dataclass
class PolicyInput:
    tool: str
    capabilities: list[str]
    args_hash: str
    assessment: RiskAssessment
    side_effects: SideEffect
    autonomy_level: int
    task_id: str | None
    tainted: list[str] = field(default_factory=list)
    out_of_scope: list[str] = field(default_factory=list)
    require_approval: bool = False
    internal: bool = False  # ask_user / finish / read_artifact


@dataclass
class PolicyOutcome:
    decision: PolicyDecision
    reason: str
    risk: RiskLevel
    critical_confirmation: bool = False
    grant: Grant | None = None
    grantable: bool = True
    matched_rules: list[str] = field(default_factory=list)


def _as_list(v: Any) -> list[str]:
    if v is None:
        return []
    if isinstance(v, list):
        return [str(x) for x in v]
    return [str(v)]


def _parse_rule(raw: dict[str, Any], source: str) -> PolicyRule:
    try:
        match = raw.get("match") or {}
        effect = PolicyDecision(str(raw["effect"]).lower())
        return PolicyRule(
            id=str(raw["id"]),
            effect=effect,
            reason=str(raw.get("reason", "")),
            tool=_as_list(match.get("tool")),
            capability=_as_list(match.get("capability")),
            min_risk=RiskLevel.parse(match["min_risk"]) if match.get("min_risk") else None,
            max_risk=RiskLevel.parse(match["max_risk"]) if match.get("max_risk") else None,
            path=_as_list(match.get("path")),
            command=[re.compile(p, re.IGNORECASE) for p in _as_list(match.get("command"))],
            app=_as_list(match.get("app")),
            recipient=_as_list(match.get("recipient")),
            domain=_as_list(match.get("domain")),
            escalate_to=RiskLevel.parse(raw["escalate_to"]) if raw.get("escalate_to") else None,
            source=source,
        )
    except (KeyError, ValueError, re.error) as exc:
        raise ConfigError(f"invalid policy rule in {source}: {raw!r}: {exc}") from exc


class PolicyEngine:
    def __init__(self, rules: list[PolicyRule], hard_deny: dict[str, bool], grants: GrantStore | None) -> None:
        self.rules = rules
        self.hard_deny = hard_deny
        self.grants = grants

    @classmethod
    def load(cls, grants: GrantStore | None, user_file: Path | None = None) -> PolicyEngine:
        default_file = cfg_paths.package_dir() / "policy_default.yaml"
        default = yaml.safe_load(default_file.read_text(encoding="utf-8")) or {}
        user_path = user_file or cfg_paths.user_policy_file()
        user: dict[str, Any] = {}
        if user_path.exists():
            try:
                user = yaml.safe_load(user_path.read_text(encoding="utf-8")) or {}
            except yaml.YAMLError as exc:
                raise ConfigError(f"cannot parse {user_path}: {exc}") from exc
        hard = {k: True for k in HARD_DENY_KEYS}
        hard.update({k: bool(v) for k, v in (default.get("hard_deny") or {}).items()})
        hard.update({k: bool(v) for k, v in (user.get("hard_deny") or {}).items()})
        rules = [_parse_rule(r, str(user_path)) for r in (user.get("rules") or [])]
        rules += [_parse_rule(r, "policy_default.yaml") for r in (default.get("rules") or [])]
        return cls(rules, hard, grants)

    def validate(self) -> list[str]:
        problems: list[str] = []
        ids = [r.id for r in self.rules]
        dupes = {i for i in ids if ids.count(i) > 1}
        if dupes:
            problems.append(f"duplicate rule ids: {sorted(dupes)}")
        unknown = set(self.hard_deny) - set(HARD_DENY_KEYS)
        if unknown:
            problems.append(f"unknown hard_deny keys: {sorted(unknown)}")
        return problems

    def decide(self, inp: PolicyInput) -> PolicyOutcome:
        a = inp.assessment
        risk = a.level

        # 0. internal control tools (ask_user, finish, read_artifact) are always allowed
        if inp.internal:
            return PolicyOutcome(PolicyDecision.ALLOW, "internal control tool", risk)

        # 1. hard floors from path/command guards
        if a.floor == PolicyDecision.DENY:
            key = a.deny_key or deny_key_for(a.reasons)
            if key is None or self.hard_deny.get(key, True):
                return PolicyOutcome(PolicyDecision.DENY, "; ".join(a.reasons) or "denied by guard", RiskLevel.CRITICAL, grantable=False)
            risk = RiskLevel.CRITICAL  # user disabled this hard-deny by hand: still needs typed confirmation

        # 2. policy rules: escalation first, then DENY beats everything
        matched: list[PolicyRule] = []
        for rule in self.rules:
            if rule.matches(inp.tool, inp.capabilities, risk, a.facts):
                matched.append(rule)
                if rule.escalate_to is not None and rule.escalate_to > risk:
                    risk = rule.escalate_to
        ids = [r.id for r in matched]
        for rule in matched:
            if rule.effect == PolicyDecision.DENY:
                return PolicyOutcome(PolicyDecision.DENY, rule.reason or f"denied by rule {rule.id}", risk, grantable=False, matched_rules=ids)

        # 3. user "always deny" grants
        deny_grant, allow_grant = (None, None)
        if self.grants is not None:
            deny_grant, allow_grant = self.grants.find(inp.tool, inp.args_hash, risk, a.facts, inp.task_id)
        if deny_grant is not None:
            return PolicyOutcome(PolicyDecision.DENY, "you chose to always deny this", risk, grant=deny_grant, matched_rules=ids)

        # 4. autonomy 0/1 never execute
        if inp.autonomy_level <= 1:
            ad = autonomy_decision(inp.autonomy_level, risk)
            return PolicyOutcome(ad.decision, ad.reason, risk, grantable=False, matched_rules=ids)

        # 5. CRITICAL: always fresh typed confirmation, never grants
        if risk == RiskLevel.CRITICAL:
            reason = "; ".join(a.reasons[:3]) or "critical action"
            return PolicyOutcome(PolicyDecision.ASK, f"CRITICAL: {reason}", risk, critical_confirmation=True, grantable=False, matched_rules=ids)

        # 6. taint and scope: ASK even when a grant exists
        if inp.tainted:
            return PolicyOutcome(
                PolicyDecision.ASK, "arguments come from external content: " + "; ".join(inp.tainted[:3]), risk,
                grantable=False, matched_rules=ids,
            )
        if inp.out_of_scope:
            return PolicyOutcome(
                PolicyDecision.ASK, "outside the request's scope: " + "; ".join(inp.out_of_scope[:3]), risk,
                grantable=False, matched_rules=ids,
            )

        # 7. user grants satisfy approval requirements
        if allow_grant is not None:
            return PolicyOutcome(PolicyDecision.ALLOW, f"allowed by your {allow_grant.scope.value} permission", risk, grant=allow_grant, matched_rules=ids)

        # 8. explicit ask rules
        for rule in matched:
            if rule.effect == PolicyDecision.ASK:
                return PolicyOutcome(PolicyDecision.ASK, rule.reason or f"rule {rule.id} requires approval", risk, matched_rules=ids)

        allow_rule = any(r.effect == PolicyDecision.ALLOW for r in matched)

        # 9. global require-approval switch
        if inp.require_approval and inp.side_effects != SideEffect.NONE:
            return PolicyOutcome(PolicyDecision.ASK, "approval required for every action with side effects", risk, matched_rules=ids)

        # 10. autonomy mapping
        if allow_rule and risk <= RiskLevel.HIGH and inp.autonomy_level >= 2:
            if risk <= RiskLevel.MEDIUM or inp.autonomy_level >= 4:
                return PolicyOutcome(PolicyDecision.ALLOW, f"allowed by policy rule {next(r.id for r in matched if r.effect == PolicyDecision.ALLOW)}", risk, matched_rules=ids)
        ad = autonomy_decision(inp.autonomy_level, risk, allow_rule_or_grant=allow_rule)
        return PolicyOutcome(ad.decision, ad.reason, risk, matched_rules=ids)
