"""Filesystem tools (C9.1). Every operation goes through the path guard and verifies its outcome."""

from __future__ import annotations

import asyncio
import datetime as dt
import difflib
import fnmatch
import os
import re
import shutil
from pathlib import Path
from typing import Any, Literal

from pydantic import Field

from scar.core.errors import ToolError, ToolInputError
from scar.core.types import Check, RiskLevel, SideEffect, ToolResult, TrustLevel, VerificationResult
from scar.security.path_guard import PathOp
from scar.security.risk import RiskAssessment, bulk_escalation
from scar.tools.base import Tool, ToolContext, ToolInput
from scar.tools.fs.common import (
    backup_file,
    check_path,
    human_size,
    looks_binary,
    sha256_bytes,
    sha256_file,
)

MAX_READ_BYTES = 256 * 1024
DOC_EXTS = {".pdf", ".docx", ".xlsx", ".pptx"}
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".tif", ".tiff"}


def _assess_paths(ctx: ToolContext, paths: list[str], op: PathOp) -> RiskAssessment:
    a = RiskAssessment(RiskLevel.LOW)
    for raw in paths:
        chk = ctx.services.path_guard.check(raw, op, base=ctx.services.extras.get("cwd"))
        if chk.denied:
            a.deny("; ".join(chk.reasons), "secret_paths")
        a.raise_to(chk.risk, "; ".join(r for r in chk.reasons if r) if chk.risk > RiskLevel.LOW else "")
        a.facts.paths.append(chk.canonical)
    a.facts.data_classes.append("files")
    return a


# ---------------------------------------------------------------- read
class ReadInput(ToolInput):
    path: str = Field(description="File path")
    start_line: int = Field(1, ge=1, description="1-based first line to return")
    max_lines: int = Field(400, ge=1, le=5000)
    mode: Literal["text", "head", "tail"] = "text"


class FsRead(Tool):
    name = "fs.read"
    description = ("Read a text file (line range, head or tail). For PDF/DOCX use documents.read. Large files are "
                   "never loaded whole; ask for further line ranges.")
    input_model = ReadInput
    capabilities = ("fs.read",)
    output_trust = TrustLevel.UNTRUSTED_EXTERNAL
    data_class = "files"
    categories = ("fs", "dev", "documents")
    sensitive_args = {"path": "path"}

    def assess(self, args: ReadInput, ctx: ToolContext) -> RiskAssessment:
        return _assess_paths(ctx, [args.path], PathOp.READ)

    def describe(self, args: ReadInput) -> str:
        return f"read {args.path}"

    async def run(self, args: ReadInput, ctx: ToolContext) -> ToolResult:
        chk = check_path(ctx, args.path, PathOp.READ)
        p = chk.path
        if not p.exists():
            raise ToolError(f"File not found: {p}", "NotFound")
        if p.is_dir():
            raise ToolError(f"{p} is a directory; use fs.list", "IsDirectory")
        if p.suffix.lower() in DOC_EXTS:
            raise ToolError(f"{p.name} is a document; use documents.read", "WrongTool")
        if looks_binary(p):
            raise ToolError(f"{p.name} is a binary file ({human_size(p.stat().st_size)})", "BinaryFile")
        return await asyncio.to_thread(self._read, p, args)

    def _read(self, p: Path, args: ReadInput) -> ToolResult:
        size = p.stat().st_size
        lines: list[str] = []
        total = 0
        if args.mode == "tail":
            from collections import deque

            tail: deque[tuple[int, str]] = deque(maxlen=args.max_lines)
            with p.open("r", encoding="utf-8", errors="replace") as fh:
                for total, line in enumerate(fh, 1):
                    tail.append((total, line))
            first = tail[0][0] if tail else 1
            lines = [line for _, line in tail]
        else:
            first = 1 if args.mode == "head" else args.start_line
            used = 0
            with p.open("r", encoding="utf-8", errors="replace") as fh:
                for total, line in enumerate(fh, 1):
                    if total >= first and len(lines) < args.max_lines and used < MAX_READ_BYTES:
                        lines.append(line)
                        used += len(line)
        last = first + len(lines) - 1
        body = "".join(f"{first + i:>6}| {line if line.endswith(chr(10)) else line + chr(10)}" for i, line in enumerate(lines))
        more = f"\n[lines {first}-{last} of {total}; request more with start_line]" if last < total or first > 1 else ""
        return self.ok(
            f"Read {p.name} (lines {first}-{last} of {total})",
            {"path": str(p), "first_line": first, "last_line": last, "total_lines": total, "size": size,
             "content": "".join(lines)},
            model_view=f"{p}\n{body}{more}",
            source=f"file:{p}",
        )


