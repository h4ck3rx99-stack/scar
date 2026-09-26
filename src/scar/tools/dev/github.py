"""GitHub via the REST API (fine-grained token in GITHUB_TOKEN): repos, issues, pull requests, CI status."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Any, Literal

import httpx
from pydantic import Field

from scar.core.errors import CapabilityUnavailable, ToolError
from scar.core.types import Check, RiskLevel, SideEffect, ToolResult, TrustLevel, VerificationResult
from scar.security.risk import RiskAssessment
from scar.tools.base import Tool, ToolContext, ToolInput

API = "https://api.github.com"
DOC = "docs/integrations/github.md"


class GitHubClient:
    def __init__(self, secrets: Any, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.secrets = secrets
        self.transport = transport

    def _headers(self) -> dict[str, str]:
        tok = self.secrets.get("GITHUB_TOKEN")
        if tok is None:
            raise CapabilityUnavailable("GitHub is not configured: set GITHUB_TOKEN (fine-grained token)", DOC)
        return {"Authorization": f"Bearer {tok.get_secret_value()}", "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "SCAR"}

    async def request(self, method: str, path: str, **kwargs: Any) -> Any:
        async with httpx.AsyncClient(base_url=API, headers=self._headers(), timeout=30.0, transport=self.transport) as c:
            try:
                r = await c.request(method, path, **kwargs)
            except httpx.HTTPError as exc:
                raise ToolError(f"GitHub unreachable: {exc}", "Network") from exc
        if r.status_code == 401:
            raise CapabilityUnavailable("GITHUB_TOKEN was rejected (expired or revoked)", DOC)
        if r.status_code == 403 and r.headers.get("x-ratelimit-remaining") == "0":
            raise ToolError(f"GitHub rate limit reached; resets at {r.headers.get('x-ratelimit-reset')}", "RateLimited")
        if r.status_code >= 400:
            msg = r.json().get("message", r.text[:200]) if r.headers.get("content-type", "").startswith("application/json") else r.text[:200]
            raise ToolError(f"GitHub {r.status_code}: {msg}", "GitHubError")
        return r.json() if r.content else {}


def repo_from_remote(path: str) -> str | None:
    try:
        out = subprocess.run(["git", "remote", "get-url", "origin"], cwd=path, capture_output=True, text=True, timeout=10,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None
    m = re.search(r"github\.com[:/]([^/]+)/([^/.]+?)(?:\.git)?$", out)
    return f"{m.group(1)}/{m.group(2)}" if m else None


def _slug(ctx: ToolContext, repo: str | None) -> str:
    if repo and re.fullmatch(r"[\w.-]+/[\w.-]+", repo):
        return repo
    base = repo or ctx.services.extras.get("cwd") or "."
    if Path(base).is_dir():
        slug = repo_from_remote(base)
        if slug:
            return slug
    raise ToolError("specify repo as owner/name (no GitHub remote found)", "InvalidInput")


class RepoInput(ToolInput):
    repo: str | None = Field(None, description="owner/name, or a local clone path")


class IssuesInput(RepoInput):
    action: Literal["list", "get", "create", "comment"] = "list"
    number: int | None = None
    title: str | None = None
    body: str | None = None
    state: Literal["open", "closed", "all"] = "open"
    kind: Literal["issue", "pr"] = "issue"
    head: str | None = Field(None, description="PR source branch (create PR)")
    base: str | None = Field(None, description="PR target branch (create PR)")


class GitHubIssues(Tool):
    name = "github.issues"
    description = "GitHub issues and pull requests: list, get, create, comment (creating/commenting publishes — HIGH)."
    input_model = IssuesInput
    capabilities = ("github",)
    base_risk = RiskLevel.LOW
    side_effects = SideEffect.EXTERNAL
    categories = ("dev", "git")
    output_trust = TrustLevel.UNTRUSTED_EXTERNAL
    sensitive_args = {"body": "body", "title": "body"}

    def assess(self, args: IssuesInput, ctx: ToolContext) -> RiskAssessment:
        if args.action in ("create", "comment"):
            a = RiskAssessment(RiskLevel.HIGH, ["publishes to GitHub"])
            a.facts.domains.append("github.com")
            a.facts.external_destination = True
            return a
        return RiskAssessment(RiskLevel.LOW)

    def describe(self, args: IssuesInput) -> str:
        target = args.repo or "this repository"
        if args.action == "create":
            return f"create a GitHub {'pull request' if args.kind == 'pr' else 'issue'} in {target}: '{args.title}'"
        if args.action == "comment":
            return f"comment on #{args.number} in {target}"
        return f"{args.action} GitHub {args.kind}s in {target}"

    async def run(self, args: IssuesInput, ctx: ToolContext) -> ToolResult:
        gh: GitHubClient = ctx.services.github
        slug = _slug(ctx, args.repo)
        kind_path = "pulls" if args.kind == "pr" else "issues"
        if args.action == "list":
            items = await gh.request("GET", f"/repos/{slug}/{kind_path}", params={"state": args.state, "per_page": 30})
            rows = [{"number": i["number"], "title": i["title"], "state": i["state"], "user": i["user"]["login"],
                     "url": i["html_url"]} for i in items if args.kind == "pr" or "pull_request" not in i]
            return self.ok(f"{len(rows)} {args.kind}s", {"items": rows},
                           model_view="\n".join(f"#{r['number']} [{r['state']}] {r['title']} ({r['user']})" for r in rows))
        if args.number is None and args.action in ("get", "comment"):
            raise ToolError("number is required", "InvalidInput")
        if args.action == "get":
            item = await gh.request("GET", f"/repos/{slug}/{kind_path}/{args.number}")
            comments = await gh.request("GET", f"/repos/{slug}/issues/{args.number}/comments", params={"per_page": 30})
            text = f"#{item['number']} {item['title']} [{item['state']}]\n{item.get('body') or ''}\n\n" + "\n\n".join(
                f"{c['user']['login']}: {c['body']}" for c in comments)
            return self.ok(f"#{item['number']} {item['title']}", {"item": {k: item.get(k) for k in ("number", "title", "state", "html_url")},
                                                                 "comments": len(comments)}, model_view=text)
        if args.action == "comment":
            c = await gh.request("POST", f"/repos/{slug}/issues/{args.number}/comments", json={"body": args.body or ""})
            return self.ok(f"Commented on #{args.number}", {"id": c["id"], "url": c["html_url"], "slug": slug, "number": args.number})
        if not args.title:
            raise ToolError("title is required", "InvalidInput")
        if args.kind == "pr":
            if not args.head or not args.base:
                raise ToolError("head and base branches are required for a PR", "InvalidInput")
            item = await gh.request("POST", f"/repos/{slug}/pulls", json={"title": args.title, "body": args.body or "",
                                                                          "head": args.head, "base": args.base})
        else:
            item = await gh.request("POST", f"/repos/{slug}/issues", json={"title": args.title, "body": args.body or ""})
        return self.ok(f"Created #{item['number']}", {"number": item["number"], "url": item["html_url"], "slug": slug,
                                                       "kind": args.kind})

    async def verify(self, args: IssuesInput, result: ToolResult, ctx: ToolContext) -> VerificationResult | None:
        if args.action not in ("create", "comment"):
            return None
        gh: GitHubClient = ctx.services.github
        if args.action == "comment":
            c = await gh.request("GET", f"/repos/{result.data['slug']}/issues/comments/{result.data['id']}")
            return VerificationResult.from_checks([Check(name="comment exists", passed=c.get("id") == result.data["id"])])
        path = "pulls" if args.kind == "pr" else "issues"
        item = await gh.request("GET", f"/repos/{result.data['slug']}/{path}/{result.data['number']}")
        return VerificationResult.from_checks([Check(name="created item exists", passed=item.get("title") == args.title)])


class CiInput(RepoInput):
    ref: str = Field("HEAD", description="branch, tag or commit SHA")


class GitHubCi(Tool):
    name = "github.ci"
    description = "CI status (check runs) for a branch or commit on GitHub."
    input_model = CiInput
    capabilities = ("github",)
    categories = ("dev", "git")
    output_trust = TrustLevel.UNTRUSTED_EXTERNAL

    async def run(self, args: CiInput, ctx: ToolContext) -> ToolResult:
        gh: GitHubClient = ctx.services.github
        slug = _slug(ctx, args.repo)
        ref = args.ref
        if ref == "HEAD":
            try:
                ref = subprocess.run(["git", "rev-parse", "HEAD"], cwd=args.repo or ctx.services.extras.get("cwd") or ".",
                                     capture_output=True, text=True, timeout=10).stdout.strip() or "HEAD"
            except (OSError, subprocess.SubprocessError):
                ref = "HEAD"
        data = await gh.request("GET", f"/repos/{slug}/commits/{ref}/check-runs", params={"per_page": 50})
        runs = [{"name": r["name"], "status": r["status"], "conclusion": r.get("conclusion"), "url": r.get("html_url")}
                for r in data.get("check_runs", [])]
        failed = [r for r in runs if r["conclusion"] in ("failure", "timed_out", "cancelled")]
        pending = [r for r in runs if r["status"] != "completed"]
        summary = (f"{len(failed)} check(s) failing" if failed else f"{len(pending)} check(s) running" if pending
                   else f"All {len(runs)} checks passed" if runs else "No CI checks found")
        return self.ok(summary, {"ref": ref, "runs": runs},
                       model_view="\n".join(f"{r['name']}: {r['status']} {r['conclusion'] or ''}" for r in runs))


TOOLS: list[type[Tool]] = [GitHubIssues, GitHubCi]
