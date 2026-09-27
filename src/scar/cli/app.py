"""SCAR command-line interface (C11)."""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from typing import Annotated, Any

import typer
from rich.markup import escape

from scar.cli.render import Renderer, console
from scar.config import paths as cfg_paths
from scar.config.settings import SECRET_KEYS, ConfigError, Settings, load_settings

app = typer.Typer(add_completion=False, no_args_is_help=False, invoke_without_command=True,
                  help="SCAR — an AI operator for your Windows computer. Run `scar` for the interactive prompt.")
config_app = typer.Typer(help="Show and change configuration.")
tasks_app = typer.Typer(help="List, inspect and cancel tasks.")
perm_app = typer.Typer(help="Permissions: grants and policy.")
memory_app = typer.Typer(help="Long-term memory.")
providers_app = typer.Typer(help="Model/search/speech providers.")
daemon_app = typer.Typer(help="Background runtime.")
voice_app = typer.Typer(help="Voice devices and tests.")
auth_app = typer.Typer(help="Sign in to Google, Microsoft or Telegram.")
for sub, name in ((config_app, "config"), (tasks_app, "tasks"), (perm_app, "permissions"), (memory_app, "memory"),
                  (providers_app, "providers"), (daemon_app, "daemon"), (voice_app, "voice"), (auth_app, "auth")):
    app.add_typer(sub, name=name)

_STATE: dict[str, Any] = {}


def _settings() -> Settings:
    try:
        return load_settings(**_STATE.get("overrides", {}))
    except ConfigError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(2) from exc


def _run(coro: Any) -> Any:
    try:
        return asyncio.run(coro)
    except KeyboardInterrupt:
        console.print("[yellow]Interrupted.[/yellow]")
        raise typer.Exit(130) from None


@app.callback()
def main_callback(
    ctx: typer.Context,
    voice: Annotated[bool, typer.Option("--voice", help="Start a voice session (with text fallback)")] = False,
    verbose: Annotated[bool, typer.Option("--verbose", "-v", help="Show tools and timings")] = False,
    debug: Annotated[bool, typer.Option("--debug", help="Show internals: providers, fallbacks, logs")] = False,
    autonomy: Annotated[int | None, typer.Option("--autonomy", min=0, max=4, help="Autonomy level 0-4 for this run")] = None,
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Report side-effecting actions instead of doing them")] = False,
    background: Annotated[bool, typer.Option("--background", "-b", help="Run the objective in the daemon in the background")] = False,
    no_daemon: Annotated[bool, typer.Option("--no-daemon", help="Do not attach to a running daemon")] = False,
) -> None:
    overrides: dict[str, Any] = {"verbose": verbose or None, "debug": debug or None, "dry_run": dry_run or None}
    if autonomy is not None:
        overrides["autonomy_level"] = autonomy
    _STATE["overrides"] = {k: v for k, v in overrides.items() if v is not None}
    _STATE["verbose"], _STATE["debug"], _STATE["no_daemon"] = verbose, debug, no_daemon
    _STATE["background"] = background
    if ctx.invoked_subcommand is not None:
        return
    settings = _settings()
    renderer = Renderer(verbose=verbose, debug=debug)
    if voice:
        from scar.voice.cli import run_voice

        raise typer.Exit(_run(run_voice(settings, renderer)))
    raise typer.Exit(_run(_repl(settings, renderer, not no_daemon)))


@app.command("run", hidden=True)
def run_objective(objective: Annotated[list[str], typer.Argument(help="What SCAR should do")]) -> None:
    """Run one objective (`scar "<objective>"` is shorthand for this)."""
    settings = _settings()
    renderer = Renderer(verbose=_STATE.get("verbose", False), debug=_STATE.get("debug", False))
    text = " ".join(objective)
    raise typer.Exit(_run(_one_shot(settings, renderer, text, _STATE.get("background", False), not _STATE.get("no_daemon"))))


