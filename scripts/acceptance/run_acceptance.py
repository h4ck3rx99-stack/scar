"""SCAR acceptance harness (Part D3). Runs the 12 acceptance scenarios for real on this Windows machine.

    SCAR_LIVE_TESTS=1 uv run python scripts/acceptance/run_acceptance.py [--only 1,3,12]

Every fixture lives in a fresh sandbox folder under ~/scar-sandbox; each test cleans up only what it created
(windows it opened, processes it started, servers it ran). Results go to docs/acceptance_results.json.
Statuses: VERIFIED (exercised end-to-end, evidence recorded), IMPLEMENTED-UNVERIFIED (prerequisite missing),
FAILED.
"""

from __future__ import annotations

import argparse
import asyncio
import http.server
import json
import os
import re
import socket
import subprocess
import sys
import textwrap
import threading
import time
import traceback
from collections.abc import Callable
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from scar.config.settings import load_settings  # noqa: E402
from scar.core.types import TaskState  # noqa: E402
from scar.runtime.runtime import Runtime  # noqa: E402
from scar.tools.windows import win32  # noqa: E402

Result = dict[str, Any]


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


class FixtureServer:
    def __init__(self, pages: dict[str, str]) -> None:
        pages_ = pages

        class H(http.server.BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                body = pages_.get(self.path, pages_.get("/", ""))
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(body.encode())

            def log_message(self, *a: object) -> None:
                return None

        self.port = free_port()
        self.srv = http.server.HTTPServer(("127.0.0.1", self.port), H)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.port}/"

    def close(self) -> None:
        self.srv.shutdown()


def close_windows(pred: Callable[[win32.WindowInfo], bool]) -> int:
    n = 0
    for w in win32.list_windows():
        if pred(w):
            win32.close_window(w.hwnd)
            n += 1
    return n


