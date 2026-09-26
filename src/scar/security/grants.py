"""Permission grants (C4.3).

Only the user can create grants: every mutating call requires a
``UserAuthority`` token, which is constructed exclusively by the approval
subsystem (after a user response) and by the ``scar permissions`` CLI. Tools,
model output and external content have no path to this API; a security test
asserts that no tool module imports it.
"""

from __future__ import annotations

import fnmatch
import json
import re
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

from scar.core.ids import new_id
from scar.core.types import RiskLevel
from scar.security.path_guard import is_within
from scar.security.risk import ActionFacts
from scar.storage.db import Database, now_iso


class GrantScope(StrEnum):
    ONCE = "once"
    TASK = "task"
    SESSION = "session"
    TIMED = "timed"
    PERSISTENT = "persistent"


class GrantKind(StrEnum):
    EXACT = "exact"  # tool + args hash
    TOOL = "tool"  # trusted tool (any args up to max_risk)
    APP = "app"
    RECIPIENT = "recipient"
    PATH = "path"
    DOMAIN = "domain"
    COMMAND = "command"  # command prefix


class UserAuthority:
    """Proof that a grant mutation originates from the user.

    Created only by ``ApprovalBroker`` (after a user response on an
    interactive channel) and by CLI permission commands.
    """

    __slots__ = ("channel", "detail")

    def __init__(self, channel: str, detail: str = "") -> None:
        if channel not in {"cli", "tui", "voice", "ipc-client", "approval"}:
            raise ValueError(f"not a user channel: {channel}")
        self.channel = channel
        self.detail = detail


class Grant(BaseModel):
    grant_id: str = Field(default_factory=lambda: new_id("grant"))
    effect: str = "allow"  # allow | deny
    kind: GrantKind
    tool: str | None = None
    match: dict[str, Any] = Field(default_factory=dict)
    args_hash: str | None = None
    scope: GrantScope
    task_id: str | None = None
    session_id: str | None = None
    max_risk: RiskLevel = RiskLevel.HIGH
    expires_at: datetime | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    revoked_at: datetime | None = None
    uses: int = 0
    created_via: str = "cli"

    def active(self, now: datetime, task_id: str | None, session_id: str | None) -> bool:
        if self.revoked_at is not None:
            return False
        if self.expires_at is not None and now >= self.expires_at:
            return False
        if self.scope == GrantScope.ONCE and self.uses >= 1:
            return False
        if self.scope in (GrantScope.TASK, GrantScope.ONCE) and self.task_id and task_id != self.task_id:
            return False
        return not (self.scope == GrantScope.SESSION and self.session_id and session_id != self.session_id)

    def matches(self, tool: str, args_hash: str, risk: RiskLevel, facts: ActionFacts) -> bool:
        if risk == RiskLevel.CRITICAL:
            return False  # CRITICAL can never be granted
        if self.effect == "allow" and risk > self.max_risk:
            return False
        if self.tool and not fnmatch.fnmatchcase(tool, self.tool):
            return False
        m = self.match
        if self.kind == GrantKind.EXACT:
            return self.args_hash == args_hash
        if self.kind == GrantKind.TOOL:
            return True
        if self.kind == GrantKind.APP:
            return bool(facts.app) and str(facts.app).lower() == str(m.get("app", "")).lower()
        if self.kind == GrantKind.RECIPIENT:
            want = str(m.get("recipient", "")).lower()
            return bool(facts.recipients) and all(r.lower() == want for r in facts.recipients)
        if self.kind == GrantKind.PATH:
            root = str(m.get("path", ""))
            return bool(facts.paths) and bool(root) and all(is_within(p, root) for p in facts.paths)
        if self.kind == GrantKind.DOMAIN:
            pattern = str(m.get("domain", "")).lower()
            return bool(facts.domains) and all(
                d == pattern or d.endswith("." + pattern) or fnmatch.fnmatchcase(d, pattern) for d in facts.domains
            )
        if self.kind == GrantKind.COMMAND:
            prefix = str(m.get("command_prefix", ""))
            cmd = (facts.command or "").strip()
            if not prefix or not cmd:
                return False
            # the prefix must match on a token boundary and the remainder may not chain further commands
            if not (cmd == prefix or cmd.startswith(prefix + " ")):
                return False
            return not re.search(r"[;&|`\n]|\$\(", cmd[len(prefix):])
        return False


