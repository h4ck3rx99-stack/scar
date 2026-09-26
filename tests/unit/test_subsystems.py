"""Memory, scheduler, monitors, time parsing, fast path, config, core primitives, parsers (D2)."""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from scar.core.artifacts import ArtifactStore
from scar.core.budgets import Budget, BudgetTracker
from scar.core.cancel import CancelToken, run_cancellable
from scar.core.errors import BudgetExceeded, Cancelled
from scar.memory.extraction import extract
from scar.memory.policy import check_write
from scar.memory.store import MemoryStore
from scar.tools.dev.project import detect_project, parse_diagnostics, parse_test_output
from scar.tools.scheduler.timeparse import parse_when
from tests.helpers import FakeEmbeddings


# ---------------------------------------------------------------- core
async def test_cancel_token_hierarchy_and_run_cancellable() -> None:
    root = CancelToken()
    child = root.child()
    fired = []
    child.on_cancel(fired.append)

    async def slow() -> int:
        await asyncio.sleep(10)
        return 1

    task = asyncio.create_task(run_cancellable(slow(), child))
    await asyncio.sleep(0.05)
    root.cancel("stop")
    with pytest.raises(Cancelled):
        await task
    assert fired == ["stop"] and child.cancelled
    late = root.child()
    assert late.cancelled  # children created after cancellation are cancelled


def test_budget_tracker() -> None:
    b = BudgetTracker(Budget(max_steps=2, max_tokens=100, no_progress_window=3))
    b.step()
    b.step()
    with pytest.raises(BudgetExceeded):
        b.step()
    with pytest.raises(BudgetExceeded):
        b.add_tokens(101)
    for _ in range(3):
        b.record_state("same")
    assert b.no_progress()
    assert b.record_failure("h") == 1 and not b.repeated_failure("h")


def test_artifacts_ranges_and_retention(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path / "a", max_total_bytes=10_000)
    info = store.put_text("t1", "\n".join(f"line {i}" for i in range(1000)))
    text, total = store.read_lines(info.ref, 10, 3)
    assert total == 1000 and text.splitlines() == ["line 9", "line 10", "line 11"]
    with pytest.raises(Exception):
        store.resolve("artifact://t1/../../etc")
    for _ in range(5):
        store.put_bytes("t2", b"x" * 4000)
    assert store.enforce_retention() >= 1


