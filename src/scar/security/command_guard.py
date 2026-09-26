"""Command guard (C4.8): classifies PowerShell, CMD and argv commands by risk.

PowerShell is parsed with PowerShell's own AST parser (parse-only). CMD uses a
tokenizer that understands quoting, ``^`` escapes and ``& && || |`` operators.
Nested shells (``cmd /c``, ``powershell -Command``, ``-EncodedCommand``) are
unwrapped and classified recursively. Chains take the maximum risk of their
parts; unknown commands are at least MEDIUM; hard-deny patterns cannot be
overridden by grants or autonomy.
"""

from __future__ import annotations

import base64
import binascii
import ntpath
import re
from dataclasses import dataclass, field
from typing import Literal

from scar.core.types import PolicyDecision, RiskLevel
from scar.security.path_guard import PathCategory, PathGuard, PathOp
from scar.security.ps_parser import PowerShellParser

Shell = Literal["powershell", "pwsh", "cmd", "argv"]

_MAX_DEPTH = 4

PS_ALIASES: dict[str, str] = {
    "rm": "remove-item", "del": "remove-item", "erase": "remove-item", "rd": "remove-item", "rmdir": "remove-item",
    "ri": "remove-item", "iex": "invoke-expression", "iwr": "invoke-webrequest", "curl": "invoke-webrequest",
    "wget": "invoke-webrequest", "irm": "invoke-restmethod", "saps": "start-process", "start": "start-process",
    "kill": "stop-process", "spps": "stop-process", "gci": "get-childitem", "ls": "get-childitem",
    "dir": "get-childitem", "cat": "get-content", "gc": "get-content", "type": "get-content",
    "echo": "write-output", "write": "write-output", "cp": "copy-item", "copy": "copy-item", "cpi": "copy-item",
    "mv": "move-item", "move": "move-item", "mi": "move-item", "ni": "new-item", "ren": "rename-item",
    "rni": "rename-item", "cd": "set-location", "sl": "set-location", "chdir": "set-location", "pwd": "get-location",
    "gl": "get-location", "ps": "get-process", "gps": "get-process", "sc": "set-content", "ac": "add-content",
    "clc": "clear-content", "sp": "set-itemproperty", "gp": "get-itemproperty", "rp": "remove-itemproperty",
    "icm": "invoke-command", "iwmi": "invoke-wmimethod", "sajb": "start-job", "ii": "invoke-item",
    "select": "select-object", "where": "where-object", "?": "where-object", "%": "foreach-object",
    "foreach": "foreach-object", "sort": "sort-object", "measure": "measure-object", "ft": "format-table",
    "fl": "format-list", "cls": "clear-host", "clear": "clear-host", "h": "get-history", "history": "get-history",
    "sleep": "start-sleep", "tee": "tee-object", "gsv": "get-service", "sasv": "start-service",
    "spsv": "stop-service", "md": "mkdir",
}

_READ_ONLY_PS_VERBS = ("get-", "test-", "select-", "measure-", "format-", "out-string", "sort-", "group-",
                       "compare-", "convertto-", "convertfrom-", "resolve-", "find-", "show-", "search-", "read-host",
                       "where-object", "foreach-object", "write-output", "write-host", "write-verbose", "tee-object",
                       "split-path", "join-path", "start-sleep", "clear-host", "set-location", "push-location",
                       "pop-location", "import-csv", "import-clixml", "export-csv", "out-null", "out-host")

_LOW_EXES = {
    "whoami", "hostname", "ipconfig", "systeminfo", "tasklist", "where", "ver", "vol", "tree", "findstr", "find",
    "more", "sort", "fc", "comp", "date", "time", "echo", "type", "dir", "cd", "chdir", "path", "set", "cls",
    "nvidia-smi", "getmac", "nslookup", "ping", "tracert", "pathping", "netstat", "arp", "route_print", "query",
    "wmic_get", "driverquery", "powercfg_q", "systeminfo", "winver", "rem", "title", "color", "help", "pwd", "ls",
    "cat", "head", "tail", "wc", "grep", "which", "true", "false",
}

_MEDIUM_EXES = {
    "python", "python3", "py", "pip", "pip3", "uv", "uvx", "poetry", "pipenv", "conda", "node", "npm", "npx", "pnpm",
    "yarn", "bun", "deno", "tsc", "cargo", "rustc", "go", "dotnet", "java", "javac", "mvn", "gradle", "make", "cmake",
    "ninja", "msbuild", "pytest", "ruff", "black", "mypy", "pyright", "eslint", "prettier", "jest", "vitest", "code",
    "git", "gh", "docker", "mkdir", "md", "copy", "xcopy", "robocopy_copy", "attrib_read", "notepad", "explorer",
    "start", "timeout", "choice", "curl", "wget", "tar", "7z", "zip", "unzip", "expand", "certutil_hash", "ollama",
    "llama-server", "ffmpeg", "magick", "sqlite3", "jq", "scar", "echo_redirect",
}

