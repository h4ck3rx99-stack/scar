"""Managed child processes in Win32 Job Objects (B4, C9.2).

Every process SCAR spawns is created suspended, assigned to its own Job Object
with ``JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`` and then resumed, so the whole
process tree can be terminated at once and dies automatically if SCAR exits.
On non-Windows hosts the process group is used instead.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field

import psutil
import structlog

log = structlog.get_logger("scar.jobs")

CREATE_SUSPENDED = 0x00000004
CREATE_NEW_PROCESS_GROUP = 0x00000200
CREATE_NO_WINDOW = 0x08000000


class JobObject:
    """Thin wrapper over a Win32 Job Object configured to kill its tree on close."""

    def __init__(self, name: str = "") -> None:
        self.name = name
        self._handle = None
        if sys.platform == "win32":
            import win32job

            self._handle = win32job.CreateJobObject(None, "")
            info = win32job.QueryInformationJobObject(self._handle, win32job.JobObjectExtendedLimitInformation)
            info["BasicLimitInformation"]["LimitFlags"] |= win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            win32job.SetInformationJobObject(self._handle, win32job.JobObjectExtendedLimitInformation, info)

    def assign(self, pid: int) -> bool:
        if self._handle is None:
            return False
        import win32api
        import win32con
        import win32job

        h = win32api.OpenProcess(win32con.PROCESS_SET_QUOTA | win32con.PROCESS_TERMINATE, False, pid)
        try:
            win32job.AssignProcessToJobObject(self._handle, h)
            return True
        finally:
            win32api.CloseHandle(h)

    def pids(self) -> list[int]:
        if self._handle is None:
            return []
        import win32job

        try:
            return list(win32job.QueryInformationJobObject(self._handle, win32job.JobObjectBasicProcessIdList))
        except Exception:  # noqa: BLE001 - job may already be closed
            return []

    def terminate(self, exit_code: int = 1) -> None:
        if self._handle is None:
            return
        import win32job

        with contextlib.suppress(Exception):
            win32job.TerminateJobObject(self._handle, exit_code)

    def close(self) -> None:
        if self._handle is not None:
            import win32api

            with contextlib.suppress(Exception):
                win32api.CloseHandle(self._handle)
            self._handle = None


@dataclass
class ManagedProcess:
    pid: int
    name: str
    argv: list[str]
    job: JobObject | None
    started: float = field(default_factory=time.time)
    owner_task: str | None = None
    kind: str = "command"  # command | devserver | model-server | app

    def alive(self) -> bool:
        try:
            p = psutil.Process(self.pid)
            return p.is_running() and p.status() != psutil.STATUS_ZOMBIE
        except psutil.Error:
            return False

    def tree_pids(self) -> list[int]:
        pids = set(self.job.pids()) if self.job else set()
        try:
            p = psutil.Process(self.pid)
            pids.add(p.pid)
            pids.update(c.pid for c in p.children(recursive=True))
        except psutil.Error:
            pass
        return sorted(pids)

    def kill_tree(self) -> None:
        if self.job is not None:
            self.job.terminate(1)
        for pid in self.tree_pids():
            with contextlib.suppress(psutil.Error):
                psutil.Process(pid).kill()
        if sys.platform != "win32":
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.killpg(self.pid, signal.SIGKILL)

    def release(self) -> None:
        if self.job is not None:
            self.job.close()
            self.job = None


class ProcessManager:
    """Tracks every child process SCAR starts."""

    def __init__(self) -> None:
        self._procs: dict[int, ManagedProcess] = {}
        self._lock = threading.Lock()

    @staticmethod
    def creation_flags(new_console: bool = False) -> int:
        if sys.platform != "win32":
            return 0
        flags = CREATE_SUSPENDED | CREATE_NEW_PROCESS_GROUP
        if not new_console:
            flags |= CREATE_NO_WINDOW
        return flags

    def adopt(self, pid: int, name: str, argv: list[str], *, owner_task: str | None = None, kind: str = "command",
              suspended: bool = True) -> ManagedProcess:
        """Put a just-created (suspended) process into a fresh job and resume it."""
        job: JobObject | None = None
        if sys.platform == "win32":
            job = JobObject(name)
            try:
                job.assign(pid)
            except Exception as exc:  # noqa: BLE001 - still resume; fall back to psutil tree kill
                log.warning("job_assign_failed", pid=pid, error=str(exc))
        if suspended and sys.platform == "win32":
            with contextlib.suppress(psutil.Error):
                psutil.Process(pid).resume()
        mp = ManagedProcess(pid=pid, name=name, argv=argv, job=job, owner_task=owner_task, kind=kind)
        with self._lock:
            self._procs[pid] = mp
        return mp

    def popen(self, argv: list[str], *, cwd: str | None = None, env: dict[str, str] | None = None, name: str = "",
              owner_task: str | None = None, kind: str = "command", new_console: bool = False,
              stdout: int | None = subprocess.DEVNULL, stderr: int | None = subprocess.DEVNULL,
              stdin: int | None = subprocess.DEVNULL) -> tuple[subprocess.Popen[bytes], ManagedProcess]:
        flags = self.creation_flags(new_console)
        kwargs: dict[str, object] = {}
        if sys.platform != "win32":
            kwargs["start_new_session"] = True
        proc = subprocess.Popen(argv, cwd=cwd, env=env, stdout=stdout, stderr=stderr, stdin=stdin,
                                creationflags=flags, **kwargs)  # type: ignore[call-overload]
        mp = self.adopt(proc.pid, name or os.path.basename(argv[0]), argv, owner_task=owner_task, kind=kind)
        return proc, mp

    async def spawn(self, argv: list[str], *, cwd: str | None = None, env: dict[str, str] | None = None, name: str = "",
                    owner_task: str | None = None, kind: str = "command",
                    stdin: int | None = asyncio.subprocess.DEVNULL) -> tuple[asyncio.subprocess.Process, ManagedProcess]:
        flags = self.creation_flags()
        kwargs: dict[str, object] = {}
        if sys.platform != "win32":
            kwargs["start_new_session"] = True
        proc = await asyncio.create_subprocess_exec(
            *argv, cwd=cwd, env=env, stdin=stdin, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            creationflags=flags, **kwargs,  # type: ignore[arg-type]
        )
        mp = self.adopt(proc.pid, name or os.path.basename(argv[0]), argv, owner_task=owner_task, kind=kind)
        return proc, mp

    def get(self, pid: int) -> ManagedProcess | None:
        with self._lock:
            return self._procs.get(pid)

    def list(self) -> list[ManagedProcess]:
        with self._lock:
            return list(self._procs.values())

    def forget(self, pid: int) -> None:
        with self._lock:
            mp = self._procs.pop(pid, None)
        if mp is not None:
            mp.release()

    def kill(self, pid: int) -> bool:
        with self._lock:
            mp = self._procs.pop(pid, None)
        if mp is None:
            return False
        mp.kill_tree()
        mp.release()
        return True

    def kill_task(self, task_id: str) -> int:
        n = 0
        for mp in self.list():
            if mp.owner_task == task_id:
                self.kill(mp.pid)
                n += 1
        return n

    def kill_all(self, kinds: set[str] | None = None) -> int:
        n = 0
        for mp in self.list():
            if kinds is None or mp.kind in kinds:
                self.kill(mp.pid)
                n += 1
        return n

    def reap(self) -> int:
        """Forget processes whose whole tree has exited."""
        n = 0
        for mp in self.list():
            if not mp.alive() and not [p for p in mp.tree_pids() if psutil.pid_exists(p)]:
                self.forget(mp.pid)
                n += 1
        return n

    def orphans(self) -> list[int]:
        """PIDs still alive that belonged to processes SCAR already forgot (should be empty)."""
        me = psutil.Process()
        known = {p for mp in self.list() for p in mp.tree_pids()}
        try:
            return [c.pid for c in me.children(recursive=True) if c.pid not in known]
        except psutil.Error:
            return []