# ---------------------------------------------------------------- write / edit
class WriteInput(ToolInput):
    path: str
    content: str
    mode: Literal["overwrite", "create", "append"] = Field("create", description="create fails if the file exists")
    encoding: Literal["utf-8", "utf-8-sig", "utf-16", "latin-1"] = "utf-8"


class FsWrite(Tool):
    name = "fs.write"
    description = "Create, overwrite or append to a text file. Verifies the bytes on disk afterwards."
    input_model = WriteInput
    capabilities = ("fs.write",)
    base_risk = RiskLevel.MEDIUM
    side_effects = SideEffect.LOCAL
    categories = ("fs", "dev", "documents")
    sensitive_args = {"path": "path"}

    def assess(self, args: WriteInput, ctx: ToolContext) -> RiskAssessment:
        a = _assess_paths(ctx, [args.path], PathOp.WRITE)
        chk = ctx.services.path_guard.check(args.path, PathOp.WRITE, base=ctx.services.extras.get("cwd"))
        if args.mode == "overwrite" and chk.path.exists():
            a.raise_to(a.level.escalate(1) if a.level < RiskLevel.HIGH else a.level, "overwrites an existing file")
        return a

    def describe(self, args: WriteInput) -> str:
        verb = {"overwrite": "overwrite", "create": "create", "append": "append to"}[args.mode]
        return f"{verb} {args.path} ({len(args.content.encode(args.encoding, errors='replace'))} bytes)"

    def progress_line(self, args: WriteInput) -> str | None:
        return f"Writing {Path(args.path).name}."

    async def run(self, args: WriteInput, ctx: ToolContext) -> ToolResult:
        chk = check_path(ctx, args.path, PathOp.WRITE)
        p = chk.path
        if p.exists() and p.is_dir():
            raise ToolError(f"{p} is a directory", "IsDirectory")
        if args.mode == "create" and p.exists():
            raise ToolError(f"{p} already exists (use mode=overwrite or fs.edit)", "Exists")
        backup = backup_file(ctx, p) if p.exists() else None
        p.parent.mkdir(parents=True, exist_ok=True)
        data = args.content.encode(args.encoding)
        if args.mode == "append":
            with p.open("ab") as fh:
                fh.write(data)
        else:
            tmp = p.with_name(f".{p.name}.scar-tmp")
            tmp.write_bytes(data)
            os.replace(tmp, p)
        return self.ok(f"Wrote {p.name} ({human_size(len(data))})",
                       {"path": str(p), "bytes": len(data), "sha256": sha256_bytes(data),
                        "backup": str(backup) if backup else None, "mode": args.mode})

    async def verify(self, args: WriteInput, result: ToolResult, ctx: ToolContext) -> VerificationResult:
        p = Path(result.data["path"])
        checks = [Check(name="file exists", passed=p.is_file())]
        if p.is_file():
            data = p.read_bytes()
            expected = args.content.encode(args.encoding)
            if args.mode == "append":
                checks.append(Check(name="content appended", passed=data.endswith(expected)))
            else:
                digest = sha256_bytes(data)
                checks.append(Check(name="sha256 matches", passed=digest == result.data["sha256"], detail=digest[:16]))
        return VerificationResult.from_checks(checks, {"path": str(p)})

    def dry_run(self, args: WriteInput) -> ToolResult:
        return ToolResult.success(f"[dry run] would {self.describe(args)}", {"dry_run": True})


class EditInput(ToolInput):
    path: str
    old: str = Field(description="Exact existing text to replace (must be unique unless count>1)")
    new: str = Field(description="Replacement text")
    count: int = Field(1, ge=1, le=100, description="Number of occurrences expected and replaced")