_CRITICAL_PROCESSES = {"csrss", "wininit", "winlogon", "lsass", "services", "smss", "system", "svchost", "lsaiso",
                       "fontdrvhost", "dwm", "registry", "memory compression", "secure system", "msmpeng",
                       "securityhealthservice", "mpdefendercoreservice"}

_DOWNLOADERS = {"invoke-webrequest", "invoke-restmethod", "curl", "wget", "certutil", "bitsadmin", "start-bitstransfer"}
_EXECUTORS = {"invoke-expression", "iex", "powershell", "pwsh", "cmd", "sh", "bash", "python", "python3", "py", "node",
              "wscript", "cscript", "mshta", "rundll32", "regsvr32", "invoke-command"}


@dataclass
class Invocation:
    name: str  # normalised, lower-case, no path/extension, aliases resolved
    args: list[str]
    raw: str
    piped_after: list[str] = field(default_factory=list)  # names earlier in the same pipeline
    dynamic: bool = False


@dataclass
class CommandClassification:
    risk: RiskLevel
    decision_floor: PolicyDecision | None
    reasons: list[str]
    commands: list[str]
    parsed_with: str
    parse_errors: list[str] = field(default_factory=list)

    @property
    def denied(self) -> bool:
        return self.decision_floor == PolicyDecision.DENY


def _norm_name(name: str) -> str:
    base = ntpath.basename(name.strip().strip('"').strip("'")).lower()
    for ext in (".exe", ".cmd", ".bat", ".com", ".ps1"):
        if base.endswith(ext):
            base = base[: -len(ext)]
            break
    return PS_ALIASES.get(base, base)


def _flags(args: list[str]) -> set[str]:
    out: set[str] = set()
    for a in args:
        al = a.lower()
        if al.startswith(("-", "/")):
            out.add(al.split(":", 1)[0])
    return out


def _has_flag(args: list[str], *names: str) -> bool:
    """Prefix-aware flag check: PowerShell accepts unambiguous abbreviations (-rec, -fo)."""
    fl = _flags(args)
    for f in fl:
        stripped = f.lstrip("-/")
        for n in names:
            target = n.lstrip("-/").lower()
            if stripped == target or (f.startswith("-") and len(stripped) >= 2 and target.startswith(stripped)):
                return True
    return False


# ---------------------------------------------------------------- tokenizer (CMD)
_CMD_OPS = ("&&", "||", "&", "|")


def tokenize_cmd(command: str) -> list[list[str] | str]:
    """Split a CMD command line into token lists separated by operator strings."""
    segments: list[list[str] | str] = []
    tokens: list[str] = []
    buf: list[str] = []
    in_quote = False
    i = 0
    n = len(command)

    def flush_token() -> None:
        if buf:
            tokens.append("".join(buf))
            buf.clear()

    def flush_segment() -> None:
        flush_token()
        if tokens:
            segments.append(list(tokens))
            tokens.clear()

    while i < n:
        ch = command[i]
        if ch == '"':
            in_quote = not in_quote
            i += 1
            continue
        if not in_quote and ch == "^" and i + 1 < n:
            buf.append(command[i + 1])
            i += 2
            continue
        if not in_quote:
            if ch in " \t":
                flush_token()
                i += 1
                continue
            if ch in "\r\n":
                flush_segment()
                segments.append("&")
                i += 1
                continue
            if ch in "()":
                flush_segment()
                i += 1
                continue
            matched = next((op for op in _CMD_OPS if command.startswith(op, i)), None)
            if matched:
                flush_segment()
                segments.append(matched)
                i += len(matched)
                continue
            if ch in "<>":
                flush_token()
                j = i
                while j < n and command[j] in "<>&12":
                    j += 1
                tokens.append(command[i:j])
                i = j
                continue
        buf.append(ch)
        i += 1
    flush_segment()
    return segments


def _cmd_invocations(command: str) -> list[Invocation]:
    invs: list[Invocation] = []
    pipeline: list[str] = []
    for seg in tokenize_cmd(command):
        if isinstance(seg, str):
            if seg != "|":
                pipeline = []
            continue
        if not seg:
            continue
        name = seg[0]
        if name.startswith("@"):
            name = name[1:]
        inv = Invocation(name=_norm_name(name) if name else "", args=seg[1:], raw=" ".join(seg), piped_after=list(pipeline))
        invs.append(inv)
        pipeline.append(inv.name)
    return invs


