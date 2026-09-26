"""Project detection and test/build output parsing (C9.10)."""

from __future__ import annotations

import json
import re
import shutil
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class ProjectInfo:
    root: str
    kinds: list[str] = field(default_factory=list)
    package_manager: str | None = None
    test_command: list[str] | None = None
    build_command: list[str] | None = None
    lint_command: list[str] | None = None
    format_command: list[str] | None = None
    dev_command: list[str] | None = None
    install_command: list[str] | None = None
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _which(name: str) -> str:
    return shutil.which(name) or name


def python_for(root: Path) -> str:
    """Interpreter for a project: its own venv, else a system Python that has pytest, else SCAR's."""
    import subprocess
    import sys

    for venv in (root / ".venv", root / "venv", root / "env"):
        exe = venv / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
        if exe.exists():
            return str(exe)
    base = getattr(sys, "_base_executable", None) or sys.executable
    try:
        ok = subprocess.run([base, "-c", "import pytest"], capture_output=True, timeout=20,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).returncode == 0
    except (OSError, subprocess.SubprocessError):
        ok = False
    return base if ok else sys.executable


def detect_project(root: Path, preferred_pm: str | None = None) -> ProjectInfo:
    info = ProjectInfo(root=str(root))
    if (root / "pyproject.toml").exists() or (root / "setup.py").exists() or (root / "requirements.txt").exists() \
            or any(root.glob("test_*.py")) or ((root / "tests").is_dir() and any((root / "tests").glob("*.py"))):
        info.kinds.append("python")
        pm = preferred_pm or ("uv" if (root / "uv.lock").exists() else "poetry" if (root / "poetry.lock").exists()
                              else "uv" if (root / "pyproject.toml").exists() and shutil.which("uv") else "pip")
        info.package_manager = pm
        runner = ["uv", "run"] if pm == "uv" else ["poetry", "run"] if pm == "poetry" else []
        py = [] if runner else [python_for(root), "-m"]
        info.test_command = [*runner, "pytest", "-q", "-rfE", "--color=no"] if runner else [*py, "pytest", "-q", "-rfE", "--color=no"]
        info.lint_command = [*(runner or py), "ruff", "check", "."]
        info.format_command = [*(runner or py), "ruff", "format", "."]
        info.install_command = ["uv", "sync"] if pm == "uv" else ["poetry", "install"] if pm == "poetry" else \
            [python_for(root), "-m", "pip", "install", "-r", "requirements.txt"] if (root / "requirements.txt").exists() else None
    pkg = root / "package.json"
    if pkg.exists():
        info.kinds.append("node")
        pm = preferred_pm if preferred_pm in ("npm", "pnpm", "yarn", "bun") else (
            "pnpm" if (root / "pnpm-lock.yaml").exists() else "yarn" if (root / "yarn.lock").exists()
            else "bun" if (root / "bun.lockb").exists() or (root / "bun.lock").exists() else "npm")
        info.package_manager = info.package_manager or pm
        try:
            scripts = json.loads(pkg.read_text(encoding="utf-8")).get("scripts", {})
        except (json.JSONDecodeError, OSError):
            scripts = {}
        exe = _which(pm)
        if "test" in scripts:
            info.test_command = [exe, "test"] if pm != "npm" else [exe, "test", "--silent"]
        if "build" in scripts:
            info.build_command = [exe, "run", "build"]
        if "lint" in scripts:
            info.lint_command = [exe, "run", "lint"]
        if "format" in scripts:
            info.format_command = [exe, "run", "format"]
        for k in ("dev", "start", "serve"):
            if k in scripts:
                info.dev_command = [exe, "run", k]
                break
        info.install_command = [exe, "install"]
    if (root / "Cargo.toml").exists():
        info.kinds.append("rust")
        info.package_manager = info.package_manager or "cargo"
        info.test_command = info.test_command or ["cargo", "test"]
        info.build_command = info.build_command or ["cargo", "build"]
        info.lint_command = info.lint_command or ["cargo", "clippy"]
        info.format_command = info.format_command or ["cargo", "fmt"]
    if (root / "go.mod").exists():
        info.kinds.append("go")
        info.test_command = info.test_command or ["go", "test", "./..."]
        info.build_command = info.build_command or ["go", "build", "./..."]
        info.format_command = info.format_command or ["gofmt", "-w", "."]
    if any(root.glob("*.sln")) or any(root.glob("*.csproj")):
        info.kinds.append("dotnet")
        info.test_command = info.test_command or ["dotnet", "test"]
        info.build_command = info.build_command or ["dotnet", "build"]
    if (root / "pom.xml").exists():
        info.kinds.append("java-maven")
        info.test_command = info.test_command or [_which("mvn"), "test", "-q"]
        info.build_command = info.build_command or [_which("mvn"), "package", "-q"]
    if not info.kinds:
        info.notes.append("no known project files found")
    return info


@dataclass
class TestReport:
    passed: int = 0
    failed: int = 0
    errors: int = 0
    skipped: int = 0
    total: int = 0
    failures: list[dict[str, str]] = field(default_factory=list)
    framework: str = "unknown"
    parsed: bool = False

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _n(pattern: str, text: str) -> int:
    m = re.search(pattern, text)
    return int(m.group(1)) if m else 0


