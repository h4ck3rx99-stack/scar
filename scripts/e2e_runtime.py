"""A real SCAR runtime for the desktop app's end-to-end tests, fully sandboxed:

* its own data and config folders (never the user's), dev origin allowed for the test page
* an in-memory keyring: the user's Windows Credential Manager is never read or written
* a local mock OpenAI-compatible model (the "groq" provider is pointed at it): it streams, and scripts a tool call
  for "move X to Y" (a HIGH-risk action that needs approval) and a slow reply for "slow …" (to test cancel)

    uv run python scripts/e2e_runtime.py --root <folder>        # writes <folder>/ready.json when serving
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def _memory_keyring() -> None:
    import keyring
    from keyring.backend import KeyringBackend

    class MemoryKeyring(KeyringBackend):
        priority = 100  # type: ignore[assignment]

        def __init__(self) -> None:
            super().__init__()
            self.store: dict[tuple[str, str], str] = {}

        def get_password(self, service: str, username: str) -> str | None:
            return self.store.get((service, username))

        def set_password(self, service: str, username: str, password: str) -> None:
            self.store[(service, username)] = password

        def delete_password(self, service: str, username: str) -> None:
            self.store.pop((service, username), None)

    keyring.set_keyring(MemoryKeyring())


async def mock_model(port_holder: dict[str, int]) -> Any:
    from aiohttp import web

    def reply_text(objective: str, after_tool: bool, tool_result: str) -> tuple[str, dict[str, Any] | None]:
        if after_tool:
            return (f"Done. {tool_result[:120]}", None)
        m = re.search(r"move (\S+) to (\S+)", objective, re.I)
        if m:
            return ("", {"name": "fs__move", "arguments": json.dumps({"src": m.group(1), "dst": m.group(2)})})
        return ("RAM is your computer's short-term working memory: fast storage the processor uses for running apps.", None)

    async def models(_r: web.Request) -> web.Response:
        return web.json_response({"data": [{"id": "mock-model"}]})

    async def chat(request: web.Request) -> web.StreamResponse:
        body = await request.json()
        msgs = body.get("messages", [])
        users = [m for m in msgs if m.get("role") == "user" and not str(m.get("content", "")).startswith("[SCAR runtime]")]
        objective = str(users[-1]["content"]) if users else ""
        objective = objective.split("Objective:", 1)[-1].strip().splitlines()[0] if "Objective:" in objective else objective
        last_user = max((i for i, m in enumerate(msgs) if m.get("role") == "user"), default=0)
        tool_msgs = [m for m in msgs[last_user:] if m.get("role") == "tool"]
        if "slow" in objective.lower():
            await asyncio.sleep(30)
        text, call = reply_text(objective, bool(tool_msgs), str(tool_msgs[-1].get("content", "")) if tool_msgs else "")
        if not body.get("stream"):
            msg: dict[str, Any] = {"role": "assistant", "content": text}
            if call:
                msg["tool_calls"] = [{"id": "call_1", "type": "function", "function": call}]
            return web.json_response({"model": "mock-model", "choices": [{"message": msg, "finish_reason": "stop"}],
                                      "usage": {"prompt_tokens": 10, "completion_tokens": 10}})
        resp = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
        await resp.prepare(request)
        if call:
            chunk = {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "call_1", "function": call}]}, "finish_reason": "tool_calls"}]}
            await resp.write(f"data: {json.dumps(chunk)}\n\n".encode())
        else:
            for word in re.findall(r"\S+\s*", text):
                await resp.write(f"data: {json.dumps({'choices': [{'delta': {'content': word}}]})}\n\n".encode())
                await asyncio.sleep(0.03)
        await resp.write(b"data: [DONE]\n\n")
        return resp

    app = web.Application()
    app.router.add_get("/v1/models", models)
    app.router.add_post("/v1/chat/completions", chat)
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port_holder["port"] = site._server.sockets[0].getsockname()[1]  # type: ignore[union-attr]
    return runner


async def main(root: Path, onboarding: int) -> None:
    data, config, work = root / "data", root / "config", root / "work"
    for d in (data, config, work):
        d.mkdir(parents=True, exist_ok=True)
    os.environ.update({"SCAR_DATA_DIR": str(data), "SCAR_CONFIG_DIR": str(config), "SCAR_APP_DEV": "1"})
    _memory_keyring()
    holder: dict[str, int] = {}
    runner = await mock_model(holder)
    (config / "providers.yaml").write_text(
        "providers:\n  groq:\n    kind: openai_compat\n"
        f"    base_url: http://127.0.0.1:{holder['port']}/v1\n    api_key_env: [GROQ_API_KEY]\n    tier: free\n"
        "    privacy: cloud\n    data_terms: 'Mock provider for tests.'\n"
        "categories:\n  reasoning:\n    - {provider: groq, models: [mock-model]}\n"
        "  fast:\n    - {provider: groq, models: [mock-model]}\n", encoding="utf-8")
    (config / "config.toml").write_text(
        f"allowed_roots = ['{work.as_posix()}']\nlocal_inference_policy = 'never'\ntts_provider = 'none'\n"
        f"game_mode = 'off'\napp_onboarding_step = {onboarding}\nembeddings_provider = 'none'\n", encoding="utf-8")
    from scar.security.secrets import SecretStore

    SecretStore().set("GROQ_API_KEY", "gsk_mock_" + "x" * 40)
    (work / "report.txt").write_text("quarterly report\n", encoding="utf-8")
    from scar.config.settings import load_settings
    from scar.runtime.daemon import serve

    serve_task = asyncio.create_task(serve(load_settings()))
    for _ in range(200):
        ep = data / "app-api.json"
        if ep.exists():
            (root / "ready.json").write_text(json.dumps({**json.loads(ep.read_text(encoding="utf-8")), "work": str(work),
                                                         "mock_port": holder["port"]}), encoding="utf-8")
            break
        await asyncio.sleep(0.1)
    try:
        await serve_task
    finally:
        await runner.cleanup()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, required=True)
    ap.add_argument("--onboarding", type=int, default=99)
    a = ap.parse_args()
    asyncio.run(main(a.root, a.onboarding))