class Harness:
    def __init__(self, sandbox: Path) -> None:
        self.sandbox = sandbox
        self.rt: Runtime | None = None
        self.results: list[Result] = []

    async def start(self) -> None:
        settings = load_settings(allowed_roots=[str(Path.home())])
        self.rt = Runtime(settings)
        await self.rt.start(with_hotkeys=False)
        self.rt.services.extras["cwd"] = str(self.sandbox)

    async def stop(self) -> None:
        if self.rt is not None:
            await self.rt.stop()

    @property
    def s(self) -> Any:
        assert self.rt is not None
        return self.rt.services

    async def run(self, objective: str, timeout: float = 900) -> TaskState:
        assert self.rt is not None and self.rt.tasks is not None
        return await asyncio.wait_for(self.rt.tasks.run(objective), timeout)

    def tools_used(self, task: TaskState) -> list[str]:
        return [f"{o.tool}:{o.result.status.value}" for o in task.observations]

    # ------------------------------------------------------------------ 1
    async def t01_vscode(self) -> Result:
        folder = self.sandbox / "acc1-vscode-direct"
        folder.mkdir()
        (folder / "README.md").write_text("acceptance 1\n")
        alias_folder = self.sandbox / "acc1-vscode-alias"
        alias_folder.mkdir()
        evidence: dict[str, Any] = {}
        try:
            task = await self.run(f"Open VS Code in {folder}")
            w = win32.find_windows(title=folder.name, process="Code.exe")
            evidence["direct"] = {"reply": task.result_summary, "status": task.status.value,
                                  "verified": task.verification.verified if task.verification else None,
                                  "window_titles": [x.title for x in w]}
            self.s.memory.set_alias("my acceptance project", str(alias_folder))
            task2 = await self.run("Open VS Code in my acceptance project")
            w2 = win32.find_windows(title=alias_folder.name, process="Code.exe")
            evidence["alias"] = {"reply": task2.result_summary, "status": task2.status.value,
                                 "verified": task2.verification.verified if task2.verification else None,
                                 "window_titles": [x.title for x in w2]}
            ok = (task.status.value == "succeeded" and bool(w) and task2.status.value == "succeeded" and bool(w2)
                  and evidence["direct"]["verified"] is True and evidence["alias"]["verified"] is True)
            return {"status": "VERIFIED" if ok else "FAILED", "evidence": evidence}
        finally:
            await asyncio.sleep(1)
            close_windows(lambda w: w.process.lower() == "code.exe" and (folder.name in w.title or alias_folder.name in w.title))

    # ------------------------------------------------------------------ 2
    async def t02_browser(self) -> Result:
        token = f"SCAR-TOKEN-{int(time.time())}"
        srv = FixtureServer({"/": f"<html><title>Acceptance 2</title><body><h1>{token}</h1></body></html>"})
        try:
            task = await self.run(f"Open Chrome and navigate to {srv.url}")
            bm = self.s.browser
            _tid, page = await bm.page()
            content = await page.content()
            ok = task.status.value == "succeeded" and page.url.startswith(srv.url.rstrip("/")) and token in content
            return {"status": "VERIFIED" if ok else "FAILED",
                    "evidence": {"reply": task.result_summary, "url": page.url, "token_found": token in content,
                                 "browser_channel": bm.state.channel, "tools": self.tools_used(task)}}
        finally:
            await self.s.browser.close()
            srv.close()

    # ------------------------------------------------------------------ 3
    async def t03_tests(self) -> Result:
        proj = self.sandbox / "acc3-tests"
        (proj / "tests").mkdir(parents=True)
        (proj / "tests" / "test_sample.py").write_text(textwrap.dedent("""
            def test_pass_one(): assert 1 + 1 == 2
            def test_pass_two(): assert "a".upper() == "A"
            def test_pass_three(): assert [1, 2][0] == 1
            def test_fail_one(): assert 2 * 2 == 5
            def test_fail_two(): assert "x" in "abc"
        """))
        task = await self.run(f"Run the tests in {proj}")
        obs = next((o for o in task.observations if o.tool == "dev.run_tests"), None)
        report = (obs.result.data.get("report") if obs else None) or {}
        cmd = obs.result.data.get("command") if obs else None
        actual = subprocess.run([*(cmd or [sys.executable, "-m", "pytest", "-q", "-rfE", "--color=no"])], cwd=proj,
                                capture_output=True, text=True, timeout=300)
        m = re.search(r"(\d+) failed, (\d+) passed", actual.stdout)
        actual_failed, actual_passed = (int(m.group(1)), int(m.group(2))) if m else (-1, -1)
        names = sorted(f["name"].split("::")[-1] for f in report.get("failures", []))
        ok = (report.get("failed") == actual_failed == 2 and report.get("passed") == actual_passed == 3
              and names == ["test_fail_one", "test_fail_two"] and "test_fail_one" in task.result_summary)
        return {"status": "VERIFIED" if ok else "FAILED",
                "evidence": {"reply": task.result_summary, "reported": {"failed": report.get("failed"), "passed": report.get("passed"),
                             "failing": names}, "actual": {"failed": actual_failed, "passed": actual_passed}}}

    # ------------------------------------------------------------------ 4
    async def t04_screen(self) -> Result:
        phrase = "SCAR ACCEPTANCE PHRASE 4417"
        script = self.sandbox / "acc4_window.py"
        script.write_text(textwrap.dedent(f"""
            import tkinter as tk
            r = tk.Tk(); r.title("SCAR Acceptance Window"); r.geometry("900x300+200+200")
            tk.Label(r, text="{phrase}", font=("Segoe UI", 36)).pack(expand=True)
            r.attributes("-topmost", True); r.after(120000, r.destroy); r.mainloop()
        """))
        base_py = getattr(sys, "_base_executable", None) or sys.executable
        proc = subprocess.Popen([base_py, str(script)])
        try:
            w = await asyncio.to_thread(win32.wait_for_window, lambda x: x.title == "SCAR Acceptance Window", 20)
            if w is None:
                return {"status": "FAILED", "evidence": {"error": "fixture window did not appear"}}
            win32.focus_window(w.hwnd)
            await asyncio.sleep(0.8)
            task = await self.run("Look at my screen")
            obs = next((o for o in task.observations if o.tool == "screen.describe"), None)
            data = obs.result.data if obs else {}
            ocr_ok = phrase in (data.get("ocr_text") or "").upper().replace("\n", " ")
            app_ok = (data.get("window_title") == "SCAR Acceptance Window")
            evidence: dict[str, Any] = {"reply": task.result_summary[:300], "ocr_contains_phrase": ocr_ok,
                                        "app": data.get("app"), "window_title": data.get("window_title")}
            # vision path, if a vision provider is available
            ctx = self.rt.tasks._ctx(TaskState(objective="describe the screen with vision"), self.rt.tasks.root_cancel.child(), False)  # type: ignore[union-attr]
            vobs = await self.rt.pipeline.execute("screen.describe", {"question": "What large text is shown in this window?",  # type: ignore[union-attr]
                                                                     "use_vision": "always"}, ctx)
            evidence["vision"] = vobs.result.data.get("vision") or vobs.result.data.get("vision_error")
            evidence["vision_model"] = self.s.router.last_route.get("vision")
            vision_ok = bool(vobs.result.data.get("vision"))
            ok = ocr_ok and app_ok
            evidence["vision_status"] = "VERIFIED" if vision_ok else "IMPLEMENTED-UNVERIFIED (no vision provider reachable)"
            return {"status": "VERIFIED" if ok else "FAILED", "evidence": evidence}
        finally:
            proc.kill()

    # ------------------------------------------------------------------ 5
    async def t05_create_file(self) -> Result:
        import hashlib

        target = self.sandbox / "acc5.txt"
        text = "SCAR acceptance five: 42 bananas"
        task = await self.run(f'Create a file named acc5.txt in {self.sandbox} containing exactly this text: {text}')
        data = target.read_bytes() if target.exists() else b""
        want = hashlib.sha256(text.encode()).hexdigest()
        got = hashlib.sha256(data).hexdigest()
        ok = task.status.value == "succeeded" and got == want
        return {"status": "VERIFIED" if ok else "FAILED",
                "evidence": {"reply": task.result_summary, "expected_sha256": want, "actual_sha256": got,
                             "bytes": data.decode("utf-8", errors="replace"), "tools": self.tools_used(task)}}

    # ------------------------------------------------------------------ 6
    async def t06_find_file(self) -> Result:
        tree = self.sandbox / "acc6-tree"
        for i in range(6):
            d = tree / f"dir{i}" / f"sub{i % 3}"
            d.mkdir(parents=True, exist_ok=True)
            (d / f"report_{i}.txt").write_text(f"decoy report {i} quartz lantern {i}")
        target = tree / "dir4" / "sub1" / "notes_final.txt"
        target.write_text("the unique phrase is quartz-lantern-5521")
        task = await self.run(f"Find the file in {tree} that contains the phrase 'quartz-lantern-5521'")
        ok = task.status.value == "succeeded" and str(target).lower() in task.result_summary.lower()
        return {"status": "VERIFIED" if ok else "FAILED",
                "evidence": {"reply": task.result_summary, "expected": str(target), "tools": self.tools_used(task)}}

    # ------------------------------------------------------------------ 7 / 8
    async def t07_message(self) -> Result:
        return await self._comms_unverified("message", ["tests/integration/test_comms_messaging.py",
                                                        "tests/integration/test_comms_contacts.py",
                                                        "tests/integration/test_comms_pipeline.py"],
                                            "TELEGRAM_BOT_TOKEN + SCAR_TEST_TELEGRAM_CHAT (or DISCORD_BOT_TOKEN + SCAR_TEST_DISCORD_CHANNEL)")

    async def t08_email(self) -> Result:
        return await self._comms_unverified("email", ["tests/integration/test_comms_gmail.py", "tests/integration/test_comms_graph.py",
                                                      "tests/integration/test_comms_pipeline.py"],
                                            "Gmail/Outlook OAuth (`scar auth google|microsoft`) or IMAP/SMTP app password + SCAR_TEST_EMAIL_TO")

    async def _comms_unverified(self, kind: str, tests: list[str], prereq: str) -> Result:
        creds = {n: self.s.secrets.has(n) for n in ("TELEGRAM_BOT_TOKEN", "DISCORD_BOT_TOKEN", "SCAR_SMTP_PASSWORD")}
        r = subprocess.run([sys.executable, "-m", "pytest", "-q", *tests], cwd=ROOT, capture_output=True, text=True, timeout=600)
        summary = (r.stdout.strip().splitlines() or [""])[-1]
        live_possible = any(creds.values()) and bool(self.s.settings.test_telegram_chat or self.s.settings.test_email_to)
        return {"status": "IMPLEMENTED-UNVERIFIED" if not live_possible else "FAILED",
                "evidence": {"integration_tests": summary, "exit_code": r.returncode, "credentials_present": creds},
                "prerequisite": prereq}

    # ------------------------------------------------------------------ 9
    async def t09_fix_bug(self) -> Result:
        repo = self.sandbox / "acc9-repo"
        (repo / "src").mkdir(parents=True)
        (repo / "tests").mkdir()
        (repo / "src" / "__init__.py").write_text("")
        (repo / "src" / "calc.py").write_text(textwrap.dedent('''
            def add(a, b):
                """Return the sum of a and b."""
                return a - b


            def multiply(a, b):
                return a * b
        ''').lstrip())
        (repo / "tests" / "test_calc.py").write_text(textwrap.dedent('''
            import sys, pathlib
            sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
            from src.calc import add, multiply


            def test_add():
                assert add(2, 3) == 5


            def test_multiply():
                assert multiply(3, 4) == 12
        ''').lstrip())
        for cmd in (["git", "init", "-q"], ["git", "add", "-A"], ["git", "-c", "user.email=a@b", "-c", "user.name=acc",
                                                                   "commit", "-qm", "init"]):
            subprocess.run(cmd, cwd=repo, check=True, capture_output=True)
        task = await self.run(f"Open the repository at {repo}, find the failing test, fix it, and run the tests again.", timeout=1500)
        after = subprocess.run([sys.executable, "-m", "pytest", "-q"], cwd=repo, capture_output=True, text=True)
        changed = subprocess.run(["git", "diff", "--name-only"], cwd=repo, capture_output=True, text=True).stdout.split()
        stat = subprocess.run(["git", "diff", "--numstat"], cwd=repo, capture_output=True, text=True).stdout.split()
        lines_changed = sum(int(x) for x in stat[0:2]) if len(stat) >= 2 and stat[0].isdigit() else -1
        ok = (after.returncode == 0 and changed == ["src/calc.py"] and 0 < lines_changed <= 4)
        return {"status": "VERIFIED" if ok else "FAILED",
                "evidence": {"reply": task.result_summary, "pytest_after": after.stdout.strip().splitlines()[-1:],
                             "files_changed": changed, "lines_changed": lines_changed, "tools": self.tools_used(task),
                             "model": self.s.router.last_route.get("reasoning")}}

    # ------------------------------------------------------------------ 10
    async def t10_research(self) -> Result:
        from scar.tools.web.citations import URL_RE, check_citations, fetched_urls

        doc = self.sandbox / "acc10-research.md"
        task = await self.run(f"Research what the Python walrus operator (:=) is and when it was introduced, summarize the useful "
                              f"information, and save it to a document at {doc}", timeout=1800)
        text = doc.read_text(encoding="utf-8") if doc.exists() else ""
        allowed = fetched_urls(self.s.db, task.task_id, self.s.extras.get("fetch_log", {}).get(task.task_id, []))
        cited = URL_RE.findall(text)
        _good, bad = check_citations(text, allowed)
        on_topic = ":=" in text or "walrus" in text.lower()
        ok = doc.exists() and len(text) > 200 and on_topic and bool(cited) and not bad
        return {"status": "VERIFIED" if ok else "FAILED",
                "evidence": {"reply": task.result_summary, "doc_chars": len(text), "cited": cited, "unverified_citations": bad,
                             "fetched": sorted(allowed)[:10], "tools": self.tools_used(task), "search": self.s.search.last_provider}}

    # ------------------------------------------------------------------ 11
    async def t11_vscode_devserver(self) -> Result:
        proj = self.sandbox / "acc11-webapp"
        proj.mkdir()
        port = free_port()
        (proj / "server.py").write_text(textwrap.dedent(f"""
            import http.server, time
            print("compiling...", flush=True)
            time.sleep(3)
            print("Server ready at http://127.0.0.1:{port}", flush=True)
            http.server.HTTPServer(("127.0.0.1", {port}), http.server.SimpleHTTPRequestHandler).serve_forever()
        """))
        base_py = (getattr(sys, "_base_executable", None) or sys.executable).replace("\\", "/")
        (proj / "package.json").write_text(json.dumps({"name": "acc11", "scripts": {"dev": f'"{base_py}" server.py'}}))
        delivered_before = len(self.s.notifier.delivered)
        try:
            task = await self.run(f"Open VS Code in {proj}, start the development server, and tell me when it's ready", timeout=1200)
            w = win32.find_windows(title=proj.name, process="Code.exe")
            servers = [d for d in self.s.devservers.servers.values() if Path(d.cwd) == proj]
            ds = servers[-1] if servers else None
            notes = [d for d in list(self.s.notifier.delivered)[delivered_before:] if "ready" in d.message.lower() or "ready" in d.title.lower()]
            managed = bool(ds) and self.s.processes.get(ds.managed.pid) is not None
            evidence = {"reply": task.result_summary, "vscode_window": [x.title for x in w],
                        "devserver": ds.info() if ds else None, "notified": [f"{d.title}: {d.message} via {d.channels}" for d in notes],
                        "managed": managed, "tools": self.tools_used(task)}
            cancellable = False
            if ds is not None:
                await self.s.devservers.stop(ds.id)
                cancellable = ds.exit_code is not None
            evidence["cancelled_ok"] = cancellable
            ok = (bool(w) and ds is not None and ds.ready_line is not None and ds.http_status is not None and bool(notes)
                  and managed and cancellable)
            return {"status": "VERIFIED" if ok else "FAILED", "evidence": evidence}
        finally:
            await asyncio.sleep(1)
            close_windows(lambda w: w.process.lower() == "code.exe" and proj.name in w.title)
            await self.s.devservers.stop_all()

    # ------------------------------------------------------------------ 12
    async def t12_watch_crash(self) -> Result:
        base_py = getattr(sys, "_base_executable", None) or sys.executable
        proc = subprocess.Popen([base_py, "-c", "import time, sys; time.sleep(5); sys.exit(7)"])
        delivered_before = len(self.s.notifier.delivered)
        task = await self.run(f"Watch process {proc.pid} and tell me if it crashes")
        mid = next((o.result.data.get("id") for o in task.observations if o.tool == "monitor.start"), None)
        m = self.s.monitors.monitors.get(mid) if mid else None
        for _ in range(100):
            if m is not None and m.events:
                break
            await asyncio.sleep(0.2)
        ev = m.events[0] if m and m.events else {}
        notes = [d for d in list(self.s.notifier.delivered)[delivered_before:] if "crash" in d.title.lower()]
        ok = ev.get("exit_code") == 7 and ev.get("crashed") is True and bool(notes) and "WaitForMultipleObjects" in ev.get("wait_method", "")
        return {"status": "VERIFIED" if ok else "FAILED",
                "evidence": {"reply": task.result_summary, "event": ev, "notified": [f"{d.title}: {d.message} via {d.channels}" for d in notes]}}


