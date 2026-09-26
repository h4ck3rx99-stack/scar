"""Shared fixtures. Every test runs against a sandbox data dir and sandbox allowed root.

A guard fixture fails any test that writes outside its sandbox (tmp_path) or
SCAR's own sandboxed data dir.
"""

from __future__ import annotations

import os
import sys
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

# Tests never use the user's real config, secrets or data.
os.environ.setdefault("SCAR_TESTING", "1")


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    live = os.environ.get("SCAR_LIVE_TESTS") == "1"
    for item in items:
        if "windows" in item.keywords and sys.platform != "win32":
            item.add_marker(pytest.mark.skip(reason="requires a Windows host"))
        if "live" in item.keywords and not live:
            item.add_marker(pytest.mark.skip(reason="live desktop test; set SCAR_LIVE_TESTS=1 to run"))


@pytest.fixture
def sandbox(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """An isolated allowed root + data dir + config dir."""
    root = tmp_path / "sandbox"
    root.mkdir()
    data = tmp_path / "data"
    cfg = tmp_path / "config"
    cfg.mkdir()
    monkeypatch.setenv("SCAR_CONFIG_DIR", str(cfg))
    monkeypatch.setenv("SCAR_DATA_DIR", str(data))
    monkeypatch.setenv("SCAR_ALLOWED_ROOTS", str(root))
    for key in list(os.environ):
        if key.endswith(("_API_KEY", "_TOKEN")) or key in ("GITHUB_TOKEN", "HF_TOKEN"):
            monkeypatch.delenv(key, raising=False)
    monkeypatch.chdir(root)
    return root


@pytest.fixture
def settings(sandbox: Path):  # type: ignore[no-untyped-def]
    from scar.config.settings import load_settings

    return load_settings(local_inference_policy="never", model_idle_timeout=5.0, approval_timeout=5.0)


@pytest.fixture
def services(settings, sandbox: Path) -> Iterator[object]:  # type: ignore[no-untyped-def]
    from scar.runtime.builder import build_services
    from scar.security.secrets import SecretStore

    svc = build_services(settings, secrets=SecretStore(dotenv_path=sandbox / "no.env"))
    svc.extras["cwd"] = str(sandbox)
    yield svc
    svc.command_guard.ps_parser.close() if svc.command_guard.ps_parser else None
    svc.db.close()


@pytest.fixture
async def runtime_parts(services) -> AsyncIterator[dict[str, object]]:  # type: ignore[no-untyped-def]
    """Registry + pipeline wired with every tool module (no agent)."""
    from scar.memory.store import MemoryStore
    from scar.runtime.registration import build_registry
    from scar.tools.dev.devserver import DevServerManager
    from scar.tools.monitor.service import MonitorService
    from scar.tools.notify.service import Notifier
    from scar.tools.pipeline import ToolPipeline
    from scar.tools.scheduler.service import Scheduler
    from tests.helpers import FakeEmbeddings

    services.memory = MemoryStore(services.db, FakeEmbeddings())
    notifier = Notifier(services)
    notifier._toast = lambda title, message: False  # never pop real toasts during tests
    services.notifier = notifier
    services.scheduler = Scheduler(services)
    services.monitors = MonitorService(services)
    services.devservers = DevServerManager(services)
    registry = build_registry(services)
    pipeline = ToolPipeline(services, registry)
    yield {"services": services, "registry": registry, "pipeline": pipeline}
    await services.monitors.stop_all()
    await services.devservers.stop_all()
    await services.scheduler.stop()
    services.processes.kill_all()


@pytest.fixture
def ctx_factory(services):  # type: ignore[no-untyped-def]
    from scar.core.cancel import CancelToken
    from scar.core.types import TaskState
    from scar.security.scope import ScopeAnchor
    from scar.security.taint import TaintTracker
    from scar.tools.base import ToolContext

    def make(objective: str = "test objective", autonomy: int = 3, dry_run: bool = False) -> ToolContext:
        task = TaskState(objective=objective, autonomy_level=autonomy)
        taint = TaintTracker()
        taint.record_trusted(objective)
        return ToolContext(services=services, cancel=CancelToken(), task=task, taint=taint,
                           scope=ScopeAnchor(objective), dry_run=dry_run, session_id=services.session_id)

    return make


@pytest.fixture(autouse=True)
def _no_writes_outside_sandbox(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[None]:
    """Fail the test if files appear in the repo tree or the user's home top-level during the test."""
    watched = [ROOT, Path.home() / "Desktop", Path.home() / "Documents"]
    before = {p: _snapshot(p) for p in watched}
    yield
    for p in watched:
        after = _snapshot(p)
        new = after - before[p]
        new = {n for n in new if not n.startswith((".pytest_cache", ".ruff_cache", ".hypothesis", "__pycache__", ".venv", ".coverage"))}
        assert not new, f"test wrote outside its sandbox: {sorted(new)[:5]} in {p}"


def _snapshot(p: Path) -> set[str]:
    try:
        return {c.name for c in p.iterdir()}
    except OSError:
        return set()
