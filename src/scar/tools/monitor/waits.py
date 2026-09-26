"""Event-based waits: process exit via a process handle (WaitForSingleObject), no tight polling."""

from __future__ import annotations

import asyncio
import sys

import psutil

from scar.core.cancel import CancelToken

SYNCHRONIZE = 0x00100000
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
WAIT_OBJECT_0 = 0
WAIT_TIMEOUT = 0x102


def _wait_handle(pid: int, timeout_s: float, cancel: CancelToken) -> int | None:
    """Block in a worker thread on the process handle; return exit code or None on timeout/cancel."""
    if sys.platform != "win32":
        try:
            p = psutil.Process(pid)
            return p.wait(timeout=timeout_s)
        except (psutil.TimeoutExpired, psutil.NoSuchProcess):
            return None
    import ctypes
    from ctypes import wintypes

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.OpenProcess.restype = wintypes.HANDLE
    k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    k32.WaitForSingleObject.restype = wintypes.DWORD
    k32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    k32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    k32.CloseHandle.argtypes = [wintypes.HANDLE]
    k32.CreateEventW.restype = wintypes.HANDLE
    k32.CreateEventW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR]
    k32.SetEvent.argtypes = [wintypes.HANDLE]
    k32.WaitForMultipleObjects.restype = wintypes.DWORD
    k32.WaitForMultipleObjects.argtypes = [wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE), wintypes.BOOL, wintypes.DWORD]
    handle = k32.OpenProcess(SYNCHRONIZE | PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return None
    cancel_event = k32.CreateEventW(None, True, False, None)
    closed = [False]
    cancel.on_cancel(lambda _reason: None if closed[0] else k32.SetEvent(cancel_event))
    try:
        handles = (wintypes.HANDLE * 2)(handle, cancel_event)
        timeout_ms = min(int(timeout_s * 1000), 0xFFFFFFFE)
        rc = k32.WaitForMultipleObjects(2, handles, False, timeout_ms)
        if rc == WAIT_OBJECT_0:
            code = wintypes.DWORD()
            if k32.GetExitCodeProcess(handle, ctypes.byref(code)):
                value = int(code.value)
                return value - 2**32 if value >= 2**31 else value
        return None
    finally:
        closed[0] = True
        k32.CloseHandle(handle)
        k32.CloseHandle(cancel_event)


async def wait_for_pid_exit(pid: int, timeout_s: float, cancel: CancelToken) -> int | None:
    return await asyncio.to_thread(_wait_handle, pid, timeout_s, cancel)


def open_exit_waiter(pid: int) -> int | None:
    """Return a raw process handle usable with WaitForSingleObject (caller closes), or None."""
    if sys.platform != "win32":
        return None
    import ctypes
    from ctypes import wintypes

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.OpenProcess.restype = wintypes.HANDLE
    h = k32.OpenProcess(SYNCHRONIZE | PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    return int(h) if h else None