class FsEdit(Tool):
    name = "fs.edit"
    description = ("Replace exact text in a file (search/replace). Fails if the text is missing or occurs a "
                   "different number of times than `count`. Keeps a backup outside git repos.")
    input_model = EditInput
    capabilities = ("fs.write",)
    base_risk = RiskLevel.MEDIUM
    side_effects = SideEffect.LOCAL
    categories = ("fs", "dev")
    sensitive_args = {"path": "path"}

    def assess(self, args: EditInput, ctx: ToolContext) -> RiskAssessment:
        return _assess_paths(ctx, [args.path], PathOp.WRITE)

    def describe(self, args: EditInput) -> str:
        return f"edit {args.path}: replace {len(args.old)} chars with {len(args.new)} chars"

    def approval_details(self, args: EditInput) -> dict[str, Any]:
        diff = "".join(difflib.unified_diff(args.old.splitlines(True), args.new.splitlines(True), "before", "after"))
        return {"path": args.path, "diff": diff[:4000]}

    async def run(self, args: EditInput, ctx: ToolContext) -> ToolResult:
        chk = check_path(ctx, args.path, PathOp.WRITE)
        p = chk.path
        if not p.is_file():
            raise ToolError(f"File not found: {p}", "NotFound")
        raw = p.read_bytes()
        crlf = b"\r\n" in raw
        text = raw.decode("utf-8", errors="strict")
        old, new = args.old, args.new
        occurrences = text.count(old)
        if occurrences == 0 and crlf:
            old, new = old.replace("\r\n", "\n").replace("\n", "\r\n"), new.replace("\r\n", "\n").replace("\n", "\r\n")
            occurrences = text.count(old)
        if occurrences == 0:
            raise ToolError("the text to replace was not found (check whitespace/indentation; read the file first)", "NoMatch")
        if occurrences != args.count:
            raise ToolError(f"the text occurs {occurrences} times but count={args.count}; include more context", "Ambiguous")
        backup = backup_file(ctx, p)
        updated = text.replace(old, new, args.count)
        data = updated.encode("utf-8")
        tmp = p.with_name(f".{p.name}.scar-tmp")
        tmp.write_bytes(data)
        os.replace(tmp, p)
        diff = "".join(difflib.unified_diff(text.splitlines(True), updated.splitlines(True), str(p), str(p), n=2))
        return self.ok(f"Edited {p.name}", {"path": str(p), "sha256": sha256_bytes(data), "replacements": args.count,
                                             "backup": str(backup) if backup else None},
                       model_view=f"Edited {p}\n{diff[:3000]}")

    async def verify(self, args: EditInput, result: ToolResult, ctx: ToolContext) -> VerificationResult:
        p = Path(result.data["path"])
        digest = sha256_file(p)
        return VerificationResult.from_checks([
            Check(name="file hash matches edit", passed=digest == result.data["sha256"]),
            Check(name="new text present", passed=args.new.replace("\r\n", "\n") in p.read_text("utf-8").replace("\r\n", "\n")
                  if args.new else True),
        ])


# ---------------------------------------------------------------- list / search / info
class ListInput(ToolInput):
    path: str = "."
    pattern: str = Field("*", description="glob filter on names")
    recursive: bool = False
    max_entries: int = Field(300, ge=1, le=5000)
    include_hidden: bool = False


class FsList(Tool):
    name = "fs.list"
    description = "List a directory (optionally recursive with a glob filter)."
    input_model = ListInput
    capabilities = ("fs.read",)
    categories = ("fs", "dev")
    data_class = "files"
    sensitive_args = {"path": "path"}

    def assess(self, args: ListInput, ctx: ToolContext) -> RiskAssessment:
        return _assess_paths(ctx, [args.path], PathOp.LIST)

    async def run(self, args: ListInput, ctx: ToolContext) -> ToolResult:
        chk = check_path(ctx, args.path, PathOp.LIST)
        root = chk.path
        if not root.is_dir():
            raise ToolError(f"Not a directory: {root}", "NotFound")
        return await asyncio.to_thread(self._list, root, args, ctx)

    def _list(self, root: Path, args: ListInput, ctx: ToolContext) -> ToolResult:
        entries: list[dict[str, Any]] = []
        truncated = False
        skip_dirs = {".git", "node_modules", "__pycache__", ".venv", "venv", ".mypy_cache", ".pytest_cache", "dist", "build"}
        walker = os.walk(root) if args.recursive else [(str(root), [d.name for d in root.iterdir() if d.is_dir()],
                                                        [f.name for f in root.iterdir() if not f.is_dir()])]
        for dirpath, dirs, files in walker:
            if args.recursive:
                dirs[:] = [d for d in dirs if d not in skip_dirs and (args.include_hidden or not d.startswith("."))]
            for name in sorted(dirs) + sorted(files):
                if not args.include_hidden and name.startswith("."):
                    continue
                if not fnmatch.fnmatch(name.lower(), args.pattern.lower()):
                    continue
                full = Path(dirpath) / name
                if ctx.services.path_guard.check(str(full), PathOp.LIST).denied:
                    continue
                try:
                    st = full.stat()
                except OSError:
                    continue
                entries.append({"path": str(full), "name": name, "dir": full.is_dir(), "size": st.st_size,
                                "modified": dt.datetime.fromtimestamp(st.st_mtime).isoformat(timespec="seconds")})
                if len(entries) >= args.max_entries:
                    truncated = True
                    break
            if truncated:
                break
        lines = [f"{'[D]' if e['dir'] else '   '} {Path(e['path']).relative_to(root)}"
                 f"{'' if e['dir'] else '  ' + human_size(e['size'])}" for e in entries]
        view = f"{root}\n" + "\n".join(lines) + ("\n[truncated]" if truncated else "")
        return self.ok(f"{len(entries)} entries in {root.name or root}", {"root": str(root), "entries": entries,
                                                                           "truncated": truncated}, model_view=view)