async def _one_shot(settings: Settings, renderer: Renderer, text: str, background: bool, prefer_daemon: bool) -> int:
    from scar.cli.session import DaemonSession, EmbeddedSession, open_session

    session = await open_session(settings, renderer, prefer_daemon=prefer_daemon or background, with_hotkeys=True)
    if background and not isinstance(session, DaemonSession):
        console.print("[yellow]No daemon running; running in the foreground. Start one with `scar daemon start`.[/yellow]")
    try:
        result = await session.run(text, background=background and isinstance(session, DaemonSession))
        handed_off = session.handoff_browser() if isinstance(session, EmbeddedSession) else None
    finally:
        await session.stop()
    renderer.result(result["status"], result["summary"], result.get("verified"))
    if handed_off:
        console.print(f"[dim]{handed_off}[/dim]")
    return 0 if result["status"] in ("succeeded", "running") else 1


async def _repl(settings: Settings, renderer: Renderer, prefer_daemon: bool) -> int:
    from scar.cli.repl import Repl
    from scar.cli.session import open_session

    session = await open_session(settings, renderer, prefer_daemon=prefer_daemon)
    try:
        await Repl(settings, session, renderer).loop()
    finally:
        await session.stop()
    return 0


# ---------------------------------------------------------------------- doctor / status / logs
@app.command()
def doctor(deep: Annotated[bool, typer.Option(help="Also send a tiny test prompt to each configured provider")] = False) -> None:
    """Check the environment, providers, devices and security configuration."""
    from scar.cli.doctor import Doctor, print_results
    from scar.runtime.builder import build_services

    settings = _settings()

    async def go() -> list[Any]:
        svc = build_services(settings)
        try:
            return await Doctor(settings, svc, deep=deep).run()
        finally:
            await svc.router.aclose()
            if svc.command_guard.ps_parser:
                svc.command_guard.ps_parser.close()
            svc.db.close()

    results = _run(go())
    print_results(results)
    failures = [r for r in results if r.ok is False]
    console.print(f"\n{len(results)} checks: {sum(r.ok is True for r in results)} ok, "
                  f"{sum(r.ok is None for r in results)} warnings, {len(failures)} problems")
    raise typer.Exit(1 if failures else 0)


@app.command()
def status(as_json: Annotated[bool, typer.Option("--json")] = False) -> None:
    """Tasks, monitors, loaded models, resources, provider health, data sent, permissions."""
    from scar.cli.status import collect_status, print_status
    from scar.runtime.ipc import IpcClient, daemon_alive

    settings = _settings()

    async def go() -> dict[str, Any]:
        if await daemon_alive(settings.data_path):
            c = IpcClient(settings.data_path)
            await c.connect()
            try:
                resp = await c.request("status")
                return dict((resp or {}).get("status") or {}) | {"source": "daemon"}
            finally:
                await c.close()
        from scar.runtime.builder import build_services

        svc = build_services(settings)
        try:
            from scar.tools.monitor.service import MonitorService

            svc.monitors = MonitorService(svc)
            return collect_status(svc, None) | {"source": "database (no runtime running)"}
        finally:
            if svc.command_guard.ps_parser:
                svc.command_guard.ps_parser.close()
            svc.db.close()

    st = _run(go())
    if as_json:
        console.print_json(json.dumps(st, default=str))
    else:
        print_status(st)
        console.print(f"[dim]source: {st.get('source')}[/dim]")


