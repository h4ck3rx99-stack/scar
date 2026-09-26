"""Secrets never leak (logs, traces, model payloads, CLI/tool output); test doubles are isolated from runtime (D5)."""

from __future__ import annotations

import ast
import json
import logging
from pathlib import Path

import pytest
import structlog

from scar.observability.logging import configure_logging
from scar.security.redaction import Redactor, global_redactor
from tests.helpers import ScriptedChatClient, call, install_scripted_router, reply

SRC = Path(__file__).resolve().parents[2] / "src" / "scar"
SECRET = "gsk_leaktest_ABCDEFGHIJKLMNOPQRSTUVWX123456"


def test_no_runtime_module_imports_tests() -> None:
    offenders = []
    for path in SRC.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            if any(n == "tests" or n.startswith("tests.") or n.startswith("conftest") for n in names):
                offenders.append(str(path))
    assert not offenders, offenders


def test_no_stub_markers_in_runtime() -> None:
    bad = []
    for path in SRC.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for marker in ("raise NotImplementedError", "TODO", "FIXME", "XXX:"):
            if marker in text:
                bad.append(f"{path.name}: {marker}")
    assert not bad, bad


def test_redactor_patterns() -> None:
    r = Redactor()
    r.add_secret("MY_TOKEN", "abcdefghij-secret-123")
    samples = {
        "token abcdefghij-secret-123 here": "abcdefghij-secret-123",
        "Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.abcdefghijk": "eyJhbGci",
        "https://user:hunter2pass@example.com/x": "hunter2pass",
        "$pw = ConvertTo-SecureString 'P@ssw0rd!' -AsPlainText": "P@ssw0rd!",
        "net use x: \\\\srv\\s /user:bob password=Sup3rS3cret": "Sup3rS3cret",
        "key: AIzaSyA1234567890abcdefghijklmnopqrstuv": "AIzaSyA1234567890",
        "ghp_abcdefghijklmnopqrstuvwxyz0123456789": "ghp_abcdef",
        "-----BEGIN RSA PRIVATE KEY-----\nMIIE\n-----END RSA PRIVATE KEY-----": "MIIE",
    }
    for text, secret in samples.items():
        out = r.redact(text)
        assert secret not in out, (text, out)
        assert r.contains_secret(text)
    assert r.redact("the token budget is 400") == "the token budget is 400"


def test_secrets_not_in_logs(tmp_path: Path) -> None:
    global_redactor().add_secret("GROQ_API_KEY", SECRET)
    log_file = configure_logging(tmp_path, "DEBUG")
    structlog.get_logger("t").info("calling provider", headers={"Authorization": f"Bearer {SECRET}"}, note=f"key={SECRET}",
                                   reasoning="private chain of thought")
    logging.getLogger("stdlib").warning("stdlib log with %s", SECRET)
    for h in logging.getLogger().handlers:
        h.flush()
    text = log_file.read_text(encoding="utf-8")
    assert SECRET not in text
    assert "private chain of thought" not in text
    assert "calling provider" in text


async def test_secrets_not_in_model_payload_trace_or_tool_output(runtime_parts, ctx_factory, sandbox: Path) -> None:
    from scar.agent.runner import TaskManager

    services = runtime_parts["services"]
    global_redactor().add_secret("GROQ_API_KEY", SECRET)
    (sandbox / "config.txt").write_text(f"api_key = {SECRET}\n")
    client = ScriptedChatClient("s", [reply(call("fs.read", path=str(sandbox / "config.txt"))),
                                      reply(call("finish", summary="Read it."))])
    install_scripted_router(services, {"s": client})
    tm = TaskManager(services, runtime_parts["registry"], runtime_parts["pipeline"])
    task = await tm.run(f"read {sandbox / 'config.txt'}")
    payload = json.dumps([r.model_dump(mode="json") for _, r in client.requests])
    assert SECRET not in payload
    assert SECRET not in json.dumps(task.model_dump(mode="json"), default=str)
    trace = services.tracer.path_for(task.task_id).read_text(encoding="utf-8")
    assert SECRET not in trace
    rows = services.db.query("SELECT * FROM tool_calls WHERE task_id = ?", (task.task_id,))
    assert SECRET not in json.dumps(rows, default=str)
    audit = json.dumps(services.db.query("SELECT * FROM audit_log"))
    assert SECRET not in audit


def test_config_file_rejects_secrets(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from scar.config.settings import ConfigError, load_settings

    monkeypatch.setenv("SCAR_CONFIG_DIR", str(tmp_path))
    (tmp_path / "config.toml").write_text('groq_api_key = "gsk_x"\n', encoding="utf-8")
    with pytest.raises(ConfigError):
        load_settings()


async def test_env_secrets_not_passed_to_children(runtime_parts, ctx_factory, monkeypatch: pytest.MonkeyPatch) -> None:
    import sys

    monkeypatch.setenv("OPENAI_API_KEY", "sk-proj-shouldnotleak123456789")
    obs = await runtime_parts["pipeline"].execute("terminal.exec", {"argv": [sys.executable, "-c",
                                                  "import os;print(os.environ.get('OPENAI_API_KEY','absent'))"]}, ctx_factory("x"))
    assert "absent" in obs.result.data["stdout"]


async def test_scar_refuses_to_read_its_own_secrets(runtime_parts, ctx_factory) -> None:
    services = runtime_parts["services"]
    (services.settings.data_path / "ipc.json").write_text('{"token": "x"}')
    obs = await runtime_parts["pipeline"].execute("fs.read", {"path": str(services.settings.data_path / "ipc.json")}, ctx_factory("x"))
    assert obs.result.status.value == "denied"