TESTS: list[tuple[int, str, str]] = [
    (1, "Open VS Code in this folder (direct + alias)", "t01_vscode"),
    (2, "Open Chrome and navigate to a URL", "t02_browser"),
    (3, "Run the tests in this project", "t03_tests"),
    (4, "Look at my screen", "t04_screen"),
    (5, "Create a file containing this text", "t05_create_file"),
    (6, "Find this file", "t06_find_file"),
    (7, "Send this message to this person", "t07_message"),
    (8, "Email this document", "t08_email"),
    (9, "Find and fix the failing test", "t09_fix_bug"),
    (10, "Research, summarize, save to a document", "t10_research"),
    (11, "VS Code + dev server + tell me when ready", "t11_vscode_devserver"),
    (12, "Watch a process and report a crash", "t12_watch_crash"),
]


async def main(only: set[int] | None) -> int:
    if os.environ.get("SCAR_LIVE_TESTS") != "1":
        print("Refusing to run: set SCAR_LIVE_TESTS=1 (this opens apps and windows on the desktop).")
        return 2
    stamp = time.strftime("%Y%m%d-%H%M%S")
    sandbox = Path.home() / "scar-sandbox" / f"acceptance-{stamp}"
    sandbox.mkdir(parents=True)
    h = Harness(sandbox)
    await h.start()
    out_path = ROOT / "docs" / "acceptance_results.json"
    previous: dict[str, Any] = json.loads(out_path.read_text(encoding="utf-8")) if out_path.exists() else {}
    results: dict[str, Any] = dict(previous.get("results", {}))
    try:
        for num, name, fn in TESTS:
            if only and num not in only:
                continue
            print(f"[{num:2}] {name} ...", flush=True)
            t0 = time.monotonic()
            try:
                res = await getattr(h, fn)()
            except Exception as exc:
                res = {"status": "FAILED", "evidence": {"exception": f"{type(exc).__name__}: {exc}",
                                                        "traceback": traceback.format_exc()[-2000:]}}
            res["seconds"] = round(time.monotonic() - t0, 1)
            res["name"] = name
            res["at"] = time.strftime("%Y-%m-%d %H:%M:%S")
            results[str(num)] = res
            print(f"     -> {res['status']} ({res['seconds']}s)", flush=True)
    finally:
        await h.stop()
    payload = {"machine": os.environ.get("COMPUTERNAME", ""), "sandbox": str(sandbox), "results": results}
    out_path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    print(f"Results written to {out_path}")
    return 0 if all(r["status"] != "FAILED" for r in results.values()) else 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="", help="comma-separated test numbers")
    args = ap.parse_args()
    sel = {int(x) for x in args.only.split(",") if x.strip()} or None
    raise SystemExit(asyncio.run(main(sel)))