class GrantStore:
    def __init__(self, db: Database, session_id: str) -> None:
        self.db = db
        self.session_id = session_id
        self._memory: dict[str, Grant] = {}  # once/task/session grants live only in memory

    # ---------------- mutations (user only) ----------------
    def create(self, grant: Grant, authority: UserAuthority) -> Grant:
        if not isinstance(authority, UserAuthority):  # pyright: ignore[reportUnnecessaryIsInstance] - runtime guard for untyped callers
            raise PermissionError("grants can only be created with user authority")
        grant.created_via = authority.channel
        if grant.scope == GrantScope.SESSION:
            grant.session_id = self.session_id
        if grant.scope in (GrantScope.TIMED, GrantScope.PERSISTENT):
            self.db.execute(
                "INSERT INTO grants(grant_id, effect, kind, tool, match_json, args_hash, scope, task_id, session_id, "
                "max_risk, expires_at, created_at, revoked_at, uses, created_via) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    grant.grant_id, grant.effect, grant.kind.value, grant.tool, json.dumps(grant.match), grant.args_hash,
                    grant.scope.value, grant.task_id, grant.session_id, grant.max_risk.name,
                    grant.expires_at.isoformat() if grant.expires_at else None, grant.created_at.isoformat(), None, 0,
                    grant.created_via,
                ),
            )
        else:
            self._memory[grant.grant_id] = grant
        return grant

    def revoke(self, grant_id: str, authority: UserAuthority) -> bool:
        if not isinstance(authority, UserAuthority):  # pyright: ignore[reportUnnecessaryIsInstance] - runtime guard for untyped callers
            raise PermissionError("grants can only be revoked with user authority")
        if grant_id in self._memory:
            del self._memory[grant_id]
            return True
        return self.db.execute(
            "UPDATE grants SET revoked_at = ? WHERE grant_id = ? AND revoked_at IS NULL", (now_iso(), grant_id)
        ) > 0

    def revoke_all(self, authority: UserAuthority) -> int:
        if not isinstance(authority, UserAuthority):  # pyright: ignore[reportUnnecessaryIsInstance] - runtime guard for untyped callers
            raise PermissionError("grants can only be revoked with user authority")
        n = len(self._memory)
        self._memory.clear()
        return n + self.db.execute("UPDATE grants SET revoked_at = ? WHERE revoked_at IS NULL", (now_iso(),))

    # ---------------- queries ----------------
    def _persistent(self) -> list[Grant]:
        rows = self.db.query("SELECT * FROM grants WHERE revoked_at IS NULL")
        out: list[Grant] = []
        for r in rows:
            out.append(
                Grant(
                    grant_id=r["grant_id"], effect=r["effect"], kind=GrantKind(r["kind"]), tool=r["tool"],
                    match=json.loads(r["match_json"] or "{}"), args_hash=r["args_hash"], scope=GrantScope(r["scope"]),
                    task_id=r["task_id"], session_id=r["session_id"], max_risk=RiskLevel[r["max_risk"]],
                    expires_at=datetime.fromisoformat(r["expires_at"]) if r["expires_at"] else None,
                    created_at=datetime.fromisoformat(r["created_at"]), uses=int(r["uses"]), created_via=r["created_via"],
                )
            )
        return out

    def list(self, include_inactive: bool = False) -> list[Grant]:
        now = datetime.now(UTC)
        grants = list(self._memory.values()) + self._persistent()
        if include_inactive:
            return grants
        return [g for g in grants if g.active(now, g.task_id, self.session_id)]

    def find(
        self, tool: str, args_hash: str, risk: RiskLevel, facts: ActionFacts, task_id: str | None
    ) -> tuple[Grant | None, Grant | None]:
        """Return (deny_grant, allow_grant) matching this action."""
        now = datetime.now(UTC)
        deny: Grant | None = None
        allow: Grant | None = None
        for g in list(self._memory.values()) + self._persistent():
            if not g.active(now, task_id, self.session_id):
                continue
            if g.effect == "deny":
                if g.kind == GrantKind.EXACT and g.args_hash != args_hash:
                    continue
                if g.matches(tool, args_hash, min(risk, RiskLevel.HIGH), facts):
                    deny = deny or g
            elif allow is None and g.matches(tool, args_hash, risk, facts):
                allow = g
        return deny, allow

    def consume(self, grant: Grant) -> None:
        grant.uses += 1
        if grant.grant_id in self._memory:
            if grant.scope == GrantScope.ONCE:
                del self._memory[grant.grant_id]
        else:
            self.db.execute("UPDATE grants SET uses = uses + 1 WHERE grant_id = ?", (grant.grant_id,))

    def clear_task(self, task_id: str) -> None:
        for gid, g in list(self._memory.items()):
            if g.task_id == task_id and g.scope in (GrantScope.ONCE, GrantScope.TASK):
                del self._memory[gid]


def timed_expiry(minutes: float) -> datetime:
    return datetime.now(UTC) + timedelta(minutes=minutes)
