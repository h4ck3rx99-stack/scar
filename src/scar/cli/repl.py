"""Interactive REPL (C11): history, multiline input (Esc+Enter), inline approvals, slash commands."""

from __future__ import annotations

import asyncio
import contextlib
from typing import Any

from prompt_toolkit import PromptSession
from prompt_toolkit.history import FileHistory
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.patch_stdout import patch_stdout

from scar.cli.render import Renderer, console
from scar.cli.session import DaemonSession, EmbeddedSession
from scar.config.settings import Settings

HELP = """[bold]Just type what you want done.[/bold] Examples:
  open VS Code in my BISense folder · what's using my RAM · remind me in 10 minutes to stretch
  run the tests in C:\\Projects\\app · research X and save a summary to research.docx

Commands: /help  /status  /tasks  /cancel [id]  /bg <objective>  /autonomy <0-4>  /dry-run on|off  /verbose on|off  /exit
Ctrl+C cancels the running task. Esc then Enter inserts a new line."""


class Repl:
    def __init__(self, settings: Settings, session: EmbeddedSession | DaemonSession, renderer: Renderer) -> None:
        self.settings = settings
        self.session = session
        self.r = renderer
        self.autonomy: int | None = None
        self.dry_run: bool | None = None
        self._current: asyncio.Task[Any] | None = None
        kb = KeyBindings()

        @kb.add("escape", "enter")
        def _newline(event: Any) -> None:
            event.current_buffer.insert_text("\n")

        settings.data_path.mkdir(parents=True, exist_ok=True)
        self.prompt: PromptSession[str] | None
        try:
            self.prompt = PromptSession(history=FileHistory(str(settings.data_path / "history.txt")), key_bindings=kb)
        except Exception:  # noqa: BLE001 - no Windows console (e.g. mintty): plain line input
            self.prompt = None

    def _install_sigint(self) -> None:
        import signal

        loop = asyncio.get_running_loop()

        def handler(_sig: int, _frame: Any) -> None:
            if self._current is not None and not self._current.done():
                loop.call_soon_threadsafe(lambda: asyncio.ensure_future(self._cancel()))
            else:
                raise KeyboardInterrupt

        signal.signal(signal.SIGINT, handler)

    async def loop(self) -> None:
        self._install_sigint()
        mode = "daemon" if isinstance(self.session, DaemonSession) else "local runtime"
        console.print(f"[bold]SCAR[/bold] ready ({mode}). Type /help for examples.")
        while True:
            try:
                if self.prompt is not None:
                    with patch_stdout():
                        line = await self.prompt.prompt_async("› ")
                else:
                    line = await asyncio.to_thread(input, "> ")
            except (EOFError, KeyboardInterrupt):
                return
            line = line.strip()
            if not line:
                continue
            if line.startswith("/"):
                if not await self.command(line):
                    return
                continue
            await self.run(line)

    async def run(self, objective: str, background: bool = False) -> None:
        task = asyncio.create_task(self.session.run(objective, background=background, autonomy=self.autonomy, dry_run=self.dry_run))
        self._current = task
        try:
            result = await task
        except KeyboardInterrupt:
            await self._cancel()
            with contextlib.suppress(Exception):
                result = await asyncio.wait_for(task, 15)
                self.r.result(result["status"], result["summary"], result.get("verified"))
            return
        except asyncio.CancelledError:
            await self._cancel()
            return
        except ConnectionError as exc:
            console.print(f"[red]{exc}[/red]")
            return
        self.r.result(result["status"], result["summary"], result.get("verified"))

    async def _cancel(self) -> None:
        if isinstance(self.session, EmbeddedSession):
            self.session.cancel_running()
        else:
            await self.session.simple("cancel")
        console.print("[yellow]Cancelling…[/yellow]")

    async def command(self, line: str) -> bool:
        cmd, _, arg = line[1:].partition(" ")
        cmd = cmd.lower()
        if cmd in ("exit", "quit", "q"):
            return False
        if cmd == "help":
            console.print(HELP)
        elif cmd == "status":
            from scar.cli.status import collect_status, print_status

            if isinstance(self.session, EmbeddedSession):
                print_status(collect_status(self.session.rt.services, self.session.rt.tasks))
            else:
                resp = await self.session.simple("status")
                if resp:
                    print_status(resp["status"])
        elif cmd == "tasks":
            await self.run("status")
        elif cmd == "cancel":
            if isinstance(self.session, EmbeddedSession) and self.session.rt.tasks:
                ids = self.session.rt.tasks.cancel(arg or None)
            else:
                resp = await self.session.simple("cancel", task_id=arg or None)
                ids = (resp or {}).get("cancelled", [])
            console.print(f"Cancelled {len(ids)} task(s).")
        elif cmd == "bg":
            if arg:
                await self.run(arg, background=True)
        elif cmd == "autonomy":
            if arg.isdigit() and 0 <= int(arg) <= 4:
                self.autonomy = int(arg)
                console.print(f"Autonomy for this session: {self.autonomy}")
            else:
                console.print("Usage: /autonomy 0-4")
        elif cmd in ("dry-run", "dryrun"):
            self.dry_run = arg.lower() in ("on", "true", "1", "yes")
            console.print(f"Dry run {'on' if self.dry_run else 'off'}")
        elif cmd == "verbose":
            self.r.verbose = arg.lower() in ("on", "true", "1", "yes")
        else:
            console.print(f"Unknown command /{cmd}. Try /help.")
        return True