class SearchInput(ToolInput):
    root: str = Field(".", description="Directory to search in")
    name: str | None = Field(None, description="glob or substring for file names, e.g. '*report*.pdf'")
    content: str | None = Field(None, description="text or regex that file contents must contain")
    regex: bool = False
    max_results: int = Field(50, ge=1, le=1000)
    max_depth: int = Field(12, ge=1, le=64)
    file_glob: str = Field("*", description="restrict content search to these files, e.g. '*.py'")


class FsSearch(Tool):
    name = "fs.search"
    description = ("Find files by name (glob/substring) and/or content (text or regex) under a directory. Returns "
                   "paths and matching lines. Skips .git, node_modules and similar folders.")
    input_model = SearchInput
    capabilities = ("fs.read",)
    categories = ("fs", "dev")
    output_trust = TrustLevel.UNTRUSTED_EXTERNAL
    data_class = "files"
    timeout = 120.0
    sensitive_args = {"root": "path"}

    def assess(self, args: SearchInput, ctx: ToolContext) -> RiskAssessment:
        return _assess_paths(ctx, [args.root], PathOp.LIST)

    def progress_line(self, args: SearchInput) -> str | None:
        return "Searching files."

    async def run(self, args: SearchInput, ctx: ToolContext) -> ToolResult:
        if not args.name and not args.content:
            raise ToolInputError("give `name` and/or `content`")
        chk = check_path(ctx, args.root, PathOp.LIST)
        if not chk.path.is_dir():
            raise ToolError(f"Not a directory: {chk.path}", "NotFound")
        return await asyncio.to_thread(search_files, chk.path, args, ctx, self)


_SKIP = {".git", "node_modules", "__pycache__", ".venv", "venv", ".tox", ".mypy_cache", ".pytest_cache", ".ruff_cache",
         "dist", "build", ".next", "target", "$recycle.bin", "appdata"}


def search_files(root: Path, args: SearchInput, ctx: ToolContext, tool: Tool) -> ToolResult:
    name_pat = args.name
    if name_pat and not any(ch in name_pat for ch in "*?["):
        name_pat = f"*{name_pat}*"
    content_re: re.Pattern[str] | None = None
    if args.content:
        content_re = re.compile(args.content if args.regex else re.escape(args.content), re.IGNORECASE)
    results: list[dict[str, Any]] = []
    scanned = 0
    root_depth = len(root.parts)
    for dirpath, dirs, files in os.walk(root):
        ctx.cancel.raise_if_cancelled()
        depth = len(Path(dirpath).parts) - root_depth
        dirs[:] = [d for d in dirs if d.lower() not in _SKIP and not d.startswith(".")] if depth < args.max_depth else []
        for fname in files:
            if name_pat and not fnmatch.fnmatch(fname.lower(), name_pat.lower()):
                continue
            full = Path(dirpath) / fname
            if ctx.services.path_guard.check(str(full), PathOp.READ).denied:
                continue
            scanned += 1
            if content_re is None:
                results.append({"path": str(full)})
            else:
                if not fnmatch.fnmatch(fname.lower(), args.file_glob.lower()):
                    continue
                try:
                    if full.stat().st_size > 5 * 1024 * 1024 or looks_binary(full):
                        continue
                    hits: list[dict[str, Any]] = []
                    with full.open("r", encoding="utf-8", errors="replace") as fh:
                        for ln, line in enumerate(fh, 1):
                            if content_re.search(line):
                                hits.append({"line": ln, "text": line.strip()[:200]})
                                if len(hits) >= 5:
                                    break
                except OSError:
                    continue
                if hits:
                    results.append({"path": str(full), "matches": hits})
            if len(results) >= args.max_results:
                break
        if len(results) >= args.max_results:
            break
    lines: list[str] = []
    for r in results:
        lines.append(r["path"])
        for m in r.get("matches", []):
            lines.append(f"    {m['line']}: {m['text']}")
    summary = f"Found {len(results)} match{'es' if len(results) != 1 else ''}" + (f" (scanned {scanned} files)" if content_re else "")
    return tool.ok(summary, {"root": str(root), "results": results, "scanned": scanned},
                   model_view=summary + "\n" + "\n".join(lines), source=f"fs-search:{root}")