# ---------------------------------------------------------------- guard
class CommandGuard:
    def __init__(self, path_guard: PathGuard | None = None, ps_parser: PowerShellParser | None = None) -> None:
        self.path_guard = path_guard
        self.ps_parser = ps_parser

    # public API ---------------------------------------------------------
    def classify(self, command: str, shell: Shell = "powershell", cwd: str | None = None) -> CommandClassification:
        reasons: list[str] = []
        names: list[str] = []
        errors: list[str] = []
        parsed_with = shell
        risk, floor = self._classify_script(command, shell, cwd, reasons, names, errors, depth=0)
        if shell in ("powershell", "pwsh"):
            parsed_with = "powershell-ast" if self.ps_parser and self.ps_parser.available else "tokenizer-fallback"
        return CommandClassification(risk, floor, _dedupe(reasons), names, parsed_with, errors)

    def classify_argv(self, argv: list[str], cwd: str | None = None) -> CommandClassification:
        if not argv:
            return CommandClassification(RiskLevel.MEDIUM, PolicyDecision.DENY, ["empty command"], [], "argv")
        inv = Invocation(name=_norm_name(argv[0]), args=list(argv[1:]), raw=" ".join(argv))
        reasons: list[str] = []
        names: list[str] = []
        errors: list[str] = []
        risk, floor = self._classify_invocations([inv], cwd, reasons, names, errors, depth=0)
        return CommandClassification(risk, floor, _dedupe(reasons), names, "argv", errors)

    # internals ----------------------------------------------------------
    def _classify_script(
        self,
        script: str,
        shell: Shell,
        cwd: str | None,
        reasons: list[str],
        names: list[str],
        errors: list[str],
        depth: int,
    ) -> tuple[RiskLevel, PolicyDecision | None]:
        if depth > _MAX_DEPTH:
            reasons.append("nesting too deep")
            return RiskLevel.CRITICAL, None
        if not script.strip():
            return RiskLevel.LOW, None
        extra_risk = RiskLevel.LOW
        if shell in ("powershell", "pwsh"):
            invs, extra_risk = self._ps_invocations(script, reasons, errors)
        else:
            invs = _cmd_invocations(script)
        risk, floor = self._classify_invocations(invs, cwd, reasons, names, errors, depth)
        return max(risk, extra_risk), floor

    def _ps_invocations(self, script: str, reasons: list[str], errors: list[str]) -> tuple[list[Invocation], RiskLevel]:
        if self.ps_parser is None or not self.ps_parser.available:
            reasons.append("PowerShell parser unavailable; using tokenizer")
            return _cmd_invocations(script.replace(";", "&")), RiskLevel.MEDIUM
        parsed = self.ps_parser.parse(script)
        if not parsed.ok:
            reasons.append(f"could not parse command ({parsed.error}); treated as high risk")
            return _cmd_invocations(script.replace(";", "&")), RiskLevel.HIGH
        extra = RiskLevel.LOW
        if parsed.errors:
            errors.extend(parsed.errors)
            reasons.append("command has syntax errors")
            extra = RiskLevel.MEDIUM
        invs: list[Invocation] = []
        pipelines: dict[int, list[str]] = {}
        for cmd in parsed.commands:
            elements = [e.value for e in cmd.elements]
            dynamic = cmd.name is None
            name = _norm_name(cmd.name) if cmd.name else ""
            before = pipelines.setdefault(cmd.pipe_start, []) if cmd.pipe_len > 1 else []
            invs.append(Invocation(name=name, args=elements[1:], raw=cmd.text, piped_after=list(before), dynamic=dynamic))
            if cmd.pipe_len > 1:
                before.append(name)
        for inv_text in parsed.invocations:
            low = inv_text.lower()
            if re.search(r"(assembly\]::load|reflection|::loadfile|virtualalloc|marshal\]::|add-type)", low):
                reasons.append(f"dynamic code loading: {inv_text[:60]}")
                extra = max(extra, RiskLevel.CRITICAL)
            elif re.search(r"(file|directory)\]::(delete|move|writeall|appendall|create|copy|replace)", low):
                reasons.append(f".NET filesystem mutation: {inv_text[:60]}")
                extra = max(extra, RiskLevel.HIGH)
            elif re.search(r"(downloadstring|downloadfile|downloaddata|webclient|httpclient)", low):
                reasons.append(f".NET download: {inv_text[:60]}")
                extra = max(extra, RiskLevel.HIGH)
            elif re.search(r"(process\]::start|::kill\()", low):
                reasons.append(f".NET process control: {inv_text[:60]}")
                extra = max(extra, RiskLevel.HIGH)
            else:
                extra = max(extra, RiskLevel.MEDIUM)
        return invs, extra

    def _classify_invocations(
        self,
        invs: list[Invocation],
        cwd: str | None,
        reasons: list[str],
        names: list[str],
        errors: list[str],
        depth: int,
    ) -> tuple[RiskLevel, PolicyDecision | None]:
        overall = RiskLevel.LOW
        floor: PolicyDecision | None = None
        if not invs:
            return RiskLevel.LOW, None
        for inv in invs:
            names.append(inv.name or "<dynamic>")
            r, deny = self._rule(inv, cwd, reasons, errors, depth)
            overall = max(overall, r)
            if deny:
                floor = PolicyDecision.DENY
        return overall, floor

    def _nested(
        self, script: str, shell: Shell, cwd: str | None, reasons: list[str], errors: list[str], depth: int
    ) -> tuple[RiskLevel, bool]:
        sub_names: list[str] = []
        r, f = self._classify_script(script, shell, cwd, reasons, sub_names, errors, depth + 1)
        return r, f == PolicyDecision.DENY

    def _path_risk(self, inv: Invocation, cwd: str | None, op: PathOp, reasons: list[str]) -> tuple[RiskLevel, bool]:
        if self.path_guard is None:
            return RiskLevel.LOW, False
        risk = RiskLevel.LOW
        deny = False
        for raw_arg in inv.args:
            a = raw_arg.strip("'\"")
            if a.startswith("-"):
                if ":" not in a:
                    continue
                a = a.split(":", 1)[1].strip("'\"")
            if re.match(r"^/[A-Za-z?]{1,4}(:.*)?$", a):
                continue
            if not _looks_like_path(a):
                continue
            chk = self.path_guard.check(a, op, base=cwd)
            if chk.category in (PathCategory.SECRET, PathCategory.DEVICE):
                reasons.append(f"touches {chk.category} path {chk.canonical}")
                deny = True
                risk = RiskLevel.CRITICAL
            elif chk.category in (PathCategory.SYSTEM, PathCategory.PROTECTED):
                reasons.append(f"touches {chk.category} path {chk.canonical}")
                risk = max(risk, RiskLevel.CRITICAL if op != PathOp.READ else RiskLevel.MEDIUM)
            elif chk.category in (PathCategory.OUTSIDE, PathCategory.NETWORK) and op != PathOp.READ:
                reasons.append(f"writes outside allowed roots: {chk.canonical}")
                risk = max(risk, RiskLevel.HIGH)
        return risk, deny

    def _rule(
        self, inv: Invocation, cwd: str | None, reasons: list[str], errors: list[str], depth: int
    ) -> tuple[RiskLevel, bool]:
        name = inv.name
        args = inv.args
        joined = " ".join(args).lower()
        raw_low = inv.raw.lower()

        if inv.dynamic or not name:
            reasons.append("dynamically computed command name (possible obfuscation)")
            return RiskLevel.CRITICAL, False
        if name.startswith("$") or name.startswith("&"):
            reasons.append("invokes a variable or expression as a command")
            return RiskLevel.CRITICAL, False

        # ---------- hard deny -----------------------------------------------------------------
        deny_reason = _hard_deny(name, args, joined, raw_low)
        if deny_reason:
            reasons.append(f"hard-denied: {deny_reason}")
            return RiskLevel.CRITICAL, True

        # ---------- download then execute ------------------------------------------------------
        if name in _EXECUTORS and any(p in _DOWNLOADERS for p in inv.piped_after):
            reasons.append("download piped into an interpreter")
            return RiskLevel.CRITICAL, False

        # ---------- nested shells --------------------------------------------------------------
        if name in ("powershell", "pwsh"):
            return self._nested_powershell(inv, cwd, reasons, errors, depth)
        if name == "cmd":
            idx = next((i for i, a in enumerate(args) if a.lower() in ("/c", "/k", "/r")), None)
            if idx is None:
                reasons.append("opens an interactive cmd shell")
                return RiskLevel.MEDIUM, False
            inner = " ".join(args[idx + 1 :])
            r, d = self._nested(inner, "cmd", cwd, reasons, errors, depth)
            return max(r, RiskLevel.LOW), d
        if name in ("invoke-expression",):
            reasons.append("Invoke-Expression executes arbitrary strings")
            return RiskLevel.CRITICAL, False
        if name in ("invoke-command", "start-job") and ("-scriptblock" in _flags(args) or "{" in joined):
            reasons.append(f"{name} runs a script block")
            return RiskLevel.HIGH, False

        # ---------- registry -------------------------------------------------------------------
        if name == "reg":
            sub = args[0].lower() if args else ""
            key = args[1].upper() if len(args) > 1 else ""
            system_hive = key.startswith(("HKLM", "HKEY_LOCAL_MACHINE", "HKCR", "HKEY_CLASSES_ROOT", "HKU", "HKEY_USERS"))
            if sub in ("query", "export", "compare"):
                return RiskLevel.LOW, False
            if sub in ("delete", "add", "import", "restore", "load", "unload", "copy"):
                if system_hive:
                    reasons.append(f"reg {sub} under a system hive")
                    return RiskLevel.CRITICAL, False
                reasons.append(f"reg {sub} modifies the user registry")
                return RiskLevel.HIGH, False
            return RiskLevel.MEDIUM, False
        if name in ("set-itemproperty", "new-itemproperty", "remove-itemproperty", "remove-item", "new-item",
                    "set-item") and re.search(r"\b(hklm|hkcr|hku|registry::hkey_local_machine)\s*:", joined):
            reasons.append("modifies HKLM registry")
            return RiskLevel.CRITICAL, False
        if name in ("set-itemproperty", "new-itemproperty", "remove-itemproperty") and "hkcu:" in joined:
            reasons.append("modifies HKCU registry")
            return RiskLevel.HIGH, False

        # ---------- destructive filesystem -----------------------------------------------------
        if name == "remove-item" or name in ("rm", "del", "erase", "rd", "rmdir"):
            recursive = _has_flag(args, "-recurse", "-r", "/s", "-rf", "-fr")
            force = _has_flag(args, "-force", "-f", "/q", "/f", "-rf", "-fr")
            pr, pd = self._path_risk(inv, cwd, PathOp.DELETE, reasons)
            if pd:
                return RiskLevel.CRITICAL, True
            if recursive and force:
                reasons.append("recursive forced removal")
                return RiskLevel.CRITICAL, False
            if recursive or re.search(r"[*?]", joined):
                reasons.append("recursive or wildcard removal")
                return max(RiskLevel.HIGH, pr), False
            reasons.append("permanent file removal (bypasses Recycle Bin)")
            return max(RiskLevel.HIGH, pr), False
        if name in ("format", "format-volume", "clear-disk", "initialize-disk", "diskpart", "bcdedit", "bootrec",
                    "remove-partition", "set-partition", "mountvol", "fsutil", "sdelete", "vssadmin", "wbadmin",
                    "clear-recyclebin", "cipher"):
            if name == "cipher" and not _has_flag(args, "/w"):
                reasons.append("cipher changes encryption state")
                return RiskLevel.HIGH, False
            if name == "fsutil" and joined.startswith(("fsinfo", "volume diskfree", "file queryextents")):
                return RiskLevel.LOW, False
            if name == "vssadmin" and joined.startswith("list"):
                return RiskLevel.LOW, False
            if name == "bcdedit" and (not args or joined.startswith("/enum")):
                reasons.append("reads boot configuration")
                return RiskLevel.MEDIUM, False
            reasons.append(f"disk/boot/destructive utility {name}")
            return RiskLevel.CRITICAL, False
        if name in ("shutdown", "restart-computer", "stop-computer", "logoff", "shutdown.exe"):
            if name == "shutdown" and _has_flag(args, "/a"):
                return RiskLevel.MEDIUM, False
            reasons.append("shuts down, restarts or logs off the computer")
            return RiskLevel.CRITICAL, False
        if name in ("takeown", "icacls", "cacls", "set-acl"):
            pr, pd = self._path_risk(inv, cwd, PathOp.WRITE, reasons)
            if pd:
                return RiskLevel.CRITICAL, True
            if name == "icacls" and len(args) <= 1:
                return RiskLevel.LOW, False
            reasons.append("changes file ownership or permissions")
            return max(RiskLevel.HIGH, pr), False
        if name == "set-executionpolicy":
            reasons.append("changes the PowerShell execution policy")
            return RiskLevel.CRITICAL, False

        # ---------- processes / services -------------------------------------------------------
        if name in ("stop-process", "taskkill", "pskill", "tskill"):
            targets = {re.sub(r"\.exe$", "", a.lower().strip('"')) for a in args if not a.startswith(("-", "/"))}
            if targets & _CRITICAL_PROCESSES or re.search(r"\b(lsass|csrss|winlogon|wininit|smss|services|svchost)\b", joined):
                reasons.append("targets a system-critical process")
                return RiskLevel.CRITICAL, True
            if "explorer" in targets:
                reasons.append("kills Explorer")
                return RiskLevel.HIGH, False
            reasons.append("terminates processes")
            return RiskLevel.HIGH, False
        if name in ("stop-service", "set-service", "remove-service", "new-service", "restart-service", "suspend-service",
                    "start-service", "sc", "net"):
            if name == "net" and args and args[0].lower() in ("view", "statistics", "config", "share", "session") and len(args) <= 2:
                return RiskLevel.LOW, False
            if name == "net" and args and args[0].lower() in ("user", "localgroup", "accounts"):
                if len(args) <= 2 and not re.search(r"/(add|delete|active)", joined):
                    return RiskLevel.LOW, False
                reasons.append("changes user accounts or groups")
                return RiskLevel.CRITICAL, False
            if name == "sc" and args and args[0].lower() in ("query", "qc", "queryex", "qdescription"):
                return RiskLevel.LOW, False
            reasons.append("changes Windows services or network configuration")
            return RiskLevel.HIGH, False
        if name in ("schtasks", "register-scheduledtask", "new-scheduledtask", "unregister-scheduledtask"):
            if name == "schtasks" and (not args or args[0].lower() == "/query"):
                return RiskLevel.LOW, False
            reasons.append("creates or changes scheduled tasks (persistence)")
            return RiskLevel.HIGH, False
        if name in ("netsh", "set-netfirewallrule", "new-netfirewallrule", "remove-netfirewallrule", "route",
                    "set-netipaddress", "set-dnsclientserveraddress", "new-netipaddress"):
            if name == "netsh" and re.search(r"\b(show|dump)\b", joined) and not re.search(r"\b(set|add|delete|reset)\b", joined):
                return RiskLevel.LOW, False
            if name == "route" and args and args[0].lower() == "print":
                return RiskLevel.LOW, False
            reasons.append("changes network or firewall configuration")
            return RiskLevel.HIGH, False

        # ---------- software install / elevation -----------------------------------------------
        if name in ("winget", "choco", "scoop", "msiexec", "install-package", "install-module", "install-script",
                    "add-appxpackage", "dism", "enable-windowsoptionalfeature", "wsl"):
            if name in ("winget", "choco", "scoop") and args and args[0].lower() in ("list", "search", "show", "info"):
                return RiskLevel.LOW, False
            if name == "wsl" and (not args or args[0].lower() in ("-l", "--list", "--status")):
                return RiskLevel.LOW, False
            reasons.append("installs or modifies system software")
            return RiskLevel.HIGH, False
        if name == "start-process":
            if "-verb" in _flags(args) and "runas" in joined:
                reasons.append("attempts elevation")
                return RiskLevel.CRITICAL, True
            target = next((a for a in args if not a.startswith("-")), "")
            if target:
                sub = Invocation(name=_norm_name(target), args=_argument_list(args), raw=inv.raw)
                r, d = self._rule(sub, cwd, reasons, errors, depth + 1)
                return max(r, RiskLevel.MEDIUM), d
            return RiskLevel.MEDIUM, False
        if name in ("invoke-item",):
            pr, pd = self._path_risk(inv, cwd, PathOp.EXECUTE, reasons)
            if re.search(r"\.(exe|msi|bat|cmd|ps1|vbs|js|scr|com|hta)\b", joined):
                reasons.append("executes a file")
                return max(RiskLevel.HIGH, pr), pd
            return max(RiskLevel.MEDIUM, pr), pd

        # ---------- downloads / execution of downloaded code ------------------------------------
        if name in _DOWNLOADERS:
            if name == "certutil" and not re.search(r"-(urlcache|decode|encode|verifyctl)", joined):
                return RiskLevel.LOW, False
            if re.search(r"-(outfile|o\b|output)|>|\s-o\s", " " + joined + " "):
                pr, pd = self._path_risk(Invocation(name, [a for a in args if _looks_like_path(a)], inv.raw), cwd, PathOp.WRITE, reasons)
                if re.search(r"\.(exe|msi|bat|cmd|ps1|vbs|scr|dll)\b", joined):
                    reasons.append("downloads an executable")
                    return max(RiskLevel.HIGH, pr), pd
                reasons.append("downloads a file")
                return max(RiskLevel.MEDIUM, pr), pd
            reasons.append("performs a web request")
            return RiskLevel.MEDIUM, False
        if name in ("mshta", "rundll32", "regsvr32", "wscript", "cscript", "installutil", "regasm", "msbuild_inline"):
            reasons.append(f"{name} is a common code-execution vector")
            return RiskLevel.CRITICAL, False

        # ---------- git ------------------------------------------------------------------------
        if name == "git":
            return _git_risk(args, reasons), False
        if name in ("npm", "pnpm", "yarn") and args and args[0].lower() in ("publish", "unpublish", "deprecate"):
            reasons.append("publishes a package")
            return RiskLevel.HIGH, False
        if name in ("pip", "pip3", "uv") and "install" in [a.lower() for a in args[:2]]:
            if any(a.lower() in ("--user", "--system", "--break-system-packages", "-g") for a in args):
                reasons.append("installs packages outside a project environment")
                return RiskLevel.HIGH, False
            return RiskLevel.MEDIUM, False
        if name in ("npm", "pnpm", "yarn") and any(a in ("-g", "--global") for a in args):
            reasons.append("installs a global package")
            return RiskLevel.HIGH, False
        if name == "gh":
            sub = " ".join(args[:2]).lower()
            if re.match(r"(repo delete|release delete|secret|auth)", sub):
                reasons.append(f"gh {sub}")
                return RiskLevel.CRITICAL, False
            if re.match(r"(pr (create|merge|close|comment)|issue (create|close|comment)|release create|repo create)", sub):
                reasons.append(f"gh {sub} publishes to GitHub")
                return RiskLevel.HIGH, False
            return RiskLevel.LOW, False

        # ---------- file writes ---------------------------------------------------------------
        if name in ("set-content", "add-content", "out-file", "new-item", "copy-item", "move-item", "rename-item",
                    "clear-content", "mkdir", "copy", "xcopy", "robocopy", "move", "ren", "tee-object", "expand-archive",
                    "compress-archive"):
            op = PathOp.MOVE if name in ("move-item", "move", "rename-item", "ren") else PathOp.WRITE
            pr, pd = self._path_risk(inv, cwd, op, reasons)
            base = RiskLevel.HIGH if op == PathOp.MOVE else RiskLevel.MEDIUM
            if name == "robocopy" and _has_flag(args, "/mir", "/purge"):
                reasons.append("robocopy mirror deletes destination files")
                base = RiskLevel.HIGH
            if name == "clear-content":
                base = RiskLevel.HIGH
            return max(base, pr), pd

        # ---------- read-only ------------------------------------------------------------------
        if name.startswith(_READ_ONLY_PS_VERBS) or name in _LOW_EXES:
            pr, pd = self._path_risk(inv, cwd, PathOp.READ, reasons)
            return max(RiskLevel.LOW, pr), pd
        if name in _MEDIUM_EXES:
            pr, pd = self._path_risk(inv, cwd, PathOp.EXECUTE, reasons)
            return max(RiskLevel.MEDIUM, pr), pd
        if re.match(r"^(set|new|remove|clear|disable|enable|stop|restart|uninstall|unregister|reset|revoke|grant|block|"
                    r"suspend|update|install|register|add|publish|send|move|rename|write|push|lock|unlock|dismount|mount|"
                    r"initialize|import|export)-", name):
            reasons.append(f"state-changing cmdlet {name}")
            return RiskLevel.HIGH, False

        pr, pd = self._path_risk(inv, cwd, PathOp.EXECUTE, reasons)
        reasons.append(f"unknown command {name!r}")
        return max(RiskLevel.MEDIUM, pr), pd

    def _nested_powershell(
        self, inv: Invocation, cwd: str | None, reasons: list[str], errors: list[str], depth: int
    ) -> tuple[RiskLevel, bool]:
        args = inv.args
        lowered = [a.lower() for a in args]
        for i, a in enumerate(lowered):
            flag = a.split(":", 1)[0]
            if flag in ("-encodedcommand", "-enc", "-ec", "-e", "-en", "-enco", "-encod", "-encode", "-encoded"):
                value = args[i + 1] if i + 1 < len(args) else (args[i].split(":", 1)[1] if ":" in args[i] else "")
                try:
                    decoded = base64.b64decode(value, validate=True).decode("utf-16-le")
                except (binascii.Error, UnicodeDecodeError, ValueError):
                    reasons.append("undecodable -EncodedCommand")
                    return RiskLevel.CRITICAL, False
                reasons.append("decoded -EncodedCommand")
                r, d = self._nested(decoded, "powershell", cwd, reasons, errors, depth)
                return max(r, RiskLevel.HIGH), d
            if flag in ("-command", "-c", "-co", "-com"):
                inner = " ".join(args[i + 1 :])
                r, d = self._nested(inner, "powershell", cwd, reasons, errors, depth)
                return max(r, RiskLevel.MEDIUM), d
            if flag in ("-file", "-f"):
                reasons.append("runs a PowerShell script file")
                pr, pd = self._path_risk(Invocation("powershell", args[i + 1 : i + 2], inv.raw), cwd, PathOp.EXECUTE, reasons)
                return max(RiskLevel.HIGH, pr), pd
        positional = [a for a in args if not a.startswith("-")]
        if positional:
            r, d = self._nested(" ".join(positional), "powershell", cwd, reasons, errors, depth)
            return max(r, RiskLevel.MEDIUM), d
        reasons.append("opens an interactive PowerShell")
        return RiskLevel.MEDIUM, False