@app.command()
def logs(task: Annotated[str | None, typer.Option("--task", help="Only this task's trace")] = None,
         follow: Annotated[bool, typer.Option("--follow", "-f")] = False,
         level: Annotated[str | None, typer.Option("--level", help="minimum level: debug/info/warning/error")] = None,
         lines: Annotated[int, typer.Option("-n")] = 40) -> None:
    """Show recent log lines (or one task's trace)."""
    settings = _settings()
    if task:
        from scar.observability.tracing import Tracer

        for e in Tracer(settings.log_path / "traces").read(task)[-lines:]:
            console.print(f"[dim]{e.get('at', '')[11:19]}[/dim] {e.get('kind')}: {e.get('message') or e.get('tool') or ''}")
        return
    path = settings.log_path / "scar.jsonl"
    if not path.exists():
        console.print(f"No log file yet at {path}")
        return
    order = {"debug": 10, "info": 20, "warning": 30, "error": 40, "critical": 50}
    min_level = order.get((level or "debug").lower(), 10)

    def show(line: str) -> None:
        try:
            e = json.loads(line)
        except json.JSONDecodeError:
            return
        if order.get(str(e.get("level", "info")), 20) < min_level:
            return
        extra = {k: v for k, v in e.items() if k not in ("event", "level", "timestamp", "logger")}
        console.print(f"[dim]{str(e.get('timestamp', ''))[11:19]}[/dim] {e.get('level', ''):7} {e.get('logger', '')}: "
                      f"{e.get('event')} {json.dumps(extra, default=str)[:200] if extra else ''}")

    with path.open("r", encoding="utf-8", errors="replace") as fh:
        for line in fh.readlines()[-lines:]:
            show(line)
        while follow:
            line = fh.readline()
            if line:
                show(line)
            else:
                time.sleep(0.5)


# ---------------------------------------------------------------------- config
@config_app.command("show")
def config_show() -> None:
    """Effective configuration (secrets masked)."""
    from scar.security.secrets import SecretStore

    settings = _settings()
    for key, value in sorted(settings.model_dump().items()):
        console.print(f"{key} = {value!r}")
    console.print("\n[bold]Secrets[/bold] (values never shown)")
    store = SecretStore()
    for name in sorted(SECRET_KEYS | {"CLOUDFLARE_ACCOUNT_ID", "AZURE_SPEECH_REGION", "TELEGRAM_API_ID"}):
        src = store.source_of(name)
        console.print(f"{name}: {'set via ' + src if src else '[dim]not set[/dim]'}")


@config_app.command("path")
def config_path() -> None:
    """Where configuration and data live."""
    settings = _settings()
    console.print(f"config file: {cfg_paths.config_file()}")
    console.print(f"policy file: {cfg_paths.user_policy_file()}")
    console.print(f"providers file: {cfg_paths.user_providers_file()}")
    console.print(f"data: {settings.data_path}")
    console.print(f"logs: {settings.log_path}")


@config_app.command("validate")
def config_validate() -> None:
    """Validate configuration, policy and provider files."""
    settings = _settings()
    from scar.providers.capabilities import Catalog
    from scar.security.policy import PolicyEngine

    try:
        pe = PolicyEngine.load(None)
        problems = pe.validate()
        Catalog.load()
    except ConfigError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc
    if problems:
        for p in problems:
            console.print(f"[red]✗[/red] {p}")
        raise typer.Exit(1)
    console.print(f"[green]✓[/green] configuration valid (autonomy {settings.autonomy_level}, data {settings.data_path})")


@config_app.command("set")
def config_set(key: str, value: str) -> None:
    """Set a non-secret setting in config.toml (e.g. `scar config set autonomy_level 2`)."""
    import tomllib

    import tomli_w

    field = key.lower().removeprefix("scar_")
    if field.upper() in SECRET_KEYS or any(m in field for m in ("password", "token", "api_key", "secret")):
        console.print("[red]That is a secret; use `scar config set-secret NAME` (stored in Windows Credential Manager).[/red]")
        raise typer.Exit(2)
    if field not in Settings.model_fields:
        console.print(f"[red]Unknown setting {key!r}.[/red]")
        raise typer.Exit(2)
    path = cfg_paths.config_file()
    data: dict[str, Any] = tomllib.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    parsed: Any = value
    ann = str(Settings.model_fields[field].annotation)
    if "bool" in ann:
        parsed = value.lower() in ("1", "true", "yes", "on")
    elif "int" in ann and value.lstrip("-").isdigit():
        parsed = int(value)
    elif "float" in ann:
        try:
            parsed = float(value)
        except ValueError:
            parsed = value
    elif "list" in ann:
        parsed = [v.strip() for v in value.replace(";", ",").split(",") if v.strip()]
    data[field] = parsed
    try:
        Settings.model_validate({**Settings().model_dump(), field: parsed})
    except Exception as exc:
        console.print(f"[red]Invalid value: {exc}[/red]")
        raise typer.Exit(2) from exc
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(tomli_w.dumps(data), encoding="utf-8")
    console.print(f"[green]✓[/green] {field} = {parsed!r} saved to {path}")