class InfoInput(ToolInput):
    path: str
    hash: bool = True


class FsInfo(Tool):
    name = "fs.info"
    description = "Metadata for a file or folder: size, timestamps, type and SHA-256."
    input_model = InfoInput
    capabilities = ("fs.read",)
    categories = ("fs", "dev", "documents")
    sensitive_args = {"path": "path"}

    def assess(self, args: InfoInput, ctx: ToolContext) -> RiskAssessment:
        return _assess_paths(ctx, [args.path], PathOp.READ)

    async def run(self, args: InfoInput, ctx: ToolContext) -> ToolResult:
        p = check_path(ctx, args.path, PathOp.READ).path
        if not p.exists():
            return self.ok(f"{p} does not exist", {"path": str(p), "exists": False})
        st = p.stat()
        data: dict[str, Any] = {
            "path": str(p), "exists": True, "is_dir": p.is_dir(), "size": st.st_size,
            "created": dt.datetime.fromtimestamp(st.st_ctime).isoformat(timespec="seconds"),
            "modified": dt.datetime.fromtimestamp(st.st_mtime).isoformat(timespec="seconds"),
            "extension": p.suffix.lower(),
        }
        if p.is_file() and args.hash:
            data["sha256"] = await asyncio.to_thread(sha256_file, p)
        if p.is_dir():
            try:
                data["entries"] = sum(1 for _ in p.iterdir())
            except OSError:
                data["entries"] = None
        return self.ok(f"{p.name}: {'folder' if p.is_dir() else human_size(st.st_size)}", data)


# ---------------------------------------------------------------- mkdir / copy / move / delete
class MkdirInput(ToolInput):
    path: str


class FsMkdir(Tool):
    name = "fs.mkdir"
    description = "Create a folder (and parents)."
    input_model = MkdirInput
    capabilities = ("fs.write",)
    base_risk = RiskLevel.MEDIUM
    side_effects = SideEffect.LOCAL
    categories = ("fs", "dev")
    sensitive_args = {"path": "path"}

    def assess(self, args: MkdirInput, ctx: ToolContext) -> RiskAssessment:
        return _assess_paths(ctx, [args.path], PathOp.CREATE)

    def describe(self, args: MkdirInput) -> str:
        return f"create folder {args.path}"

    async def run(self, args: MkdirInput, ctx: ToolContext) -> ToolResult:
        p = check_path(ctx, args.path, PathOp.CREATE).path
        p.mkdir(parents=True, exist_ok=True)
        return self.ok(f"Folder ready: {p}", {"path": str(p)})

    async def verify(self, args: MkdirInput, result: ToolResult, ctx: ToolContext) -> VerificationResult:
        return VerificationResult.from_checks([Check(name="folder exists", passed=Path(result.data["path"]).is_dir())])


class CopyMoveInput(ToolInput):
    src: str
    dst: str
    overwrite: bool = False


