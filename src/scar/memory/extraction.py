"""Rule-based extraction of durable facts from explicit 'remember' requests."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass


@dataclass
class Extracted:
    category: str
    text: str
    key: str | None = None
    alias: str | None = None
    alias_kind: str | None = None
    target: str | None = None
    preference: tuple[str, str] | None = None  # (key, value)


_PATH = r"([A-Za-z]:\\[^\n\"']+|\\\\[^\n\"']+|~[\\/][^\n\"']+|/[^\n\"']+)"


def extract(statement: str) -> Extracted:
    s = statement.strip().rstrip(".")
    s = re.sub(r"(?i)^(please\s+)?(remember|note|keep in mind|don't forget)\s+(that\s+)?", "", s).strip()
    # "my BISense folder is at C:\Projects\BISense" / "BISense is in C:\x"
    m = re.match(rf"(?i)^(?:my\s+|the\s+)?(.+?)\s+(?:folder|project|directory|repo)?\s*(?:is|lives|lies)\s+(?:at|in|under)\s+{_PATH}$", s)
    if m:
        name = m.group(1).strip()
        target = os.path.expandvars(m.group(2).strip())
        name = re.sub(r"(?i)\s+(folder|project|directory|repo)$", "", name)
        return Extracted("alias", f"folder alias '{name}' -> {target}", alias=name, alias_kind="folder", target=target)
    # "call C:\x my acceptance project" / "'work' means C:\x"
    m = re.match(rf"(?i)^['\"]?(.+?)['\"]?\s+(?:means|refers to|=)\s+{_PATH}$", s)
    if m:
        return Extracted("alias", f"folder alias '{m.group(1)}' -> {m.group(2)}", alias=m.group(1), alias_kind="folder",
                         target=m.group(2).strip())
    # "use pnpm in this project" / "prefer pnpm for BISense"
    m = re.match(r"(?i)^(?:always\s+)?(?:use|prefer)\s+(npm|pnpm|yarn|bun|uv|poetry|pip)\b(?:\s+(?:in|for)\s+(.+))?$", s)
    if m:
        return Extracted("project", f"use {m.group(1)} as the package manager", preference=("package_manager", m.group(1).lower()),
                         target=m.group(2))
    # "my preferred editor is VS Code"
    m = re.match(r"(?i)^my\s+(?:preferred|favou?rite|default)\s+(.+?)\s+is\s+(.+)$", s)
    if m:
        return Extracted("preference", f"preferred {m.group(1)}: {m.group(2)}", key=f"pref:{m.group(1).lower()}")
    # "my professor is Dr. Rao" -> contact alias hint handled by contacts; store as semantic fact
    return Extracted("semantic", s)