@config_app.command("set-secret")
def config_set_secret(name: str) -> None:
    """Store a secret (API key/token) in Windows Credential Manager."""
    import getpass

    from scar.security.secrets import SecretStore

    value = getpass.getpass(f"{name}: ")
    if not value.strip():
        console.print("Nothing stored.")
        raise typer.Exit(1)
    SecretStore().set(name.strip().upper(), value.strip())
    console.print(f"[green]✓[/green] {name.upper()} stored in Credential Manager")


@config_app.command("delete-secret")
def config_delete_secret(name: str) -> None:
    """Remove a secret from Windows Credential Manager."""
    from scar.security.secrets import SecretStore

    ok = SecretStore().delete(name.strip().upper())
    console.print("Removed." if ok else "It was not stored in Credential Manager.")


# ---------------------------------------------------------------------- tasks
def _db(settings: Settings) -> Any:
    from scar.storage.db import Database

    return Database(settings.db_path)


@tasks_app.command("list")
def tasks_list(limit: int = 20) -> None:
    settings = _settings()
    db = _db(settings)
    try:
        rows = db.query("SELECT task_id, status, objective, result_summary, created_at FROM tasks WHERE parent_task_id IS NULL "
                        "ORDER BY created_at DESC LIMIT ?", (limit,))
        if not rows:
            console.print("No tasks yet. Run [bold]scar[/bold] and ask for something.")
        for r in rows:
            console.print(f"{r['task_id']}  {escape('[' + r['status'] + ']')}  {escape(r['objective'][:60])}  "
                          f"[dim]{escape((r['result_summary'] or '')[:60])}[/dim]")
    finally:
        db.close()


@tasks_app.command("show")
def tasks_show(task_id: str) -> None:
    settings = _settings()
    db = _db(settings)
    try:
        r = db.query_one("SELECT * FROM tasks WHERE task_id = ? OR task_id LIKE ?", (task_id, f"%{task_id}"))
        if not r:
            console.print("No such task.")
            raise typer.Exit(1)
        console.print(f"[bold]{r['objective']}[/bold]\nstatus: {r['status']}\nresult: {r['result_summary']}\n"
                      f"started: {r['created_at']}  finished: {r['finished_at']}")
        for c in db.query("SELECT tool, status, risk, decision, summary, verified, duration_ms FROM tool_calls WHERE task_id = ? "
                          "ORDER BY created_at", (r["task_id"],)):
            v = {1: "✓", 0: "✗", None: "·"}[c["verified"]]
            console.print(f"  {v} {c['tool']} [{c['risk']}/{c['decision']}] {c['status']} {c['duration_ms']}ms — {c['summary'][:90]}")
    finally:
        db.close()


@tasks_app.command("cancel")
def tasks_cancel(task_id: Annotated[str | None, typer.Argument()] = None) -> None:
    from scar.runtime.ipc import IpcClient

    settings = _settings()

    async def go() -> Any:
        c = IpcClient(settings.data_path)
        if not await c.connect():
            return None
        try:
            return await c.request("cancel", task_id=task_id)
        finally:
            await c.close()

    resp = _run(go())
    if resp is None:
        console.print("No running SCAR runtime to cancel tasks in.")
        raise typer.Exit(1)
    console.print(f"Cancelled: {', '.join(resp.get('cancelled') or []) or 'nothing was running'}")


# ---------------------------------------------------------------------- permissions
def _grant_store(settings: Settings) -> Any:
    from scar.security.grants import GrantStore

    return GrantStore(_db(settings), "cli")


