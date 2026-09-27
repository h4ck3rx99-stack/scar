"""Every non-GUI tool that no other test exercises, run through the real pipeline (policy, taint, verification) in the
sandbox. Each test checks the effect on disk/state, not just the tool's own report."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from scar.core.types import ToolStatus
from scar.security.grants import Grant, GrantKind, GrantScope, UserAuthority


async def _ok(pipeline, tool: str, args: dict, ctx):  # type: ignore[no-untyped-def]
    obs = await pipeline.execute(tool, args, ctx)
    assert obs.result.status == ToolStatus.OK, (tool, obs.result.summary, ctx.task.action_history[-1].decision_reason)
    return obs.result


def _grant(services, *tools: str) -> None:  # type: ignore[no-untyped-def]
    for t in tools:
        services.grants.create(Grant(kind=GrantKind.TOOL, tool=t, scope=GrantScope.SESSION), UserAuthority("cli"))


async def test_filesystem_copy_move_mkdir_info_diff(runtime_parts, ctx_factory, sandbox: Path) -> None:
    p = runtime_parts["pipeline"]
    ctx = ctx_factory(f"organise files in {sandbox}")
    _grant(runtime_parts["services"], "fs.move")  # moving files is HIGH by design (C4 risk table)
    (sandbox / "a.txt").write_text("one\ntwo\n")
    await _ok(p, "fs.mkdir", {"path": str(sandbox / "sub")}, ctx)
    assert (sandbox / "sub").is_dir()
    await _ok(p, "fs.copy", {"src": str(sandbox / "a.txt"), "dst": str(sandbox / "sub" / "b.txt")}, ctx)
    (sandbox / "sub" / "b.txt").write_text("one\nthree\n")
    diff = await _ok(p, "fs.diff", {"a": str(sandbox / "a.txt"), "b": str(sandbox / "sub" / "b.txt")}, ctx)
    assert "-two" in (diff.model_view or "") and "+three" in (diff.model_view or "")
    await _ok(p, "fs.move", {"src": str(sandbox / "sub" / "b.txt"), "dst": str(sandbox / "c.txt")}, ctx)
    assert (sandbox / "c.txt").exists() and not (sandbox / "sub" / "b.txt").exists()
    info = await _ok(p, "fs.info", {"path": str(sandbox / "c.txt"), "hash": True}, ctx)
    assert info.data.get("size") == (sandbox / "c.txt").stat().st_size


async def test_documents_write_then_read_docx_and_pdf(runtime_parts, ctx_factory, sandbox: Path) -> None:
    p = runtime_parts["pipeline"]
    ctx = ctx_factory(f"write a report in {sandbox}")
    body = "# Findings\n\n- Alpha grew 12%\n- Beta was flat\n\nConclusion: keep Alpha."
    for ext in ("docx", "pdf", "md"):
        path = sandbox / f"report.{ext}"
        await _ok(p, "documents.write", {"path": str(path), "title": "Q3", "content": body}, ctx)
        back = await _ok(p, "documents.read", {"path": str(path)}, ctx)
        assert "Alpha grew 12%" in back.data["text"], ext


async def test_documents_write_refuses_unfetched_citation(runtime_parts, ctx_factory, sandbox: Path) -> None:
    p = runtime_parts["pipeline"]
    ctx = ctx_factory(f"write a report in {sandbox}")
    obs = await p.execute("documents.write", {"path": str(sandbox / "r.md"), "content": "See https://made-up.example/paper"}, ctx)
    assert obs.result.status == ToolStatus.ERROR and obs.result.error_type == "UnverifiedCitation"
    assert not (sandbox / "r.md").exists()


@pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")
async def test_git_status_diff_commit_branch_log_stash(runtime_parts, ctx_factory, sandbox: Path) -> None:
    services, p = runtime_parts["services"], runtime_parts["pipeline"]
    repo = sandbox / "repo"
    repo.mkdir()
    for cmd in (["init", "-q", "-b", "main"], ["config", "user.email", "t@example.test"], ["config", "user.name", "T"]):
        subprocess.run(["git", *cmd], cwd=repo, check=True)
    (repo / "x.py").write_text("print(1)\n")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=repo, check=True)
    ctx = ctx_factory(f"work on the repo in {repo}")
    _grant(services, "git.commit", "git.branch", "git.stash")
    (repo / "x.py").write_text("print(2)\n")
    st = await _ok(p, "git.status", {"repo": str(repo)}, ctx)
    assert "x.py" in (st.model_view or "")
    d = await _ok(p, "git.diff", {"repo": str(repo)}, ctx)
    assert "+print(2)" in (d.model_view or "")
    await _ok(p, "git.commit", {"repo": str(repo), "message": "bump", "all": True}, ctx)
    log = await _ok(p, "git.log", {"repo": str(repo), "n": 5}, ctx)
    assert "bump" in (log.model_view or "")
    await _ok(p, "git.branch", {"repo": str(repo), "action": "create", "name": "feature"}, ctx)
    branches = subprocess.run(["git", "branch"], cwd=repo, capture_output=True, text=True).stdout
    assert "feature" in branches
    (repo / "x.py").write_text("print(3)\n")
    await _ok(p, "git.stash", {"repo": str(repo), "action": "push", "message": "wip"}, ctx)
    assert (repo / "x.py").read_text() == "print(2)\n"


async def test_process_start_list_inspect_wait_stop(runtime_parts, ctx_factory, sandbox: Path) -> None:
    services, p = runtime_parts["services"], runtime_parts["pipeline"]
    ctx = ctx_factory(f"run a python script in {sandbox}, check on it, then stop the process")
    _grant(services, "process.start", "process.stop")
    started = await _ok(p, "process.start", {"program": sys.executable, "args": ["-c", "import time; time.sleep(60)"],
                                             "cwd": str(sandbox)}, ctx)
    pid = int(started.data["pid"])
    listed = await _ok(p, "process.list", {"name": "python"}, ctx)
    assert any(int(r["pid"]) == pid for r in listed.data["processes"])
    await _ok(p, "process.inspect", {"pid": pid}, ctx)
    waited = await p.execute("process.wait", {"pid": pid, "timeout_s": 1}, ctx)
    assert waited.result.status in (ToolStatus.OK, ToolStatus.TIMEOUT, ToolStatus.ERROR)
    await _ok(p, "process.stop", {"pid": pid, "force": True}, ctx)
    import psutil

    assert not psutil.pid_exists(pid) or psutil.Process(pid).status() == psutil.STATUS_ZOMBIE


async def test_schedule_reminder_task_list_cancel(runtime_parts, ctx_factory) -> None:
    p = runtime_parts["pipeline"]
    ctx = ctx_factory("remind me in 2 hours to stretch, and every day at 9am check my disk space")
    rem = await _ok(p, "schedule.reminder", {"text": "stretch", "when": "in 2 hours"}, ctx)
    task = await _ok(p, "schedule.task", {"objective": "check my disk space", "when": "every day at 9am"}, ctx)
    listed = await _ok(p, "schedule.list", {}, ctx)
    rid, tid = rem.data["schedule"]["schedule_id"], task.data["schedule"]["schedule_id"]
    assert {rid, tid} <= {s["schedule_id"] for s in listed.data["schedules"]}
    await _ok(p, "schedule.cancel", {"id": rid}, ctx)
    after = await _ok(p, "schedule.list", {}, ctx)
    assert rid not in {s["schedule_id"] for s in after.data["schedules"] if s.get("status") == "active"}


async def test_monitor_list_and_cancel(runtime_parts, ctx_factory) -> None:
    p = runtime_parts["pipeline"]
    ctx = ctx_factory("watch process and tell me if it crashes")
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        started = await _ok(p, "monitor.start", {"kind": "process", "pid": proc.pid}, ctx)
        mid = started.data["id"]
        listed = await _ok(p, "monitor.list", {}, ctx)
        assert mid in {m["id"] for m in listed.data["monitors"]}
        await _ok(p, "monitor.cancel", {"id": mid}, ctx)
        listed = await _ok(p, "monitor.list", {}, ctx)
        assert all(m["status"] != "active" for m in listed.data["monitors"] if m["id"] == mid)
    finally:
        proc.kill()


async def test_memory_alias_recall_forget(runtime_parts, ctx_factory, sandbox: Path) -> None:
    p = runtime_parts["pipeline"]
    ctx = ctx_factory(f"call {sandbox} my scratch folder; what do you remember about scratch; then forget it")
    await _ok(p, "memory.alias", {"alias": "scratch folder", "target": str(sandbox), "kind": "folder"}, ctx)
    rec = await _ok(p, "memory.recall", {"query": "scratch folder"}, ctx)
    assert any(str(sandbox) in m["text"] for m in rec.data["memories"])
    await _ok(p, "memory.forget", {"query": "scratch folder"}, ctx)
    rec = await _ok(p, "memory.recall", {"query": "scratch folder"}, ctx)
    assert not any(str(sandbox) in m["text"] for m in rec.data["memories"])


async def test_notify_send_is_recorded(runtime_parts, ctx_factory) -> None:
    services, p = runtime_parts["services"], runtime_parts["pipeline"]
    ctx = ctx_factory("notify me that the build finished")
    await _ok(p, "notify.send", {"title": "Build", "message": "The build finished"}, ctx)
    assert any(d.message == "The build finished" for d in services.notifier.delivered)


async def test_code_run_repo_map_dev_detect_tooling(runtime_parts, ctx_factory, sandbox: Path) -> None:
    services, p = runtime_parts["services"], runtime_parts["pipeline"]
    proj = sandbox / "proj"
    (proj / "pkg").mkdir(parents=True)
    (proj / "pkg" / "core.py").write_text("def area(r):\n    return 3.14 * r * r\n\n\nclass Shape:\n    pass\n")
    (proj / "pyproject.toml").write_text('[project]\nname = "proj"\nversion = "0.1"\n')
    ctx = ctx_factory(f"inspect the project in {proj} and run a quick python calculation")
    _grant(services, "code.run", "dev.tooling")
    run = await _ok(p, "code.run", {"language": "python", "code": "print(sum(range(10)))"}, ctx)
    assert "45" in (run.model_view or "") or run.data.get("stdout", "").strip() == "45"
    rm = await _ok(p, "code.repo_map", {"path": str(proj)}, ctx)
    assert "area" in (rm.model_view or "") and "Shape" in (rm.model_view or "")
    det = await _ok(p, "dev.detect", {"path": str(proj)}, ctx)
    assert "python" in str(det.data).lower()


async def test_apps_find_and_windows_list_are_read_only(runtime_parts, ctx_factory) -> None:
    from scar.tools.apps.index import AppIndex

    p = runtime_parts["pipeline"]
    runtime_parts["services"].app_index = AppIndex(runtime_parts["services"].db)
    ctx = ctx_factory("which apps and windows are there")
    found = await _ok(p, "apps.find", {"query": "notepad"}, ctx)
    assert found.data.get("apps") is not None
    wins = await _ok(p, "windows.list", {}, ctx)
    assert isinstance(wins.data.get("windows"), list)


_BROWSER_PAGE = """<html><head><title>Smoke</title></head><body>
<h1 id="top">Smoke page</h1>
<label for="size">Size</label><select id="size"><option>Small</option><option>Large</option></select>
<a href="/two">Go to two</a> <a href="/file.txt" download>Get file</a>
<div style="height:4000px"></div><p id="bottom">Bottom marker</p>
</body></html>"""


@pytest.fixture
def smoke_site():  # type: ignore[no-untyped-def]
    import http.server
    import socket
    import threading

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if self.path == "/file.txt":
                body, ctype = b"downloaded-content-42", "text/plain"
                self.send_response(200)
                self.send_header("Content-Disposition", 'attachment; filename="file.txt"')
            elif self.path == "/article":
                body = ("<html><title>Composting guide</title><body><article><p>" + "Composting turns kitchen scraps into "
                        "rich soil when greens and browns are balanced and the pile is turned weekly. " * 8
                        + "</p></article></body></html>").encode()
                ctype = "text/html"
                self.send_response(200)
            else:
                body = (_BROWSER_PAGE if self.path == "/" else "<html><title>Two</title><body>Page two</body></html>").encode()
                ctype = "text/html"
                self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a: object) -> None:
            return None

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    srv = http.server.HTTPServer(("127.0.0.1", port), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{port}"
    srv.shutdown()


async def test_browser_select_scroll_press_tabs_wait_navigate_screenshot_download(runtime_parts, ctx_factory,
                                                                                  smoke_site: str, sandbox: Path) -> None:
    from scar.tools.browser.manager import BrowserManager

    services, p = runtime_parts["services"], runtime_parts["pipeline"]
    services.settings.browser_headless = True
    services.settings.browser_channel = "chromium"
    services.settings.downloads_dir = str(sandbox / "downloads")
    services.browser = BrowserManager(services.settings)
    ctx = ctx_factory(f"open {smoke_site}, pick the Large size, download the file and look around")
    try:
        await _ok(p, "browser.open", {"url": smoke_site}, ctx)
        await _ok(p, "browser.select", {"label": "Size", "option": "Large"}, ctx)
        _tid, page = await services.browser.page()
        assert await page.eval_on_selector("#size", "e => e.value") == "Large"
        await _ok(p, "browser.scroll", {"direction": "down", "pages": 5}, ctx)
        assert await page.evaluate("window.scrollY") > 0
        await _ok(p, "browser.press", {"key": "Home"}, ctx)
        shot = await _ok(p, "browser.screenshot", {}, ctx)
        assert shot.data.get("artifact") or shot.data.get("path")
        await _ok(p, "browser.download", {"text": "Get file"}, ctx)
        files = list((sandbox / "downloads").rglob("file*.txt"))
        assert files and files[0].read_text() == "downloaded-content-42"
        await _ok(p, "browser.tabs", {"action": "new"}, ctx)
        tabs = await _ok(p, "browser.tabs", {"action": "list"}, ctx)
        assert len(tabs.data["tabs"]) == 2
        await _ok(p, "browser.open", {"url": smoke_site + "/two"}, ctx)
        await _ok(p, "browser.wait", {"text": "Page two", "timeout_s": 10}, ctx)
        await _ok(p, "browser.navigate", {"action": "back"}, ctx)
    finally:
        await services.browser.close()


def test_local_network_hosts_are_recognised() -> None:
    from scar.tools.web.tools import _is_local_host

    for h in ("localhost", "127.0.0.1", "10.1.2.3", "172.20.0.5", "192.168.1.1", "169.254.169.254", "::1", "[fd00::1]",
              "printer.local", "nas.lan"):
        assert _is_local_host(h), h
    for h in ("example.com", "8.8.8.8", "172.32.0.1", "2606:4700::1111"):
        assert not _is_local_host(h), h


async def test_web_search_and_research_fetch_and_cite_sources(runtime_parts, ctx_factory, smoke_site: str) -> None:
    """Search is a stub provider (no internet in tests); fetching, extraction and the fetch log are real."""
    from scar.providers.base import SearchHit

    services, p = runtime_parts["services"], runtime_parts["pipeline"]

    class StubSearch:
        async def search(self, query: str, max_results: int = 8):  # type: ignore[no-untyped-def]
            return "stub", [SearchHit(title="Router", url="http://192.168.1.1/admin", snippet="injected result"),
                            SearchHit(title="Compost", url=smoke_site + "/article", snippet="composting"),
                            SearchHit(title="Two", url=smoke_site + "/two", snippet="page two")]

    services.search = StubSearch()
    ctx = ctx_factory(f"research composting using the guide at {smoke_site} and summarise it")
    hits = await _ok(p, "web.search", {"query": "composting"}, ctx)
    assert len(hits.data["results"]) == 3
    res = await _ok(p, "web.research", {"query": "composting", "max_sources": 2}, ctx)
    assert "greens and browns" in (res.model_view or "")
    fetched = services.extras["fetch_log"][ctx.task_id]
    assert any(u.startswith(smoke_site + "/article") for u in fetched)
    assert not any("192.168.1.1" in u for u in fetched), "local-network search results are not fetched"


async def test_github_issues_and_ci_against_mocked_api(runtime_parts, ctx_factory, monkeypatch) -> None:
    """GitHub REST is mocked (no token on this machine); the request shapes, auth header and summaries are real."""
    import httpx
    import respx

    from scar.tools.dev.github import GitHubClient

    monkeypatch.setenv("GITHUB_TOKEN", "ghp_" + "t" * 36)
    from scar.security.secrets import SecretStore

    runtime_parts["services"].github = GitHubClient(SecretStore(dotenv_path=Path("no-such.env")))
    p = runtime_parts["pipeline"]
    ctx = ctx_factory("list the open issues and CI status for octo/demo")
    with respx.mock(assert_all_called=True) as mock:
        issues = mock.get("https://api.github.com/repos/octo/demo/issues").mock(return_value=httpx.Response(
            200, json=[{"number": 7, "title": "Crash on start", "state": "open", "html_url": "https://github.com/octo/demo/issues/7",
                        "user": {"login": "a"}, "labels": [], "comments": 0}]))
        mock.get("https://api.github.com/repos/octo/demo/commits/main/check-runs").mock(return_value=httpx.Response(
            200, json={"check_runs": [{"name": "tests", "status": "completed", "conclusion": "failure", "html_url": "https://x"}]}))
        res = await _ok(p, "github.issues", {"repo": "octo/demo", "action": "list"}, ctx)
        assert "Crash on start" in (res.model_view or str(res.data))
        assert issues.calls.last.request.headers["Authorization"].startswith("Bearer ghp_")
        ci = await _ok(p, "github.ci", {"repo": "octo/demo", "ref": "main"}, ctx)
        assert ci.summary == "1 check(s) failing"


async def test_github_publish_actions_need_approval(runtime_parts, ctx_factory, monkeypatch) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_" + "t" * 36)
    p = runtime_parts["pipeline"]
    ctx = ctx_factory("open an issue on octo/demo about the crash")
    obs = await p.execute("github.issues", {"repo": "octo/demo", "action": "create", "title": "Crash", "body": "x"}, ctx)
    assert obs.result.status == ToolStatus.DENIED  # HIGH: needs the user, and tests have no approval channel


@pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")
async def test_git_clone_local_repository(runtime_parts, ctx_factory, sandbox: Path) -> None:
    services, p = runtime_parts["services"], runtime_parts["pipeline"]
    src = sandbox / "origin"
    src.mkdir()
    for cmd in (["init", "-q", "-b", "main"], ["config", "user.email", "t@example.test"], ["config", "user.name", "T"]):
        subprocess.run(["git", *cmd], cwd=src, check=True)
    (src / "README.md").write_text("hello\n")
    subprocess.run(["git", "add", "."], cwd=src, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=src, check=True)
    _grant(services, "git.clone")
    ctx = ctx_factory(f"clone {src} into {sandbox / 'copy'}")
    await _ok(p, "git.clone", {"url": str(src), "dest": str(sandbox / "copy")}, ctx)
    assert (sandbox / "copy" / "README.md").read_text() == "hello\n"


async def test_devserver_status_and_stop(runtime_parts, ctx_factory, sandbox: Path) -> None:
    import socket
    import textwrap

    services, p = runtime_parts["services"], runtime_parts["pipeline"]
    services.settings.allowed_roots = [str(sandbox)]
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    (sandbox / "server.py").write_text(textwrap.dedent(f'''
        import http.server
        print("Server ready at http://127.0.0.1:{port}", flush=True)
        http.server.HTTPServer(("127.0.0.1", {port}), http.server.SimpleHTTPRequestHandler).serve_forever()
    '''))
    ctx = ctx_factory(f"start the dev server in {sandbox}, check it, then stop it")
    started = await _ok(p, "devserver.start", {"path": str(sandbox), "command": f'& "{sys.executable}" server.py', "wait_ready_s": 30}, ctx)
    sid = started.data["id"]
    st = await _ok(p, "devserver.status", {"id": sid}, ctx)
    assert "ready" in (st.model_view or "")
    await _ok(p, "devserver.stop", {"id": sid}, ctx)
    assert services.devservers.servers[sid].exit_code is not None


async def test_calendar_update_local_event(runtime_parts, ctx_factory) -> None:
    p = runtime_parts["pipeline"]
    ctx = ctx_factory("add dentist tomorrow at 3pm to my calendar, then move it to 4pm")
    from datetime import datetime, timedelta

    day = (datetime.now() + timedelta(days=1)).date().isoformat()
    made = await _ok(p, "calendar.create", {"title": "Dentist", "start": f"{day}T15:00:00"}, ctx)
    eid = made.data["event_id"]
    await _ok(p, "calendar.update", {"event_id": eid, "start": f"{day}T16:00:00", "end": f"{day}T17:00:00"}, ctx)
    listed = await _ok(p, "calendar.list", {"start": f"{day}T00:00:00", "end": f"{day}T23:59:00"}, ctx)
    ev = next(e for e in listed.data["events"] if e["event_id"] == eid)
    assert "16:00" in ev["start"]