def _argument_list(args: list[str]) -> list[str]:
    for i, a in enumerate(args):
        if a.lower().split(":", 1)[0] in ("-argumentlist", "-args"):
            return [x.strip("'\"") for x in re.split(r"[,\s]+", " ".join(args[i + 1 :])) if x]
    return []


def _looks_like_path(a: str) -> bool:
    s = a.strip("'\"")
    if not s or s.startswith("-") and not re.match(r"^-[A-Za-z]+:[A-Za-z]:\\", s):
        return False
    if ":" in s and s.split(":", 1)[0].lower() in ("http", "https", "ftp", "file", "ssh", "git"):
        return False
    if re.match(r"^[A-Za-z]{2,}:", s):  # PowerShell provider paths (HKLM:, Env:, Cert:) and URLs
        return False
    return bool(re.match(r"^([A-Za-z]:[\\/]|\\\\|\.{1,2}[\\/]|~[\\/]|%[A-Za-z_]+%|\$env:)", s) or "\\" in s or "/" in s)


def _git_risk(args: list[str], reasons: list[str]) -> RiskLevel:
    sub = next((a.lower() for a in args if not a.startswith("-")), "")
    lowered = [a.lower() for a in args]
    if sub == "push":
        if any(a in ("--force", "-f", "--force-with-lease", "--mirror", "--delete", "-d") or a.startswith("+") or
               a.startswith("--force") for a in lowered):
            reasons.append("force/destructive push rewrites remote history")
            return RiskLevel.CRITICAL
        reasons.append("git push publishes commits")
        return RiskLevel.HIGH
    if sub in ("reset",) and "--hard" in lowered:
        reasons.append("git reset --hard discards changes")
        return RiskLevel.HIGH
    if sub in ("clean",):
        reasons.append("git clean deletes untracked files")
        return RiskLevel.HIGH
    if sub in ("rebase", "filter-branch", "filter-repo", "replace"):
        reasons.append(f"git {sub} rewrites history")
        return RiskLevel.HIGH
    if sub in ("merge", "cherry-pick", "revert", "am", "pull"):
        return RiskLevel.MEDIUM
    if sub == "branch" and any(a in ("-d", "-D", "--delete", "-m", "-M") for a in args):
        reasons.append("deletes or renames a branch")
        return RiskLevel.HIGH
    if sub in ("checkout", "switch", "restore") and any(a in ("--", ".", "-f", "--force") for a in args[1:]):
        reasons.append("discards working tree changes")
        return RiskLevel.HIGH
    if sub == "stash" and any(a in ("drop", "clear") for a in lowered):
        reasons.append("drops stashed changes")
        return RiskLevel.HIGH
    if sub in ("status", "log", "diff", "show", "branch", "remote", "rev-parse", "ls-files", "blame", "grep",
               "describe", "tag", "shortlog", "reflog", "config", "fetch", "version", "--version", "help", ""):
        if sub == "config" and not any(a in ("--get", "--list", "-l", "--get-all") for a in lowered):
            return RiskLevel.MEDIUM
        if sub == "tag" and len(args) > 1:
            return RiskLevel.MEDIUM
        return RiskLevel.LOW
    return RiskLevel.MEDIUM


