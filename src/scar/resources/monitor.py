"""System resource sampling (C7): CPU, RAM, battery via psutil; GPU/VRAM via NVML.

Sampling is on demand. A periodic sampler runs only while local models are
loaded or tasks are active, and stops as soon as neither is true.
"""

from __future__ import annotations

import asyncio
import contextlib
import threading
import time
from dataclasses import asdict, dataclass, field
from typing import Any

import psutil
import structlog

log = structlog.get_logger("scar.resources")


@dataclass
class GpuInfo:
    index: int
    name: str
    util_percent: float
    mem_total_mb: float
    mem_used_mb: float
    mem_free_mb: float
    compute_capability: str = ""


@dataclass
class ResourceSnapshot:
    at: float
    cpu_percent: float
    ram_total_mb: float
    ram_used_mb: float
    ram_available_mb: float
    process_rss_mb: float
    battery_percent: float | None
    on_ac: bool | None
    gpus: list[GpuInfo] = field(default_factory=list)
    nvml_error: str | None = None

    @property
    def gpu(self) -> GpuInfo | None:
        return self.gpus[0] if self.gpus else None

    def as_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["gpus"] = [asdict(g) for g in self.gpus]
        return d


class _Nvml:
    def __init__(self) -> None:
        self._ok: bool | None = None
        self.error: str | None = None
        self._lock = threading.Lock()

    def _init(self) -> bool:
        if self._ok is not None:
            return self._ok
        try:
            import pynvml

            pynvml.nvmlInit()
            self._ok = True
        except Exception as exc:  # noqa: BLE001 - NVML raises many driver-specific types
            self._ok = False
            self.error = f"NVML unavailable: {exc}"
        return self._ok

    def gpus(self) -> list[GpuInfo]:
        with self._lock:
            if not self._init():
                return []
            import pynvml

            out: list[GpuInfo] = []
            try:
                for i in range(pynvml.nvmlDeviceGetCount()):
                    h = pynvml.nvmlDeviceGetHandleByIndex(i)
                    name = pynvml.nvmlDeviceGetName(h)
                    if isinstance(name, bytes):
                        name = name.decode()
                    mem = pynvml.nvmlDeviceGetMemoryInfo(h)
                    util = pynvml.nvmlDeviceGetUtilizationRates(h)
                    try:
                        major, minor = pynvml.nvmlDeviceGetCudaComputeCapability(h)
                        cc = f"{major}.{minor}"
                    except Exception:  # noqa: BLE001
                        cc = ""
                    out.append(
                        GpuInfo(
                            index=i,
                            name=str(name),
                            util_percent=float(util.gpu),
                            mem_total_mb=mem.total / 2**20,
                            mem_used_mb=mem.used / 2**20,
                            mem_free_mb=mem.free / 2**20,
                            compute_capability=cc,
                        )
                    )
            except Exception as exc:  # noqa: BLE001
                self.error = f"NVML query failed: {exc}"
            return out


_NVML = _Nvml()
_PROC = psutil.Process()
_PROC.cpu_percent(None)
psutil.cpu_percent(None)


def sample(cpu_interval: float | None = None) -> ResourceSnapshot:
    """Take one snapshot. ``cpu_interval=None`` uses the delta since the last call."""
    vm = psutil.virtual_memory()
    try:
        bat = psutil.sensors_battery()
    except (AttributeError, NotImplementedError, OSError):
        bat = None
    return ResourceSnapshot(
        at=time.time(),
        cpu_percent=psutil.cpu_percent(interval=cpu_interval),
        ram_total_mb=vm.total / 2**20,
        ram_used_mb=vm.used / 2**20,
        ram_available_mb=vm.available / 2**20,
        process_rss_mb=_PROC.memory_info().rss / 2**20,
        battery_percent=float(bat.percent) if bat else None,
        on_ac=bool(bat.power_plugged) if bat else None,
        gpus=_NVML.gpus(),
        nvml_error=_NVML.error,
    )


def process_tree_rss_mb(pid: int) -> float:
    try:
        p = psutil.Process(pid)
        total = p.memory_info().rss
        for c in p.children(recursive=True):
            with contextlib.suppress(psutil.Error):
                total += c.memory_info().rss
        return total / 2**20
    except psutil.Error:
        return 0.0


class ResourceMonitor:
    """Periodic sampler that only runs while someone holds an ``active()`` lease."""

    def __init__(self, interval: float = 10.0, history: int = 60) -> None:
        self.interval = interval
        self.history: list[ResourceSnapshot] = []
        self._max_history = history
        self._leases = 0
        self._task: asyncio.Task[None] | None = None
        self._last: ResourceSnapshot | None = None
        self.samples_taken = 0

    @property
    def last(self) -> ResourceSnapshot:
        if self._last is None or time.time() - self._last.at > 2.0:
            self._last = sample()
            self.samples_taken += 1
        return self._last

    def refresh(self) -> ResourceSnapshot:
        """Force a fresh sample (after SCAR itself freed resources)."""
        self._last = None
        return self.last

    def _push(self, snap: ResourceSnapshot) -> None:
        self.history.append(snap)
        if len(self.history) > self._max_history:
            del self.history[: len(self.history) - self._max_history]

    async def _loop(self) -> None:
        while self._leases > 0:
            snap = await asyncio.to_thread(sample)
            self._last = snap
            self.samples_taken += 1
            self._push(snap)
            await asyncio.sleep(self.interval)

    @contextlib.asynccontextmanager
    async def active(self):  # type: ignore[no-untyped-def]
        self._leases += 1
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._loop(), name="scar-resource-sampler")
        try:
            yield self
        finally:
            self._leases -= 1
            if self._leases <= 0 and self._task is not None:
                self._task.cancel()
                self._task = None

    def acquire(self) -> None:
        self._leases += 1
        if self._task is None or self._task.done():
            self._task = asyncio.get_running_loop().create_task(self._loop(), name="scar-resource-sampler")

    def release(self) -> None:
        self._leases = max(0, self._leases - 1)
        if self._leases == 0 and self._task is not None:
            self._task.cancel()
            self._task = None

    @property
    def sampling(self) -> bool:
        return self._task is not None and not self._task.done()
