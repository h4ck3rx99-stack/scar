"""Write THIRD_PARTY_NOTICES.md: every component the installed app ships, with its license.

    uv run python scripts/gen_notices.py

Python packages come from the locked runtime set (installed on first run), JavaScript from the frontend's production
dependencies (bundled), Rust crates from the desktop shell. The build fails if a GPL/AGPL component appears without a
recorded decision (docs/adr/0016-licenses.md).
"""

from __future__ import annotations

import importlib.metadata as md
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# license metadata missing upstream, checked by hand against the project repositories
KNOWN = {"py-rust-stemmers": "MIT", "windows-toasts": "Apache-2.0", "tld": "MPL-1.1 (chosen; the package is tri-licensed)"}
ALLOWED_COPYLEFT = {"edge-tts", "fpdf2", "pynput"}  # LGPL, dynamically loaded and replaceable (ADR 0016)


def python_rows() -> list[tuple[str, str, str]]:
    req = subprocess.run(["uv", "export", "--frozen", "--no-dev", "--no-emit-project", "--format", "requirements-txt",
                          "--no-hashes"], capture_output=True, text=True, encoding="utf-8", cwd=ROOT, check=True).stdout
    rows = []
    for line in req.splitlines():
        if "==" not in line or line.startswith("#"):
            continue
        name, rest = line.split("==", 1)
        version = rest.split(";")[0].strip()
        try:
            m = md.metadata(name.strip())
        except md.PackageNotFoundError:
            continue  # platform-specific (Linux/macOS) dependency, not shipped on Windows
        lic = KNOWN.get(name.strip()) or m.get("License-Expression") or ""
        if not lic or len(lic) > 60:
            cls = [c.split("::")[-1].strip() for c in (m.get_all("Classifier") or []) if c.startswith("License")]
            lic = ", ".join(cls) or (lic[:60] if lic else "see package")
        rows.append((name.strip(), version, lic))
    return rows


def js_rows() -> list[tuple[str, str, str]]:
    out = subprocess.run("pnpm licenses list --prod --json", shell=True, capture_output=True, text=True, encoding="utf-8",
                         cwd=ROOT / "app", check=True).stdout
    rows = []
    for lic, pkgs in json.loads(out).items():
        for p in pkgs:
            for v in p.get("versions", [p.get("version", "")]):
                rows.append((p["name"], v, lic))
    return rows


def rust_rows() -> list[tuple[str, str, str]]:
    out = subprocess.run(["cargo", "metadata", "--format-version", "1"], capture_output=True, text=True, encoding="utf-8",
                         cwd=ROOT / "app" / "src-tauri", check=True).stdout
    return [(p["name"], p["version"], p.get("license") or "see crate") for p in json.loads(out)["packages"]
            if p["name"] != "scar-desktop"]


def main() -> None:
    sections = [("Python runtime (installed on first run)", python_rows()), ("Desktop frontend (bundled)", js_rows()),
                ("Desktop shell (Rust, compiled in)", rust_rows())]
    bad = [(n, lic) for _, rows in sections for n, _v, lic in rows
           if ("GPL" in lic.upper() and "LGPL" not in lic.upper() and " OR " not in lic.upper() and "(chosen" not in lic)
           or ("LGPL" in lic.upper() and " OR " not in lic.upper() and n not in ALLOWED_COPYLEFT and "Rust" not in n)]
    if bad:
        print("copyleft components without a recorded decision:", bad)
        sys.exit(1)
    lines = ["# Third-party notices", "",
             "SCAR is MIT-licensed. It ships or installs the components below under their own licenses. LGPL components "
             "(edge-tts, fpdf2, pynput) are separate, replaceable Python packages loaded at run time.", ""]
    for title, rows in sections:
        lines += [f"## {title}", "", "| Component | Version | License |", "|---|---|---|"]
        lines += [f"| {n} | {v} | {lic} |" for n, v, lic in sorted(set(rows), key=lambda r: r[0].lower())]
        lines.append("")
    (ROOT / "THIRD_PARTY_NOTICES.md").write_text("\n".join(lines), encoding="utf-8")
    print(f"wrote THIRD_PARTY_NOTICES.md ({sum(len(r) for _, r in sections)} components)")


if __name__ == "__main__":
    main()