def parse_test_output(output: str) -> TestReport:
    r = TestReport()
    # pytest summary "== 2 failed, 5 passed, 1 skipped in 0.3s ==" or "-q" style "2 failed, 5 passed in 0.3s"
    m = re.search(r"(?m)^(?:=+ )?((?:\d+ (?:failed|passed|errors?|skipped|xfailed|xpassed|warnings?|deselected)(?:, )?)+) in [\d.]+s", output)
    if m:
        s = m.group(1)
        r.framework = "pytest"
        r.passed = _n(r"(\d+) passed", s)
        r.failed = _n(r"(\d+) failed", s)
        r.errors = _n(r"(\d+) errors?", s)
        r.skipped = _n(r"(\d+) skipped", s)
        for fm in re.finditer(r"(?m)^(FAILED|ERROR) (\S+?)(?: - (.*))?$", output):
            r.failures.append({"name": fm.group(2), "kind": fm.group(1).lower(), "message": (fm.group(3) or "")[:300]})
        r.parsed = True
    elif re.search(r"Tests:\s+.*total", output):  # jest / vitest-ish
        line = re.search(r"Tests:\s+(.*total)", output).group(1)  # type: ignore[union-attr]
        r.framework = "jest"
        r.passed, r.failed, r.skipped = _n(r"(\d+) passed", line), _n(r"(\d+) failed", line), _n(r"(\d+) skipped", line)
        for fm in re.finditer(r"(?m)^\s*●\s+(.+?)$", output):
            r.failures.append({"name": fm.group(1).strip()[:200], "kind": "failed", "message": ""})
        r.parsed = True
    elif re.search(r"Test Files\s+.*\n\s*Tests\s+", output):  # vitest
        line = re.search(r"(?m)^\s*Tests\s+(.*)$", output).group(1)  # type: ignore[union-attr]
        r.framework = "vitest"
        r.passed, r.failed = _n(r"(\d+) passed", line), _n(r"(\d+) failed", line)
        for fm in re.finditer(r"(?m)^\s*(?:×|FAIL)\s+(.+?)$", output):
            r.failures.append({"name": fm.group(1).strip()[:200], "kind": "failed", "message": ""})
        r.parsed = True
    elif "test result:" in output:  # cargo
        r.framework = "cargo"
        for tm in re.finditer(r"test result: \w+\. (\d+) passed; (\d+) failed; (\d+) ignored", output):
            r.passed += int(tm.group(1))
            r.failed += int(tm.group(2))
            r.skipped += int(tm.group(3))
        for fm in re.finditer(r"(?m)^test (\S+) \.\.\. FAILED$", output):
            r.failures.append({"name": fm.group(1), "kind": "failed", "message": ""})
        r.parsed = True
    elif re.search(r"(?m)^(ok|FAIL|---)\s", output) and ("--- FAIL" in output or re.search(r"(?m)^ok\s+\S+", output)):  # go
        r.framework = "go"
        r.failures = [{"name": f, "kind": "failed", "message": ""} for f in re.findall(r"--- FAIL: (\S+)", output)]
        r.failed = len(r.failures)
        r.passed = len(re.findall(r"--- PASS: ", output))
        r.parsed = True
    elif re.search(r"(Failed|Passed)!\s+-\s+Failed:\s+\d+", output):  # dotnet
        r.framework = "dotnet"
        r.failed = _n(r"Failed:\s+(\d+)", output)
        r.passed = _n(r"Passed:\s+(\d+)", output)
        r.skipped = _n(r"Skipped:\s+(\d+)", output)
        r.parsed = True
    elif re.search(r"(?m)^Ran (\d+) tests? in", output):  # unittest
        r.framework = "unittest"
        r.total = _n(r"Ran (\d+) tests?", output)
        r.failed = _n(r"failures=(\d+)", output)
        r.errors = _n(r"errors=(\d+)", output)
        r.skipped = _n(r"skipped=(\d+)", output)
        r.passed = r.total - r.failed - r.errors - r.skipped
        r.failures = [{"name": f"{m.group(2)} ({m.group(3)})", "kind": m.group(1).lower(), "message": ""}
                      for m in re.finditer(r"(?m)^(FAIL|ERROR): (\S+) \(([^)]+)\)", output)]
        r.parsed = True
    r.total = r.total or (r.passed + r.failed + r.errors + r.skipped)
    return r


_DIAG = re.compile(r"(?m)^(?P<file>[A-Za-z]?:?[^\s:()]+\.[A-Za-z0-9]{1,5})[:(](?P<line>\d+)(?:[:,](?P<col>\d+))?\)?:?\s*(?P<msg>.+)$")


def parse_diagnostics(output: str, limit: int = 100) -> list[dict[str, Any]]:
    """Generic file:line:col: message parser (compilers, linters, tsc, ruff, eslint unix format)."""
    out = []
    for m in _DIAG.finditer(output):
        out.append({"file": m.group("file"), "line": int(m.group("line")), "col": int(m.group("col") or 0),
                    "message": m.group("msg").strip()[:300]})
        if len(out) >= limit:
            break
    return out
