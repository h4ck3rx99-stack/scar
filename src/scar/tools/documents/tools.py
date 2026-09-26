"""Document tools (C9.9): read PDF/DOCX/text/CSV/JSON/YAML/images; write MD/TXT/DOCX/PDF with citation checks."""

from __future__ import annotations

import asyncio
from pathlib import Path

from pydantic import Field

from scar.core.errors import ToolError
from scar.core.types import Check, RiskLevel, SideEffect, ToolResult, TrustLevel, VerificationResult
from scar.security.path_guard import PathOp
from scar.security.risk import RiskAssessment
from scar.tools.base import Tool, ToolContext, ToolInput
from scar.tools.documents.readers import read_any
from scar.tools.documents.writers import write_document
from scar.tools.fs.common import backup_file, check_path, sha256_file
from scar.tools.web.citations import check_citations, fetched_urls


class ReadInput(ToolInput):
    path: str
    start_char: int = Field(0, ge=0)
    max_chars: int = Field(15000, ge=200, le=200000)


class DocumentsRead(Tool):
    name = "documents.read"
    description = ("Extract text and metadata from a document: PDF, DOCX, TXT, MD, CSV, JSON, YAML, source code, logs; "
                   "image metadata. Large documents are returned in character ranges.")
    input_model = ReadInput
    capabilities = ("fs.read",)
    categories = ("documents", "fs")
    output_trust = TrustLevel.UNTRUSTED_EXTERNAL
    data_class = "files"
    timeout = 120.0
    sensitive_args = {"path": "path"}

    def assess(self, args: ReadInput, ctx: ToolContext) -> RiskAssessment:
        chk = ctx.services.path_guard.check(args.path, PathOp.READ)
        a = RiskAssessment(chk.risk, chk.reasons if chk.risk > RiskLevel.LOW else [])
        if chk.denied:
            a.deny("; ".join(chk.reasons), "secret_paths")
        a.facts.paths.append(chk.canonical)
        return a

    def progress_line(self, args: ReadInput) -> str | None:
        return f"Reading {Path(args.path).name}."

    async def run(self, args: ReadInput, ctx: ToolContext) -> ToolResult:
        p = check_path(ctx, args.path, PathOp.READ).path
        if not p.is_file():
            raise ToolError(f"File not found: {p}", "NotFound")
        text, meta = await asyncio.to_thread(read_any, p)
        total = len(text)
        chunk = text[args.start_char : args.start_char + args.max_chars]
        end = args.start_char + len(chunk)
        more = f"\n…[{total - end} more characters; call again with start_char={end}]" if end < total else ""
        meta.update({"path": str(p), "chars": total, "range": [args.start_char, end]})
        view = f"{p.name} {({k: v for k, v in meta.items() if k in ('pages', 'title', 'rows_read', 'width', 'height')})}\n{chunk}{more}"
        return self.ok(f"Read {p.name} ({total} characters)", {**meta, "text": chunk}, model_view=view, source=f"file:{p}")


class WriteInput(ToolInput):
    path: str = Field(description="output path ending in .md, .txt, .docx or .pdf")
    title: str = ""
    content: str = Field(description="body in Markdown (headings, bullets, paragraphs, code)")
    overwrite: bool = False
    sources: list[str] = Field(default_factory=list, description="URLs cited; each must have been fetched in this task")


class DocumentsWrite(Tool):
    name = "documents.write"
    description = ("Save a document (MD/TXT/DOCX/PDF) from Markdown content. Any URL in the content or sources must have "
                   "been fetched in this task — fabricated citations are refused.")
    input_model = WriteInput
    capabilities = ("fs.write",)
    base_risk = RiskLevel.MEDIUM
    side_effects = SideEffect.LOCAL
    categories = ("documents", "fs")
    timeout = 120.0
    sensitive_args = {"path": "path"}

    def assess(self, args: WriteInput, ctx: ToolContext) -> RiskAssessment:
        chk = ctx.services.path_guard.check(args.path, PathOp.WRITE)
        a = RiskAssessment(max(chk.risk, RiskLevel.MEDIUM), chk.reasons if chk.risk > RiskLevel.MEDIUM else [])
        if chk.denied:
            a.deny("; ".join(chk.reasons), "secret_paths")
        if args.overwrite and Path(chk.canonical).exists():
            a.raise_to(a.level.escalate(1), "overwrites an existing document")
        a.facts.paths.append(chk.canonical)
        return a

    def describe(self, args: WriteInput) -> str:
        return f"save document {args.path} ({len(args.content)} characters)"

    def progress_line(self, args: WriteInput) -> str | None:
        return f"Saving {Path(args.path).name}."

    async def run(self, args: WriteInput, ctx: ToolContext) -> ToolResult:
        p = check_path(ctx, args.path, PathOp.WRITE).path
        if p.suffix.lower() not in (".md", ".markdown", ".txt", ".docx", ".pdf"):
            raise ToolError("document must end in .md, .txt, .docx or .pdf", "InvalidInput")
        if p.exists() and not args.overwrite:
            raise ToolError(f"{p} exists (set overwrite=true)", "Exists")
        body = args.content + ("\n\n## Sources\n" + "\n".join(f"- {u}" for u in args.sources) if args.sources and
                               not all(u in args.content for u in args.sources) else "")
        allowed = fetched_urls(ctx.services.db, ctx.task_id, ctx.services.extras.get("fetch_log", {}).get(ctx.task_id, []))
        user_urls = set()
        if ctx.scope is not None:
            _ok, from_user = check_citations(ctx.scope.user_text, set())
            user_urls = {u.lower().rstrip("/") for u in from_user}
        verified, unverified = check_citations(body, allowed)
        unverified = [u for u in unverified if u.lower().rstrip("/") not in user_urls]
        if unverified:
            raise ToolError("refusing to save: these cited URLs were not fetched in this task (possible fabricated "
                            f"sources): {', '.join(unverified[:5])}. Fetch them with web.fetch or remove them.",
                            "UnverifiedCitation")
        if p.exists():
            backup_file(ctx, p)
        p.parent.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(write_document, p, args.title, body)
        return self.ok(f"Saved {p.name}", {"path": str(p), "sha256": sha256_file(p), "size": p.stat().st_size,
                                           "citations_verified": verified})

    async def verify(self, args: WriteInput, result: ToolResult, ctx: ToolContext) -> VerificationResult:
        p = Path(result.data["path"])
        checks = [Check(name="document exists", passed=p.is_file() and p.stat().st_size > 0),
                  Check(name="hash matches", passed=sha256_file(p) == result.data["sha256"])]
        if p.is_file():
            try:
                text, _ = await asyncio.to_thread(read_any, p)
                probe = " ".join(args.content.split()[:6]).replace("#", "").replace("*", "").strip()
                if probe:
                    first_word = probe.split()[0]
                    checks.append(Check(name="content readable back", passed=first_word.lower() in text.lower()))
            except ToolError as exc:
                checks.append(Check(name="content readable back", passed=False, detail=str(exc)))
        return VerificationResult.from_checks(checks, {"path": str(p)})


TOOLS: list[type[Tool]] = [DocumentsRead, DocumentsWrite]