# ---------------------------------------------------------------- config
def test_config_precedence(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from scar.config.settings import ConfigError, load_settings

    monkeypatch.setenv("SCAR_CONFIG_DIR", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    for k in ("SCAR_AUTONOMY_LEVEL", "SCAR_LOG_LEVEL"):
        monkeypatch.delenv(k, raising=False)
    assert load_settings().autonomy_level == 3  # default
    (tmp_path / "config.toml").write_text("autonomy_level = 1\nlog_level = 'debug'\n", encoding="utf-8")
    s = load_settings()
    assert s.autonomy_level == 1 and s.log_level == "DEBUG"  # toml over default
    (tmp_path / ".env").write_text("SCAR_AUTONOMY_LEVEL=2\n", encoding="utf-8")
    assert load_settings().autonomy_level == 2  # .env over toml
    monkeypatch.setenv("SCAR_AUTONOMY_LEVEL", "4")
    assert load_settings().autonomy_level == 4  # env over .env
    assert load_settings(autonomy_level=0).autonomy_level == 0  # CLI flag over env
    monkeypatch.setenv("SCAR_AUTONOMY_LEVEL", "9")
    with pytest.raises(ConfigError) as ei:
        load_settings()
    assert "SCAR_AUTONOMY_LEVEL" in str(ei.value)
    monkeypatch.setenv("SCAR_AUTONOMY_LEVEL", "3")
    monkeypatch.setenv("SCAR_ALLOWED_ROOTS", "C:\\a;D:\\b")
    assert load_settings().allowed_roots == ["C:\\a", "D:\\b"]
    monkeypatch.setenv("SCAR_PRIVACY_OVERRIDES", "screen=local_only,email=bogus")
    with pytest.raises(ConfigError):
        load_settings()


# ---------------------------------------------------------------- memory
@pytest.fixture
def mem(services) -> MemoryStore:  # type: ignore[no-untyped-def]
    return MemoryStore(services.db, FakeEmbeddings())


def test_memory_store_retrieve_dedupe_forget(mem: MemoryStore) -> None:
    mid, st = mem.store("The BISense project lives at C:\\Projects\\BISense", "semantic")
    assert st == "created"
    mid2, st2 = mem.store("The BISense project lives at C:\\Projects\\BISense.", "semantic")
    assert st2 == "updated" and mid2 == mid
    mem.store("User prefers pnpm for JavaScript projects", "preference")
    mem.store("Coffee order: oat flat white", "preference")
    hits = mem.search("where is bisense")
    assert hits and "BISense" in hits[0].text
    assert mem.forget(query="bisense project location")
    assert not [m for m in mem.search("bisense") if "BISense" in m.text]


def test_memory_write_policy_blocks_secrets(mem: MemoryStore) -> None:
    for bad in ("my password is hunter2", "card 4111 1111 1111 1111", "key gsk_abcdefghijklmnopqrstuvwxyz123456",
                "ssn 123-45-6789"):
        assert not check_write(bad, "semantic").allowed, bad
        with pytest.raises(ValueError):
            mem.store(bad)
    assert check_write("order number 12345678", "semantic").allowed


def test_memory_aliases_ttl_and_preferences(mem: MemoryStore, services) -> None:  # type: ignore[no-untyped-def]
    mem.set_alias("my acceptance project", "C:\\sandbox\\accept")
    assert mem.resolve_alias("my acceptance project folder") == "C:\\sandbox\\accept"
    mem.set_project_preference("C:\\p\\web", "package_manager", "pnpm")
    assert mem.project_preference("c:\\p\\web", "package_manager") == "pnpm"
    mem.store("met Sam at lunch", "episodic", ttl_days=0.0000001)
    time.sleep(0.05)
    assert mem.expire() == 1


def test_extraction_rules() -> None:
    e = extract("remember that my BISense folder is at C:\\Projects\\BISense")
    assert e.alias == "BISense" and e.target == "C:\\Projects\\BISense"
    e = extract("use pnpm in this project")
    assert e.preference == ("package_manager", "pnpm")
    assert extract("remember I like tea").category == "semantic"


# ---------------------------------------------------------------- time parsing / scheduler
def test_parse_when() -> None:
    now = datetime(2026, 9, 27, 10, 0).astimezone()
    assert parse_when("in 10 minutes", now=now).when == now + timedelta(minutes=10)
    p = parse_when("at 5pm", now=now)
    assert (p.when.hour, p.when.minute) == (17, 0) and p.when.date() == now.date()
    p = parse_when("at 9am", now=now)
    assert p.when.date() == (now + timedelta(days=1)).date()
    p = parse_when("every 2 hours", now=now)
    assert p.interval_s == 7200
    p = parse_when("every monday at 10am", now=now)
    assert p.when.weekday() == 0 and p.interval_s == 7 * 86400 and p.when > now
    p = parse_when("daily at 8:30", now=now)
    assert p.interval_s == 86400 and (p.when.hour, p.when.minute) == (8, 30)
    assert parse_when("tomorrow at 9am", now=now).when.date() == (now + timedelta(days=1)).date()
    with pytest.raises(ValueError):
        parse_when("whenever the moon is blue", now=now)


async def test_scheduler_fires_and_reports_missed(runtime_parts) -> None:
    from scar.tools.scheduler.service import Scheduler

    services = runtime_parts["services"]
    sched = services.scheduler
    fired = []
    services.bus.add_listener(lambda e: fired.append(e.message) if e.kind == "reminder_due" else None)
    sched.add("reminder", "drink water", datetime.now().astimezone() + timedelta(seconds=0.5))
    sched.start()
    for _ in range(40):
        await asyncio.sleep(0.1)
        if fired:
            break
    assert fired == ["drink water"]
    assert sched.list() == []
    await sched.stop()
    # simulate a firing that was due while SCAR was off, then a restart
    sched.add("reminder", "missed one", datetime.now().astimezone() - timedelta(minutes=10))
    sched.add("reminder", "recurring", datetime.now().astimezone() - timedelta(minutes=10), interval_s=3600)
    restarted = Scheduler(services)
    restarted.start()
    await asyncio.sleep(0.3)
    assert {r["text"] for r in restarted.missed_on_start} == {"missed one", "recurring"}
    rec = next(r for r in restarted.list() if r["text"] == "recurring")
    assert datetime.fromisoformat(rec["next_run"]) > datetime.now().astimezone()
    assert any(d.title == "Missed while SCAR was off" for d in services.notifier.delivered)
    await restarted.stop()


# ---------------------------------------------------------------- monitors
async def test_process_crash_monitor_event_based(runtime_parts) -> None:
    services = runtime_parts["services"]
    proc = subprocess.Popen([sys.executable, "-c", "import time,sys; time.sleep(1.5); sys.exit(3)"])
    m = services.monitors.watch_process(proc.pid)
    for _ in range(80):
        await asyncio.sleep(0.1)
        if m.events:
            break
    assert m.events and m.events[0]["exit_code"] == 3 and m.events[0]["crashed"]
    assert "WaitForMultipleObjects" in m.events[0]["wait_method"]
    assert any(d.title == "Process crashed" for d in services.notifier.delivered)


async def test_folder_and_download_monitors(runtime_parts, tmp_path: Path) -> None:
    services = runtime_parts["services"]
    watch = tmp_path / "w"
    watch.mkdir()
    m = services.monitors.watch_folder(str(watch), debounce_s=0.3)
    await asyncio.sleep(0.5)
    (watch / "new.txt").write_text("x")
    for _ in range(50):
        await asyncio.sleep(0.1)
        if m.events:
            break
    assert m.events and "new.txt" in m.events[0]["message"]
    dl = tmp_path / "dl"
    dl.mkdir()
    d = services.monitors.watch_download(str(dl))
    await asyncio.sleep(0.5)
    (dl / "big.zip.crdownload").write_bytes(b"x" * 10)
    await asyncio.sleep(0.5)
    os.replace(dl / "big.zip.crdownload", dl / "big.zip")
    for _ in range(80):
        await asyncio.sleep(0.1)
        if d.status == "fired":
            break
    assert d.status == "fired" and d.events[0]["files"][0].endswith("big.zip")


async def test_monitor_limits_and_cancel(runtime_parts, tmp_path: Path) -> None:
    services = runtime_parts["services"]
    services.monitors.max_monitors = 1
    m = services.monitors.watch_folder(str(tmp_path))
    with pytest.raises(ValueError):
        services.monitors.watch_folder(str(tmp_path))
    assert services.monitors.cancel(m.id)
    await asyncio.sleep(0.2)
    assert services.monitors.list()[0]["status"] == "cancelled"


# ---------------------------------------------------------------- fast path
def test_fast_path_grammar(services, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    from scar.agent.fastpath.grammar import FastPath

    services.memory = MemoryStore(services.db, FakeEmbeddings())
    proj = tmp_path / "BISense"
    proj.mkdir()
    services.memory.set_alias("BISense", str(proj))
    fp = FastPath(services)
    plan = fp.match("Open VS Code in my BISense folder")
    assert plan.calls[0].tool == "apps.launch" and plan.calls[0].args == {"app": "vs code", "folder": str(proj)}
    plan = fp.match(f"open vscode in {proj}")
    assert plan.calls[0].args["folder"] == str(proj)
    assert fp.match("what's using my RAM").calls[0].tool == "system.info"
    assert fp.match("remind me to call mom at 6pm").calls[0].args == {"when": "at 6pm", "text": "call mom"}
    assert fp.match("take a screenshot").calls[0].tool == "screen.capture"
    assert fp.match("open chrome and go to example.com").calls[0].args == {"url": "example.com"}
    assert fp.match("set volume to 40%").calls[0].args["level"] == 40
    assert fp.match("minimize notepad").calls[0].args == {"process": "notepad.exe", "action": "minimize"}
    assert fp.match("cancel").control == "cancel"
    assert fp.match("remember that I prefer dark mode").calls[0].tool == "memory.remember"
    assert fp.match("write a poem about the sea and email it to Bob") is None


# ---------------------------------------------------------------- parsers
def test_test_output_parsers() -> None:
    py = "..F.\nFAILED tests/test_a.py::test_x - AssertionError: 1 != 2\n==== 1 failed, 3 passed in 0.12s ===="
    r = parse_test_output(py)
    assert (r.framework, r.failed, r.passed) == ("pytest", 1, 3) and r.failures[0]["name"] == "tests/test_a.py::test_x"
    jest = "Tests:       2 failed, 5 passed, 7 total\n  ● suite › adds"
    r = parse_test_output(jest)
    assert (r.framework, r.failed, r.passed, r.total) == ("jest", 2, 5, 7)
    cargo = "test a::b ... FAILED\ntest result: FAILED. 3 passed; 1 failed; 0 ignored"
    r = parse_test_output(cargo)
    assert (r.failed, r.passed) == (1, 3) and r.failures[0]["name"] == "a::b"
    ut = "FAIL: test_add (test_m.T)\nRan 4 tests in 0.001s\nFAILED (failures=1)"
    r = parse_test_output(ut)
    assert (r.framework, r.failed, r.passed) == ("unittest", 1, 3)
    diags = parse_diagnostics("src/app.ts(12,5): error TS2322: Type 'x'\nmain.py:3:1: F401 unused import")
    assert diags[0]["line"] == 12 and diags[1]["file"] == "main.py"


def test_detect_project(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text('{"scripts": {"test": "jest", "dev": "vite"}}')
    (tmp_path / "pnpm-lock.yaml").write_text("")
    info = detect_project(tmp_path)
    assert info.package_manager == "pnpm" and info.dev_command[-1] == "dev" and info.test_command[1] == "test"
