"""PowerShell AST extraction via ``[System.Management.Automation.Language.Parser]::ParseInput``.

A single long-lived ``powershell -NoProfile -NonInteractive`` process runs a
parse-only loop: it reads base64 script text from stdin, parses it (executing
nothing from the input), and writes one JSON line describing every command,
its arguments, pipelines and parse errors. The process is restarted on
failure and stopped after an idle period.
"""

from __future__ import annotations

import base64
import json
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field

_PARSER_LOOP = r"""
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
while ($true) {
  $line = [Console]::In.ReadLine()
  if ($null -eq $line) { break }
  try {
    $src = [System.Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($line))
    $tokens = $null; $errors = $null
    $ast = [System.Management.Automation.Language.Parser]::ParseInput($src, [ref]$tokens, [ref]$errors)
    $cmds = @()
    foreach ($c in $ast.FindAll({ param($n) $n -is [System.Management.Automation.Language.CommandAst] }, $true)) {
      $elems = @()
      foreach ($e in $c.CommandElements) {
        $val = $null
        if ($e -is [System.Management.Automation.Language.StringConstantExpressionAst]) { $val = $e.Value }
        elseif ($e -is [System.Management.Automation.Language.CommandParameterAst]) { $val = '-' + $e.ParameterName; if ($e.Argument) { $val = $val + ':' + $e.Argument.Extent.Text } }
        else { $val = $e.Extent.Text }
        $elems += ,@{ text = $e.Extent.Text; value = $val; kind = $e.GetType().Name }
      }
      $pipeLen = 1
      if ($c.Parent -is [System.Management.Automation.Language.PipelineAst]) { $pipeLen = $c.Parent.PipelineElements.Count }
      $pipeIdx = 0
      if ($c.Parent -is [System.Management.Automation.Language.PipelineAst]) { $pipeIdx = $c.Parent.PipelineElements.IndexOf($c) }
      $cmds += ,@{ name = $c.GetCommandName(); elements = $elems; text = $c.Extent.Text; pipe_len = $pipeLen; pipe_idx = $pipeIdx; pipe_start = $c.Parent.Extent.StartOffset }
    }
    $invokes = @()
    foreach ($m in $ast.FindAll({ param($n) $n -is [System.Management.Automation.Language.InvokeMemberExpressionAst] }, $true)) { $invokes += $m.Extent.Text }
    $errs = @(); foreach ($er in $errors) { $errs += $er.Message }
    $out = @{ ok = $true; commands = $cmds; errors = $errs; invocations = $invokes }
  } catch {
    $out = @{ ok = $false; error = $_.Exception.Message }
  }
  [Console]::Out.WriteLine(($out | ConvertTo-Json -Depth 6 -Compress))
  [Console]::Out.Flush()
}
"""


@dataclass
class PsElement:
    text: str
    value: str
    kind: str


@dataclass
class PsCommand:
    name: str | None
    elements: list[PsElement]
    text: str
    pipe_len: int = 1
    pipe_idx: int = 0
    pipe_start: int = 0


@dataclass
class PsParseResult:
    ok: bool
    commands: list[PsCommand] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    invocations: list[str] = field(default_factory=list)
    error: str = ""


def powershell_exe() -> str | None:
    if sys.platform != "win32":
        return shutil.which("pwsh")
    return shutil.which("powershell") or shutil.which("pwsh")


