"""Admission control (C7) for local inference, vision, OCR, background work and sub-agents."""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections import deque
from collections.abc import AsyncIterator
from dataclasses import dataclass

from scar.config.settings import Settings
from scar.core.errors import ResourceDenied
from scar.resources.limits import Limits, limits_for
from scar.resources.monitor import ResourceMonitor, ResourceSnapshot


@dataclass
class AdmissionDecision:
    admitted: bool
    reason: str
    use_gpu: bool = False
    gpu_layers: int = 0


class _RateWindow:
    def __init__(self, per_minute: int) -> None:
        self.per_minute = per_minute
        self._events: deque[float] = deque(maxlen=max(1, per_minute))

    def allow(self) -> bool:
        now = time.monotonic()
        while self._events and now - self._events[0] > 60:
            self._events.popleft()
        if len(self._events) >= self.per_minute:
            return False
        self._events.append(now)
        return True


class AdmissionController:
    def __init__(self, settings: Settings, monitor: ResourceMonitor) -> None:
        self.settings = settings
        self.monitor = monitor
        self.limits: Limits = limits_for(settings.background_task_policy)
        self.subprocesses = asyncio.Semaphore(self.limits.max_subprocesses)
        self.browser_contexts = asyncio.Semaphore(self.limits.max_browser_contexts)
        self.agents = asyncio.Semaphore(self.limits.max_agents)
        self.model_calls = asyncio.Semaphore(self.limits.max_model_calls)
        self.background = asyncio.Semaphore(self.limits.max_background_tasks)
        self._ocr = _RateWindow(self.limits.max_ocr_per_minute)
        self._vision = _RateWindow(self.limits.max_vision_per_minute)

    # ---------------------------------------------------------------- local inference
    def local_inference(self, model_size_mb: float, *, purpose: str = "llm", fallback: bool = True) -> AdmissionDecision:
        """Decide whether (and how) a local model may be started right now."""
        policy = self.settings.local_inference_policy
        if policy == "never":
            return AdmissionDecision(False, "local inference disabled by SCAR_LOCAL_INFERENCE_POLICY=never")
        if policy == "fallback_only" and not fallback:
            return AdmissionDecision(False, "local inference is fallback-only and a cloud provider is available")
        snap = self.monitor.last
        return self._decide(snap, model_size_mb, purpose)

    def _decide(self, snap: ResourceSnapshot, model_size_mb: float, purpose: str) -> AdmissionDecision:
        s = self.settings
        on_battery = snap.on_ac is False
        low_battery = on_battery and snap.battery_percent is not None and snap.battery_percent < s.battery_saver_threshold
        if snap.ram_available_mb < 1024:
            return AdmissionDecision(False, f"only {snap.ram_available_mb:.0f} MiB RAM available")
        gpu = snap.gpu
        gpu_ok = gpu is not None and not low_battery
        gpu_reason = ""
        if gpu is None:
            gpu_reason = "no NVIDIA GPU detected"
        elif low_battery:
            gpu_reason = f"on battery below {s.battery_saver_threshold}%"
        elif gpu.util_percent > s.gpu_usage_threshold:
            gpu_ok = False
            gpu_reason = f"GPU busy ({gpu.util_percent:.0f}% > {s.gpu_usage_threshold}%)"
        if gpu_ok and gpu is not None:
            budget = min(float(s.max_local_vram), gpu.mem_free_mb - 512.0)
            if budget < 1024:
                gpu_ok = False
                gpu_reason = f"only {gpu.mem_free_mb:.0f} MiB VRAM free"
            else:
                layers = gpu_layers_for(model_size_mb, budget)
                if layers > 0:
                    return AdmissionDecision(True, f"GPU offload {layers} layers within {budget:.0f} MiB", True, layers)
                gpu_reason = "model does not fit the VRAM budget"
        # CPU path: small models only, and only when RAM allows
        ram_budget = min(float(s.max_local_ram), snap.ram_available_mb - 2048.0)
        if model_size_mb * 1.2 > ram_budget:
            return AdmissionDecision(False, f"not enough RAM for a {model_size_mb:.0f} MiB model on CPU ({gpu_reason})")
        if snap.cpu_percent > 85:
            return AdmissionDecision(False, f"CPU busy ({snap.cpu_percent:.0f}%)")
        return AdmissionDecision(True, f"CPU only ({gpu_reason})", False, 0)

    # ---------------------------------------------------------------- bursts
    def admit_ocr(self) -> None:
        if not self._ocr.allow():
            raise ResourceDenied("OCR rate limit reached for this minute")

    def admit_vision(self) -> None:
        if not self._vision.allow():
            raise ResourceDenied("vision rate limit reached for this minute")

    def admit_background(self) -> AdmissionDecision:
        snap = self.monitor.last
        if snap.cpu_percent > 90:
            return AdmissionDecision(False, f"CPU busy ({snap.cpu_percent:.0f}%)")
        if snap.ram_available_mb < 512:
            return AdmissionDecision(False, "RAM nearly exhausted")
        return AdmissionDecision(True, "ok")

    @contextlib.asynccontextmanager
    async def slot(self, sem: asyncio.Semaphore, timeout: float = 120.0) -> AsyncIterator[None]:
        try:
            await asyncio.wait_for(sem.acquire(), timeout=timeout)
        except TimeoutError as exc:
            raise ResourceDenied("timed out waiting for a free slot") from exc
        try:
            yield
        finally:
            sem.release()


def gpu_layers_for(model_size_mb: float, vram_budget_mb: float, total_layers: int = 99, overhead_mb: float = 900.0) -> int:
    """Estimate ``-ngl`` so weights + KV/compute overhead fit the VRAM budget."""
    if vram_budget_mb <= overhead_mb or model_size_mb <= 0:
        return 0
    usable = vram_budget_mb - overhead_mb
    if usable >= model_size_mb:
        return total_layers
    # assume ~36 transformer layers for 7-9B class models; scale proportionally
    approx_layers = 36
    frac = usable / model_size_mb
    return max(0, int(approx_layers * frac))