_DENY_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("disables Microsoft Defender", re.compile(
        r"set-mppreference.*-(disable(realtimemonitoring|behaviormonitoring|ioavprotection|scriptscanning|"
        r"intrusionpreventionsystem|archivescanning|blockatfirstseen)|exclusion)|add-mppreference.*-exclusion|"
        r"disableantispyware|disableantivirus|windefend.*(stop|disable)|sc\s+(stop|config|delete)\s+windefend|"
        r"uninstall-windowsfeature.*defender|mpcmdrun.*-removedefinitions")),
    ("disables the Windows firewall", re.compile(
        r"netsh\s+(advfirewall|firewall)\s+set\s+.*(state\s+off|opmode\s+disable)|"
        r"set-netfirewallprofile.*-enabled\s*:?\s*(\$?false|0)|mpssvc.*(stop|disable)")),
    ("disables UAC", re.compile(r"enablelua.*\b0\b|consentpromptbehavioradmin.*\b0\b|promptonsecuredesktop.*\b0\b")),
    ("self-elevation", re.compile(r"\brunas(\.exe)?\b|-verb\s*:?\s*['\"]?runas|\bsudo\b|\bgsudo\b|\bpsexec\b.*-s\b")),
    ("credential extraction", re.compile(
        r"mimikatz|sekurlsa|lsadump|procdump.*lsass|comsvcs(\.dll)?.*minidump|vaultcmd\s+/listcreds|"
        r"\\login data|\\cookies\b|\\web data|logins\.json|key[34]\.db|cmdkey\s+/list|get-storedcredential|"
        r"credentialmanager|protectedstorage|dpapi|\\microsoft\\credentials|\\microsoft\\protect|ntds\.dit|"
        r"reg\s+save\s+hklm\\(sam|security|system)")),
    ("captcha/bot-detection bypass", re.compile(r"captcha.*(solv|bypass)|2captcha|anti-captcha|capsolver")),
    ("exfiltrates SCAR secrets", re.compile(r"\\scar\\ipc\.json|scar[\\/]\.env\b|get-content.*\.env\b.*\|.*(invoke-|curl|iwr)")),
    ("clears event logs", re.compile(r"wevtutil\s+(cl|clear-log)|clear-eventlog")),
    ("deletes shadow copies", re.compile(r"vssadmin.*delete\s+shadows|wmic\s+shadowcopy\s+delete|wbadmin\s+delete\s+catalog")),
]


def _hard_deny(name: str, args: list[str], joined: str, raw_low: str) -> str | None:
    text = f"{name} {joined} {raw_low}"
    for reason, pattern in _DENY_PATTERNS:
        if pattern.search(text):
            return reason
    return None


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for i in items:
        if i not in seen:
            seen.add(i)
            out.append(i)
    return out