class PowerShellParser:
    def __init__(self, idle_timeout: float = 300.0, request_timeout: float = 15.0) -> None:
        self.idle_timeout = idle_timeout
        self.request_timeout = request_timeout
        self._proc: subprocess.Popen[str] | None = None
        self._lock = threading.Lock()
        self._last_use = 0.0
        self._reaper: threading.Timer | None = None

    @property
    def available(self) -> bool:
        return powershell_exe() is not None

    def _start(self) -> subprocess.Popen[str]:
        exe = powershell_exe()
        if exe is None:
            raise RuntimeError("PowerShell is not available")
        encoded = base64.b64encode(_PARSER_LOOP.encode("utf-16-le")).decode("ascii")
        flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        return subprocess.Popen(
            [exe, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-EncodedCommand", encoded],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            creationflags=flags,
        )

    def _schedule_reap(self) -> None:
        if self._reaper is not None:
            self._reaper.cancel()
        self._reaper = threading.Timer(self.idle_timeout, self._reap)
        self._reaper.daemon = True
        self._reaper.start()

    def _reap(self) -> None:
        with self._lock:
            if self._proc is not None and time.monotonic() - self._last_use >= self.idle_timeout - 1:
                self._kill()

    def _kill(self) -> None:
        if self._proc is not None:
            try:
                if self._proc.stdin:
                    self._proc.stdin.close()
                self._proc.kill()
                self._proc.wait(timeout=5)
            except (OSError, subprocess.TimeoutExpired):
                pass
            self._proc = None

    def close(self) -> None:
        with self._lock:
            self._kill()
            if self._reaper is not None:
                self._reaper.cancel()

    def parse(self, script: str) -> PsParseResult:
        return _cached_parse(self, script)

    def _parse_uncached(self, script: str) -> PsParseResult:
        payload = base64.b64encode(script.encode("utf-8")).decode("ascii") + "\n"
        with self._lock:
            for attempt in range(2):
                if self._proc is None or self._proc.poll() is not None:
                    self._proc = self._start()
                proc = self._proc
                assert proc.stdin is not None and proc.stdout is not None
                try:
                    proc.stdin.write(payload)
                    proc.stdin.flush()
                    line = _readline_with_timeout(proc, self.request_timeout)
                except (OSError, TimeoutError):
                    self._kill()
                    if attempt == 1:
                        return PsParseResult(ok=False, error="PowerShell parser did not respond")
                    continue
                if not line:
                    self._kill()
                    continue
                self._last_use = time.monotonic()
                self._schedule_reap()
                return _decode(line)
        return PsParseResult(ok=False, error="PowerShell parser unavailable")


def _readline_with_timeout(proc: subprocess.Popen[str], timeout: float) -> str:
    result: list[str] = []

    def reader() -> None:
        assert proc.stdout is not None
        result.append(proc.stdout.readline())

    t = threading.Thread(target=reader, daemon=True)
    t.start()
    t.join(timeout)
    if t.is_alive():
        raise TimeoutError("parser timeout")
    return result[0] if result else ""


def _as_list(v: object) -> list[object]:
    if v is None:
        return []
    if isinstance(v, list):
        return v
    return [v]


def _decode(line: str) -> PsParseResult:
    try:
        data = json.loads(line)
    except json.JSONDecodeError as exc:
        return PsParseResult(ok=False, error=f"bad parser output: {exc}")
    if not data.get("ok"):
        return PsParseResult(ok=False, error=str(data.get("error", "parse failed")))
    commands: list[PsCommand] = []
    for c in _as_list(data.get("commands")):
        assert isinstance(c, dict)
        elements = [
            PsElement(text=str(e.get("text", "")), value=str(e.get("value", "")), kind=str(e.get("kind", "")))
            for e in _as_list(c.get("elements"))
            if isinstance(e, dict)
        ]
        commands.append(
            PsCommand(
                name=c.get("name"),
                elements=elements,
                text=str(c.get("text", "")),
                pipe_len=int(c.get("pipe_len", 1) or 1),
                pipe_idx=int(c.get("pipe_idx", 0) or 0),
                pipe_start=int(c.get("pipe_start", 0) or 0),
            )
        )
    return PsParseResult(
        ok=True,
        commands=commands,
        errors=[str(e) for e in _as_list(data.get("errors"))],
        invocations=[str(i) for i in _as_list(data.get("invocations"))],
    )


_CACHE: dict[str, PsParseResult] = {}
_CACHE_ORDER: list[str] = []
_CACHE_LOCK = threading.Lock()
_CACHE_MAX = 256


def _cached_parse(parser: PowerShellParser, script: str) -> PsParseResult:
    with _CACHE_LOCK:
        hit = _CACHE.get(script)
    if hit is not None:
        return hit
    result = parser._parse_uncached(script)
    if result.ok:
        with _CACHE_LOCK:
            _CACHE[script] = result
            _CACHE_ORDER.append(script)
            while len(_CACHE_ORDER) > _CACHE_MAX:
                _CACHE.pop(_CACHE_ORDER.pop(0), None)
    return result
