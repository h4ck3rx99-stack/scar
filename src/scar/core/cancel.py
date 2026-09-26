"""Cooperative cancellation tokens usable from asyncio code and worker threads."""

from __future__ import annotations

import asyncio
import contextlib
import threading
from collections.abc import Awaitable, Callable
from typing import TypeVar

from scar.core.errors import Cancelled

T = TypeVar("T")


class CancelToken:
    """Hierarchical cancellation token.

    Cancelling a token cancels all of its children. The token is safe to read
    from worker threads (``threading.Event``) and awaitable from the event loop.
    """

    def __init__(self, parent: CancelToken | None = None, name: str = "") -> None:
        self.name = name
        self._event = threading.Event()
        self._reason = ""
        self._children: list[CancelToken] = []
        self._callbacks: list[Callable[[str], None]] = []
        self._lock = threading.Lock()
        self._waiters: list[tuple[asyncio.AbstractEventLoop, asyncio.Event]] = []
        if parent is not None:
            parent._add_child(self)

    def _add_child(self, child: CancelToken) -> None:
        with self._lock:
            already = self._event.is_set()
            if not already:
                self._children.append(child)
        if already:
            child.cancel(self._reason)

    def child(self, name: str = "") -> CancelToken:
        return CancelToken(parent=self, name=name)

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    @property
    def reason(self) -> str:
        return self._reason

    def on_cancel(self, callback: Callable[[str], None]) -> None:
        """Register a callback invoked (once) when the token is cancelled."""
        with self._lock:
            if not self._event.is_set():
                self._callbacks.append(callback)
                return
        callback(self._reason)

    def cancel(self, reason: str = "cancelled") -> None:
        with self._lock:
            if self._event.is_set():
                return
            self._reason = reason
            self._event.set()
            children = list(self._children)
            callbacks = list(self._callbacks)
            waiters = list(self._waiters)
            self._callbacks.clear()
            self._waiters.clear()
        for loop, event in waiters:
            if not loop.is_closed():
                loop.call_soon_threadsafe(event.set)
        for callback in callbacks:
            try:
                callback(reason)
            except Exception:  # noqa: BLE001 - one failing callback must not block others
                continue
        for c in children:
            c.cancel(reason)

    def raise_if_cancelled(self) -> None:
        if self._event.is_set():
            raise Cancelled(self._reason)

    async def wait(self) -> str:
        loop = asyncio.get_running_loop()
        event = asyncio.Event()
        with self._lock:
            if self._event.is_set():
                return self._reason
            self._waiters.append((loop, event))
        await event.wait()
        return self._reason

    def wait_sync(self, timeout: float | None = None) -> bool:
        return self._event.wait(timeout)


async def run_cancellable(awaitable: Awaitable[T], token: CancelToken) -> T:
    """Run an awaitable, cancelling it when ``token`` fires. Raises ``Cancelled``."""
    if token.cancelled:
        if asyncio.iscoroutine(awaitable):
            awaitable.close()
        raise Cancelled(token.reason)
    task: asyncio.Future[T] = asyncio.ensure_future(awaitable)
    waiter = asyncio.ensure_future(token.wait())
    try:
        done, _ = await asyncio.wait({task, waiter}, return_when=asyncio.FIRST_COMPLETED)
        if task in done:
            return task.result()
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):  # the task is being torn down; its error is moot
            await task
        raise Cancelled(token.reason)
    finally:
        waiter.cancel()