@perm_app.command("list")
def perm_list() -> None:
    settings = _settings()
    gs = _grant_store(settings)
    try:
        grants = gs.list()
        if not grants:
            console.print("No persistent permissions.")
        for g in grants:
            console.print(f"{g.grant_id}  {g.effect:5} {g.kind.value:9} tool={g.tool or '*'} {g.match or ''} scope={g.scope.value} "
                          f"max={g.max_risk.name} uses={g.uses}" + (f" expires {g.expires_at:%Y-%m-%d %H:%M}" if g.expires_at else ""))
    finally:
        gs.db.close()


@perm_app.command("revoke")
def perm_revoke(grant_id: Annotated[str, typer.Argument(help="grant id, or 'all'")]) -> None:
    from scar.security.grants import UserAuthority

    settings = _settings()
    gs = _grant_store(settings)
    try:
        auth = UserAuthority("cli", "scar permissions revoke")
        n = gs.revoke_all(auth) if grant_id == "all" else int(gs.revoke(grant_id, auth))
        console.print(f"Revoked {n} permission(s).")
    finally:
        gs.db.close()


@perm_app.command("policy")
def perm_policy() -> None:
    from scar.security.policy import PolicyEngine

    pe = PolicyEngine.load(None)
    console.print(f"[bold]Hard-deny protections[/bold] (edit {cfg_paths.user_policy_file()} by hand to change)")
    for k, v in sorted(pe.hard_deny.items()):
        console.print(f"  {'✓' if v else '[yellow]OFF[/yellow]'} {k}")
    console.print("[bold]Rules[/bold] (evaluated in order; DENY beats ALLOW)")
    for r in pe.rules:
        console.print(f"  {r.id} [{r.effect.value}] {r.reason} ({r.source})")
    for p in pe.validate():
        console.print(f"[red]✗ {p}[/red]")


# ---------------------------------------------------------------------- memory
def _memory(settings: Settings) -> Any:
    from scar.memory.store import MemoryStore
    from scar.providers.embeddings.service import EmbeddingService

    return MemoryStore(_db(settings), EmbeddingService(settings, None))


@memory_app.command("list")
def memory_list(category: str | None = None, limit: int = 50) -> None:
    mem = _memory(_settings())
    try:
        items = mem.list(category, limit)
        if not items:
            console.print("Nothing remembered yet. Tell SCAR [bold]remember that …[/bold] to add a memory.")
        for m in items:
            console.print(f"{m.id}  {escape('[' + m.category + ']')} {escape(m.text)}")
    finally:
        mem.db.close()


@memory_app.command("search")
def memory_search(query: str, k: int = 8) -> None:
    mem = _memory(_settings())
    try:
        hits = mem.search(query, k=k)
        if not hits:
            console.print("No matching memories.")
        for m in hits:
            console.print(f"{m.score:.2f}  {m.id}  {escape('[' + m.category + ']')} {escape(m.text)}")
    finally:
        mem.db.close()


@memory_app.command("forget")
def memory_forget(target: Annotated[str, typer.Argument(help="memory id or a description")]) -> None:
    mem = _memory(_settings())
    try:
        removed = mem.forget(mid=target) if target.startswith("mem_") else mem.forget(query=target)
        console.print(f"Forgot {len(removed)} memory item(s).")
    finally:
        mem.db.close()


@memory_app.command("wipe")
def memory_wipe(yes: Annotated[bool, typer.Option("--yes")] = False) -> None:
    if not yes and not typer.confirm("Delete ALL memories?"):
        raise typer.Exit(1)
    mem = _memory(_settings())
    try:
        console.print(f"Deleted {mem.wipe()} memories.")
        mem.db.execute("DELETE FROM conversation")
    finally:
        mem.db.close()


