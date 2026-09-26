"""Scope anchoring (C4.9).

The task's verbatim objective (plus the user's clarifications) is the scope.
External communication may only target recipients the user named or
explicitly confirmed, and sensitive capabilities that the objective gives no
reason for are flagged as out of scope (policy then asks).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# capability prefix -> intent words that make the capability in-scope
_INTENTS: dict[str, tuple[str, ...]] = {
    "comms.send": ("send", "email", "e-mail", "mail", "message", "text", "tell", "reply", "forward", "notify", "dm",
                   "ping", "whatsapp", "telegram", "discord", "share", "let", "inform", "post", "write to"),
    "fs.delete": ("delete", "remove", "clean", "trash", "erase", "get rid", "clear", "purge", "tidy", "uninstall", "prune"),
    "process.kill": ("kill", "stop", "close", "end", "terminate", "quit", "exit", "restart", "shut"),
    "git.push": ("push", "publish", "upload", "ship", "deploy", "sync", "pr", "pull request"),
    "system.settings": ("setting", "configure", "change", "set", "enable", "disable", "turn", "adjust", "volume",
                        "brightness", "wifi", "bluetooth"),
    "system.install": ("install", "setup", "set up", "add", "upgrade", "update", "download"),
    "calendar.invite": ("invite", "meeting", "schedule", "calendar", "event", "book"),
    "calendar.delete": ("cancel", "delete", "remove", "clear", "reschedule", "move"),
    "browser.submit": ("submit", "fill", "form", "sign", "register", "post", "comment", "search", "log", "book",
                       "order", "apply", "send"),
    "finance": ("buy", "purchase", "pay", "order", "checkout", "transfer", "subscribe", "donate"),
}


@dataclass
class ScopeResult:
    in_scope: bool
    reasons: list[str] = field(default_factory=list)


def _norm(t: str) -> str:
    return re.sub(r"\s+", " ", t.casefold()).strip()


class ScopeAnchor:
    def __init__(self, objective: str) -> None:
        self.objective = objective
        self._user_texts: list[str] = [_norm(objective)]
        self._confirmed_recipients: set[str] = set()

    def add_user_text(self, text: str) -> None:
        if text:
            self._user_texts.append(_norm(text))

    def confirm_recipient(self, identity: str) -> None:
        self._confirmed_recipients.add(_norm(identity))

    @property
    def user_text(self) -> str:
        return " \n ".join(self._user_texts)

    def mentions(self, needle: str) -> bool:
        n = _norm(needle)
        if len(n) < 2:
            return False
        pattern = re.compile(r"(?<![\w@.])" + re.escape(n) + r"(?![\w@])")
        return any(pattern.search(t) for t in self._user_texts)

    def recipient_in_scope(self, address: str, names: list[str] | None = None) -> ScopeResult:
        """A recipient is in scope if the user named it (address, name, alias) or confirmed it."""
        if _norm(address) in self._confirmed_recipients:
            return ScopeResult(True, ["recipient confirmed by user"])
        if self.mentions(address):
            return ScopeResult(True, ["recipient address given by user"])
        for name in names or []:
            if not name:
                continue
            if _norm(name) in self._confirmed_recipients:
                return ScopeResult(True, [f"recipient {name!r} confirmed by user"])
            if self.mentions(name):
                return ScopeResult(True, [f"user named {name!r}"])
            first = name.split()[0] if name.split() else ""
            if len(first) >= 3 and self.mentions(first):
                return ScopeResult(True, [f"user named {first!r}"])
        return ScopeResult(False, [f"recipient {address!r} was not named or confirmed by the user"])

    def capability_in_scope(self, capability: str) -> ScopeResult:
        for prefix, words in _INTENTS.items():
            if capability == prefix or capability.startswith(prefix + "."):
                text = self.user_text
                if any(re.search(r"\b" + re.escape(w) + r"", text) for w in words):
                    return ScopeResult(True)
                return ScopeResult(False, [f"objective gives no reason for {capability}"])
        return ScopeResult(True)