class FsCopy(Tool):
    name = "fs.copy"
    description = "Copy a file or folder to a destination path."
    input_model = CopyMoveInput
    capabilities = ("fs.write",)
    base_risk = RiskLevel.MEDIUM
    side_effects = SideEffect.LOCAL
    categories = ("fs",)
    sensitive_args = {"src": "path", "dst": "path"}
    timeout = 600.0

    def assess(self, args: CopyMoveInput, ctx: ToolContext) -> RiskAssessment:
        a = _assess_paths(ctx, [args.src], PathOp.READ)
        a.merge(_assess_paths(ctx, [args.dst], PathOp.WRITE))
        src = ctx.services.path_guard.check(args.src, PathOp.READ).path
        if src.is_dir():
            count = sum(len(f) for _, _, f in os.walk(src))
            a.facts.file_count = count
            bulk_escalation(a, count, ctx.services.settings.bulk_threshold)
        if args.overwrite:
            a.raise_to(a.level.escalate(1), "may overwrite existing files")
        return a

    def describe(self, args: CopyMoveInput) -> str:
        return f"copy {args.src} to {args.dst}"

    async def run(self, args: CopyMoveInput, ctx: ToolContext) -> ToolResult:
        src = check_path(ctx, args.src, PathOp.READ).path
        dst = check_path(ctx, args.dst, PathOp.WRITE).path
        if not src.exists():
            raise ToolError(f"Not found: {src}", "NotFound")
        if dst.exists() and dst.is_dir() and src.is_file():
            dst = dst / src.name
        if dst.exists() and not args.overwrite:
            raise ToolError(f"{dst} exists (set overwrite=true)", "Exists")
        if src.is_dir():
            await asyncio.to_thread(shutil.copytree, src, dst, dirs_exist_ok=args.overwrite)
        else:
            dst.parent.mkdir(parents=True, exist_ok=True)
            await asyncio.to_thread(shutil.copy2, src, dst)
        return self.ok(f"Copied {src.name} to {dst}", {"src": str(src), "dst": str(dst), "is_dir": src.is_dir(),
                                                         "sha256": sha256_file(dst) if dst.is_file() else None})

    async def verify(self, args: CopyMoveInput, result: ToolResult, ctx: ToolContext) -> VerificationResult:
        dst = Path(result.data["dst"])
        checks = [Check(name="destination exists", passed=dst.exists())]
        if result.data.get("sha256"):
            checks.append(Check(name="copy hash matches source", passed=sha256_file(Path(result.data["src"])) == result.data["sha256"]))
        return VerificationResult.from_checks(checks)


class FsMove(Tool):
    name = "fs.move"
    description = "Move or rename a file or folder."
    input_model = CopyMoveInput
    capabilities = ("fs.move",)
    base_risk = RiskLevel.HIGH
    side_effects = SideEffect.LOCAL
    categories = ("fs",)
    sensitive_args = {"src": "path", "dst": "path"}

    def assess(self, args: CopyMoveInput, ctx: ToolContext) -> RiskAssessment:
        a = _assess_paths(ctx, [args.src], PathOp.MOVE)
        a.merge(_assess_paths(ctx, [args.dst], PathOp.WRITE))
        return a

    def describe(self, args: CopyMoveInput) -> str:
        return f"move {args.src} to {args.dst}"

    async def run(self, args: CopyMoveInput, ctx: ToolContext) -> ToolResult:
        src = check_path(ctx, args.src, PathOp.MOVE).path
        dst = check_path(ctx, args.dst, PathOp.WRITE).path
        if not src.exists():
            raise ToolError(f"Not found: {src}", "NotFound")
        if dst.exists() and dst.is_dir():
            dst = dst / src.name
        if dst.exists() and not args.overwrite:
            raise ToolError(f"{dst} exists (set overwrite=true)", "Exists")
        dst.parent.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(shutil.move, str(src), str(dst))
        return self.ok(f"Moved {src.name} to {dst}", {"src": str(src), "dst": str(dst)})

    async def verify(self, args: CopyMoveInput, result: ToolResult, ctx: ToolContext) -> VerificationResult:
        return VerificationResult.from_checks([
            Check(name="destination exists", passed=Path(result.data["dst"]).exists()),
            Check(name="source gone", passed=not Path(result.data["src"]).exists()),
        ])


class DeleteInput(ToolInput):
    paths: list[str] = Field(min_length=1, max_length=5000)
    permanent: bool = Field(False, description="Bypass the Recycle Bin (CRITICAL, needs typed confirmation)")