# ---------------------------------------------------------------------- providers
@providers_app.command("list")
def providers_list() -> None:
    from scar.providers.capabilities import Catalog
    from scar.security.secrets import SecretStore

    cat = Catalog.load()
    store = SecretStore()
    for pid, spec in cat.providers.items():
        need = spec.api_key_env or []
        have = spec.local or not need or any(store.has(n) for n in need)
        cats = [c for c, lst in cat.categories.items() if any(x.provider == pid for x in lst)]
        console.print(f"{'✓' if have else '·'} {pid:12} {spec.tier:5} {', '.join(cats):35} {spec.data_terms[:70]}")
    for c, lst in cat.categories.items():
        console.print(f"[bold]{c}[/bold]: " + " → ".join(f"{x.provider}({','.join(x.models[:2])})" for x in lst))


@providers_app.command("test")
def providers_test(provider: Annotated[str | None, typer.Argument()] = None) -> None:
    from scar.runtime.builder import build_services

    settings = _settings()

    async def go() -> list[dict[str, Any]]:
        svc = build_services(settings)
        try:
            targets = [provider] if provider else [p for p, s in svc.router.catalog.providers.items()
                                                  if svc.router.credential_status(s)[0] and s.kind != "llamacpp"]
            return [await svc.router.probe(p) for p in targets]
        finally:
            await svc.router.aclose()
            svc.db.close()

    for r in _run(go()):
        console.print(f"{'✓' if r.get('ok') else '✗'} {r['provider']}: {r.get('model', '')} {r.get('latency_ms', '')} "
                      f"{'ms' if r.get('latency_ms') else ''} {r.get('detail', '')}")


# ---------------------------------------------------------------------- daemon
@daemon_app.command("run", hidden=True)
def daemon_run() -> None:
    """Run the daemon in this process (used by `daemon start` and the logon task)."""
    from scar.runtime.daemon import serve

    raise typer.Exit(asyncio.run(serve(_settings())))


@daemon_app.command("start")
def daemon_start() -> None:
    from scar.runtime.daemon import spawn_detached
    from scar.runtime.ipc import daemon_alive

    settings = _settings()
    if asyncio.run(daemon_alive(settings.data_path)):
        console.print("The daemon is already running.")
        return
    pid = spawn_detached()
    for _ in range(60):
        time.sleep(0.5)
        if asyncio.run(daemon_alive(settings.data_path)):
            from scar.runtime.ipc import read_endpoint

            # the spawned PID can be the venv launcher; the daemon records its own PID in the endpoint file
            console.print(f"[green]✓[/green] daemon started (PID {(read_endpoint(settings.data_path) or {}).get('pid', pid)})")
            return
    console.print("[red]The daemon did not come up; see `scar logs`.[/red]")
    raise typer.Exit(1)


@daemon_app.command("stop")
def daemon_stop() -> None:
    from scar.runtime.ipc import IpcClient

    settings = _settings()

    async def go() -> bool:
        c = IpcClient(settings.data_path)
        if not await c.connect():
            return False
        try:
            await c.request("stop")
            return True
        finally:
            await c.close()

    if not asyncio.run(go()):
        console.print("The daemon is not running.")
        return
    console.print("Stopping…")
    from scar.runtime.ipc import daemon_alive

    for _ in range(40):
        time.sleep(0.5)
        if not asyncio.run(daemon_alive(settings.data_path)):
            console.print("[green]✓[/green] daemon stopped")
            return
    console.print("[yellow]The daemon did not confirm shutdown.[/yellow]")


@daemon_app.command("status")
def daemon_status() -> None:
    from scar.runtime.daemon import logon_task_installed
    from scar.runtime.ipc import daemon_alive, read_endpoint

    settings = _settings()
    alive = asyncio.run(daemon_alive(settings.data_path))
    ep = read_endpoint(settings.data_path)
    console.print(f"daemon: {'running (PID ' + str(ep.get('pid')) + ')' if alive and ep else 'not running'}")
    if sys.platform == "win32":
        console.print(f"start at logon: {'installed' if logon_task_installed() else 'not installed'}")


