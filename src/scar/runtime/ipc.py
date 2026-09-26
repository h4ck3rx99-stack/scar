"""Authenticated local IPC (B4): JSON lines over 127.0.0.1 TCP.

* the server binds 127.0.0.1 only and rejects non-loopback peers
* a random 256-bit token is written to ``<data>/ipc.json`` whose ACL grants only the current user
* every request carries the token (constant-time compare)
* a single-instance lock prevents two runtimes on one data directory
"""

from __future__ import annotations

import asyncio
import contextlib
import hmac
import json
import os
import secrets
import sys
from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path
from typing import Any, BinaryIO

import structlog

log = structlog.get_logger("scar.ipc")

MAX_LINE = 1024 * 1024


def restrict_to_user(path: Path) -> None:
    """Replace the file's DACL with a single ACE for the current user (Windows)."""
    if sys.platform != "win32":
        os.chmod(path, 0o600)
        return
    import ntsecuritycon as con
    import win32api
    import win32security

    user, _domain, _type = win32security.LookupAccountName("", win32api.GetUserName())
    sd = win32security.GetFileSecurity(str(path), win32security.DACL_SECURITY_INFORMATION)
    dacl = win32security.ACL()
    dacl.AddAccessAllowedAce(win32security.ACL_REVISION, con.FILE_ALL_ACCESS, user)
    sd.SetSecurityDescriptorDacl(1, dacl, 0)
    win32security.SetFileSecurity(str(path), win32security.DACL_SECURITY_INFORMATION | 0x80000000, sd)  # PROTECTED_DACL


class InstanceLock:
    """Exclusive lock file; held for the lifetime of the runtime process."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._fh: BinaryIO | None = None

    def acquire(self) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fh = self.path.open("a+b")
        try:
            if sys.platform == "win32":
                import msvcrt

                fh.seek(0)
                msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            fh.close()
            return False
        fh.seek(0)
        fh.truncate()
        fh.write(str(os.getpid()).encode())
        fh.flush()
        self._fh = fh
        return True

    def release(self) -> None:
        if self._fh is None:
            return
        with contextlib.suppress(OSError):
            if sys.platform == "win32":
                import msvcrt

                self._fh.seek(0)
                msvcrt.locking(self._fh.fileno(), msvcrt.LK_UNLCK, 1)
        self._fh.close()
        self._fh = None


def read_endpoint(data_dir: Path) -> dict[str, Any] | None:
    p = data_dir / "ipc.json"
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


Handler = Callable[[dict[str, Any], "Connection"], Awaitable[None]]


class Connection:
    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self.reader = reader
        self.writer = writer
        self._lock = asyncio.Lock()

    async def send(self, obj: dict[str, Any]) -> None:
        data = (json.dumps(obj, default=str) + "\n").encode("utf-8")
        async with self._lock:
            self.writer.write(data)
            await self.writer.drain()

    async def recv(self) -> dict[str, Any] | None:
        line = await self.reader.readline()
        if not line:
            return None
        if len(line) > MAX_LINE:
            raise ValueError("message too large")
        return json.loads(line.decode("utf-8"))

    async def close(self) -> None:
        with contextlib.suppress(OSError, ConnectionError):
            self.writer.close()
            await self.writer.wait_closed()


class IpcServer:
    def __init__(self, data_dir: Path, handler: Handler) -> None:
        self.data_dir = data_dir
        self.handler = handler
        self.token = secrets.token_hex(32)
        self._server: asyncio.base_events.Server | None = None
        self.port = 0
        self.connections: set[Connection] = set()

    async def start(self) -> None:
        self._server = await asyncio.start_server(self._on_client, host="127.0.0.1", port=0, limit=MAX_LINE)
        self.port = int(self._server.sockets[0].getsockname()[1])
        path = self.data_dir / "ipc.json"
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"port": self.port, "token": self.token, "pid": os.getpid()}), encoding="utf-8")
        restrict_to_user(tmp)
        os.replace(tmp, path)
        log.info("ipc_listening", port=self.port)

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            with contextlib.suppress(Exception):
                await asyncio.wait_for(self._server.wait_closed(), 3)
        for c in list(self.connections):
            await c.close()
        with contextlib.suppress(OSError):
            ep = read_endpoint(self.data_dir)
            if ep and ep.get("pid") == os.getpid():
                (self.data_dir / "ipc.json").unlink()

    async def _on_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        peer = writer.get_extra_info("peername")
        conn = Connection(reader, writer)
        if not peer or peer[0] not in ("127.0.0.1", "::1"):
            log.warning("ipc_rejected_peer", peer=str(peer))
            await conn.close()
            return
        self.connections.add(conn)
        try:
            first = await asyncio.wait_for(conn.recv(), timeout=10)
            if not first or not hmac.compare_digest(str(first.get("token", "")), self.token):
                await conn.send({"type": "error", "error": "unauthorized"})
                return
            await self.handler(first, conn)
        except (TimeoutError, ValueError, json.JSONDecodeError, ConnectionError) as exc:
            with contextlib.suppress(Exception):
                await conn.send({"type": "error", "error": str(exc)[:200]})
        finally:
            self.connections.discard(conn)
            await conn.close()


class IpcClient:
    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir
        self.conn: Connection | None = None

    async def connect(self, timeout: float = 3.0) -> bool:
        ep = read_endpoint(self.data_dir)
        if not ep:
            return False
        try:
            reader, writer = await asyncio.wait_for(asyncio.open_connection("127.0.0.1", int(ep["port"]), limit=MAX_LINE), timeout)
        except (OSError, TimeoutError):
            return False
        self.conn = Connection(reader, writer)
        self._token = str(ep["token"])
        return True

    async def request(self, op: str, **payload: Any) -> dict[str, Any] | None:
        assert self.conn is not None
        await self.conn.send({"token": self._token, "op": op, **payload})
        return await self.conn.recv()

    async def send(self, obj: dict[str, Any]) -> None:
        assert self.conn is not None
        await self.conn.send(obj)

    async def stream(self) -> AsyncIterator[dict[str, Any]]:
        assert self.conn is not None
        while True:
            msg = await self.conn.recv()
            if msg is None:
                return
            yield msg

    async def close(self) -> None:
        if self.conn is not None:
            await self.conn.close()


async def daemon_alive(data_dir: Path) -> bool:
    c = IpcClient(data_dir)
    if not await c.connect(timeout=1.5):
        return False
    try:
        resp = await asyncio.wait_for(c.request("ping"), timeout=3)
        return bool(resp and resp.get("type") == "pong")
    except (TimeoutError, OSError, json.JSONDecodeError):
        return False
    finally:
        await c.close()