class FsDelete(Tool):
    name = "fs.delete"
    description = "Delete files or folders. Default: move to the Recycle Bin. permanent=true is CRITICAL."
    input_model = DeleteInput
    capabilities = ("fs.delete",)
    base_risk = RiskLevel.HIGH
    side_effects = SideEffect.LOCAL
    categories = ("fs",)
    sensitive_args = {"paths": "path"}
    timeout = 300.0

    def assess(self, args: DeleteInput, ctx: ToolContext) -> RiskAssessment:
        op = PathOp.DELETE_PERMANENT if args.permanent else PathOp.DELETE
        a = _assess_paths(ctx, args.paths, op)
        count = 0
        for raw in args.paths:
            p = ctx.services.path_guard.check(raw, op).path
            if p.is_dir():
                count += sum(len(f) + len(d) for _, d, f in os.walk(p)) + 1
            else:
                count += 1
        a.facts.file_count = count
        bulk_escalation(a, count, ctx.services.settings.bulk_threshold)
        if args.permanent:
            a.raise_to(RiskLevel.CRITICAL, "permanent deletion (bypasses the Recycle Bin)")
        return a

    def describe(self, args: DeleteInput) -> str:
        where = "permanently delete" if args.permanent else "move to the Recycle Bin"
        shown = ", ".join(args.paths[:3]) + (f" and {len(args.paths) - 3} more" if len(args.paths) > 3 else "")
        return f"{where}: {shown}"

    async def run(self, args: DeleteInput, ctx: ToolContext) -> ToolResult:
        op = PathOp.DELETE_PERMANENT if args.permanent else PathOp.DELETE
        targets = [check_path(ctx, raw, op).path for raw in args.paths]
        missing = [str(t) for t in targets if not t.exists()]
        if missing:
            raise ToolError(f"Not found: {', '.join(missing[:5])}", "NotFound")

        def do() -> None:
            if args.permanent:
                for t in targets:
                    if t.is_dir():
                        shutil.rmtree(t)
                    else:
                        t.unlink()
            else:
                from send2trash import send2trash

                send2trash([str(t) for t in targets])

        await asyncio.to_thread(do)
        return self.ok(f"{'Deleted' if args.permanent else 'Moved to Recycle Bin'}: {len(targets)} item(s)",
                       {"paths": [str(t) for t in targets], "permanent": args.permanent})

    async def verify(self, args: DeleteInput, result: ToolResult, ctx: ToolContext) -> VerificationResult:
        return VerificationResult.from_checks([Check(name=f"{Path(p).name} removed", passed=not Path(p).exists())
                                               for p in result.data["paths"]])


# ---------------------------------------------------------------- diff
class DiffInput(ToolInput):
    a: str = Field(description="first file path")
    b: str = Field(description="second file path")
    context: int = Field(3, ge=0, le=20)


class FsDiff(Tool):
    name = "fs.diff"
    description = "Compare two files: identical-hash check plus a unified text diff."
    input_model = DiffInput
    capabilities = ("fs.read",)
    categories = ("fs", "dev")
    output_trust = TrustLevel.UNTRUSTED_EXTERNAL
    data_class = "files"
    sensitive_args = {"a": "path", "b": "path"}

    def assess(self, args: DiffInput, ctx: ToolContext) -> RiskAssessment:
        return _assess_paths(ctx, [args.a, args.b], PathOp.READ)

    async def run(self, args: DiffInput, ctx: ToolContext) -> ToolResult:
        pa = check_path(ctx, args.a, PathOp.READ).path
        pb = check_path(ctx, args.b, PathOp.READ).path
        ha, hb = sha256_file(pa), sha256_file(pb)
        if ha == hb and ha is not None:
            return self.ok("Files are identical", {"identical": True, "sha256": ha})
        if looks_binary(pa) or looks_binary(pb):
            return self.ok("Binary files differ", {"identical": False, "sha256_a": ha, "sha256_b": hb})
        ta = pa.read_text("utf-8", errors="replace").splitlines(True)
        tb = pb.read_text("utf-8", errors="replace").splitlines(True)
        diff = "".join(difflib.unified_diff(ta, tb, str(pa), str(pb), n=args.context))
        changed = sum(1 for line in diff.splitlines() if line[:1] in "+-" and not line.startswith(("+++", "---")))
        return self.ok(f"Files differ ({changed} changed lines)", {"identical": False, "sha256_a": ha, "sha256_b": hb,
                                                                   "diff": diff[:40000]}, model_view=diff[:8000])


TOOLS: list[type[Tool]] = [FsRead, FsWrite, FsEdit, FsList, FsSearch, FsInfo, FsMkdir, FsCopy, FsMove, FsDelete, FsDiff]