@daemon_app.command("install")
def daemon_install() -> None:
    """Register a Task Scheduler logon task (HIGH risk: asks for confirmation)."""
    from scar.runtime.daemon import install_logon_task, logon_task_command
    from scar.security.audit import AuditLog

    console.print(f"[bold red]HIGH risk[/bold red]: register a Windows logon task that runs\n  {logon_task_command()}\n"
                  "every time you sign in (non-elevated).")
    if input("Type YES to confirm: ").strip() != "YES":
        console.print("Not installed.")
        raise typer.Exit(1)
    ok, out = install_logon_task()
    settings = _settings()
    db = _db(settings)
    try:
        AuditLog(db).append("autostart_installed" if ok else "autostart_install_failed", {"command": logon_task_command(), "output": out})
    finally:
        db.close()
    console.print("[green]✓[/green] installed" if ok else f"[red]failed:[/red] {out}")


@daemon_app.command("uninstall")
def daemon_uninstall() -> None:
    from scar.runtime.daemon import uninstall_logon_task

    ok, out = uninstall_logon_task()
    console.print("[green]✓[/green] removed" if ok else f"[yellow]{out}[/yellow]")


# ---------------------------------------------------------------------- voice / auth
@voice_app.command("devices")
def voice_devices() -> None:
    from scar.voice.audio_io import list_devices

    for d in list_devices():
        console.print(f"{d['index']:>3} {'IN ' if d['inputs'] else '   '}{'OUT' if d['outputs'] else '   '} {d['name']} "
                      f"[dim]({d['hostapi']}){' default' if d['default'] else ''}[/dim]")


@voice_app.command("test")
def voice_test(seconds: float = 4.0) -> None:
    """Record a few seconds, transcribe it, and speak the transcript back."""
    from scar.voice.cli import voice_selftest

    raise typer.Exit(_run(voice_selftest(_settings(), seconds)))


@auth_app.command("google")
def auth_google() -> None:
    _auth("google")


@auth_app.command("microsoft")
def auth_microsoft() -> None:
    _auth("microsoft")


@auth_app.command("telegram")
def auth_telegram() -> None:
    _auth("telegram")


def _auth(name: str) -> None:
    from scar.integrations.cli_auth import auth_commands
    from scar.runtime.builder import build_services

    settings = _settings()

    async def go() -> None:
        svc = build_services(settings)
        try:
            await auth_commands()[name](svc)
        finally:
            svc.db.close()

    _run(go())


def _group_default(group: typer.Typer, default: str | None) -> None:
    """`scar config` alone shows the configuration (and likewise for the other groups) instead of a usage error."""

    @group.callback(invoke_without_command=True)
    def _default(ctx: typer.Context) -> None:
        if ctx.invoked_subcommand is not None:
            return
        get = getattr(ctx.command, "get_command", None)
        cmd = get(ctx, default) if default and get is not None else None
        if cmd is None:
            console.print(ctx.get_help())
            return
        with cmd.make_context(default, [], parent=ctx) as sub:
            cmd.invoke(sub)


for _group, _cmd in ((config_app, "show"), (tasks_app, "list"), (perm_app, "list"), (memory_app, "list"),
                     (providers_app, "list"), (daemon_app, "status"), (voice_app, "devices"), (auth_app, None)):
    _group_default(_group, _cmd)


SUBCOMMANDS = {"run", "doctor", "status", "logs", "config", "tasks", "permissions", "memory", "providers", "daemon", "voice",
               "auth", "--help", "-h"}


def _route_argv(argv: list[str]) -> list[str]:
    """`scar [options] <objective words>` → `scar [options] run <objective words>`."""
    for i, tok in enumerate(argv):
        if tok.startswith("-"):
            if tok in ("--help", "-h"):
                return argv
            continue
        if tok in SUBCOMMANDS:
            return argv
        return [*argv[:i], "run", *argv[i:]]
    return argv


def main() -> None:
    sys.argv[1:] = _route_argv(sys.argv[1:])
    if sys.platform == "win32":
        os.environ.setdefault("PYTHONIOENCODING", "utf-8")
        try:
            sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
            sys.stderr.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
        except (AttributeError, ValueError):
            pass
    app()

