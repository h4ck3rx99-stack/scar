"""Local model lifecycle manager (C6).

* llama.cpp ``llama-server``: if a healthy server already answers at
  ``SCAR_LOCAL_LLM_URL`` it is reused and never killed (it may be the user's).
  Otherwise SCAR starts one on demand with the configured GGUF, sizing ``-ngl``
  from free VRAM (NVML) and ``SCAR_MAX_LOCAL_VRAM``, bounding the context,
  health-checking ``/health`` and stopping it after ``SCAR_MODEL_IDLE_TIMEOUT``.
* Ollama (user-managed server): SCAR never starts or stops the server, but
  unloads the models *it* used after the idle timeout (``keep_alive: 0``).
* Lazy components (STT, TTS, VLM, embeddings) register an unload callback and
  are released after the same idle timeout.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import shutil
import socket
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

import httpx
import structlog

from scar.config.settings import Settings
from scar.core.errors import CapabilityUnavailable
from scar.providers.errors import ProviderError, ProviderErrorKind
from scar.resources.admission import AdmissionController
from scar.runtime.jobs import ManagedProcess, ProcessManager

log = structlog.get_logger("scar.local_models")


@dataclass
class LazyComponent:
    name: str
    unload: Callable[[], None]
    last_used: float = field(default_factory=time.monotonic)
    loaded: bool = True
    size_mb: float = 0.0


@dataclass
class ServerState:
    url: str
    owned: bool
    model_path: str = ""
    mmproj: str = ""
    process: ManagedProcess | None = None
    popen: subprocess.Popen[bytes] | None = None
    last_used: float = field(default_factory=time.monotonic)
    gpu_layers: int = 0
    started_at: float = field(default_factory=time.time)


def _port_free(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.3)
        return s.connect_ex((host, port)) != 0


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


class LocalModelManager:
    def __init__(self, settings: Settings, admission: AdmissionController, processes: ProcessManager) -> None:
        self.settings = settings
        self.admission = admission
        self.processes = processes
        self._servers: dict[str, ServerState] = {}  # "llm" | "vlm"
        self._components: dict[str, LazyComponent] = {}
        self._ollama_used: dict[str, float] = {}
        self._lock = asyncio.Lock()
        self._reaper: asyncio.Task[None] | None = None
        self.events: list[str] = []  # short lifecycle log for status/tests

    # ------------------------------------------------------------------ helpers
    def server_bin(self) -> str | None:
        if self.settings.local_llm_server_bin:
            p = Path(self.settings.local_llm_server_bin)
            return str(p) if p.exists() else None
        return shutil.which("llama-server")

    @staticmethod
    async def healthy(url: str, timeout: float = 2.0) -> bool:
        base = url.rstrip("/").removesuffix("/v1")
        try:
            async with httpx.AsyncClient(timeout=timeout) as c:
                r = await c.get(base + "/health")
                if r.status_code == 200:
                    return True
                r = await c.get(base + "/v1/models")
                return r.status_code == 200
        except httpx.HTTPError:
            return False

    def _log(self, msg: str) -> None:
        self.events.append(f"{time.strftime('%H:%M:%S')} {msg}")
        del self.events[:-50]
        log.info("local_model", detail=msg)

    # ------------------------------------------------------------------ llama.cpp
    async def ensure_llamacpp(self, *, vlm: bool = False, fallback: bool = True) -> str:
        """Return a base URL (…/v1) for a healthy llama.cpp server, starting one if needed."""
        key = "vlm" if vlm else "llm"
        async with self._lock:
            st = self._servers.get(key)
            if st is not None:
                if st.owned and st.process is not None and not st.process.alive():
                    self._log(f"{key} server exited; will restart")
                    self._servers.pop(key, None)
                elif await self.healthy(st.url):
                    st.last_used = time.monotonic()
                    return st.url.rstrip("/") + "/v1"
                else:
                    self._servers.pop(key, None)
            configured = self.settings.local_llm_url.rstrip("/")
            if not vlm and await self.healthy(configured):
                self._servers[key] = ServerState(url=configured, owned=False)
                self._log(f"reusing existing llama.cpp server at {configured} (not owned)")
                self._start_reaper()
                return configured + "/v1"
            model_path = self.settings.local_vlm_model_path if vlm else self.settings.local_llm_model_path
            if not model_path:
                raise ProviderError(ProviderErrorKind.UNAVAILABLE,
                                    f"no llama.cpp server at {configured} and SCAR_LOCAL_{'VLM' if vlm else 'LLM'}_MODEL_PATH is not set",
                                    provider="llamacpp")
            if not Path(model_path).exists():
                raise ProviderError(ProviderErrorKind.UNAVAILABLE, f"model file not found: {model_path}", provider="llamacpp")
            binary = self.server_bin()
            if binary is None:
                raise ProviderError(ProviderErrorKind.UNAVAILABLE, "llama-server binary not found (set SCAR_LOCAL_LLM_SERVER_BIN)",
                                    provider="llamacpp")
            size_mb = Path(model_path).stat().st_size / 2**20
            mmproj = self.settings.local_vlm_mmproj_path if vlm else ""
            if mmproj and Path(mmproj).exists():
                size_mb += Path(mmproj).stat().st_size / 2**20
            decision = self.admission.local_inference(size_mb, purpose=key, fallback=fallback)
            if not decision.admitted:
                raise ProviderError(ProviderErrorKind.RESOURCE, decision.reason, provider="llamacpp")
            parsed = urlparse(configured)
            host = parsed.hostname or "127.0.0.1"
            port = parsed.port or 8080
            if vlm or not _port_free(host, port):
                port = _free_port()
            url = f"http://127.0.0.1:{port}"
            argv = [binary, "-m", model_path, "--host", "127.0.0.1", "--port", str(port),
                    "-c", str(self.settings.local_llm_ctx), "-ngl", str(decision.gpu_layers if decision.use_gpu else 0),
                    "--jinja", "--no-webui", "--parallel", "1", "-fa", "on", "-ctk", "q8_0", "-ctv", "q8_0"]
            if mmproj:
                argv += ["--mmproj", mmproj]
            log_path = self.settings.log_path / f"llama-server-{key}.log"
            log_path.parent.mkdir(parents=True, exist_ok=True)
            log_fh = log_path.open("ab")
            popen, mp = self.processes.popen(argv, name=f"llama-server-{key}", kind="model-server", stdout=log_fh,
                                             stderr=subprocess.STDOUT)
            log_fh.close()
            self._log(f"started llama-server ({key}) pid {mp.pid} on port {port} with -ngl "
                      f"{decision.gpu_layers if decision.use_gpu else 0} ({decision.reason})")
            st = ServerState(url=url, owned=True, model_path=model_path, mmproj=mmproj, process=mp, popen=popen,
                             gpu_layers=decision.gpu_layers)
            deadline = time.monotonic() + 240
            while time.monotonic() < deadline:
                if popen.poll() is not None:
                    self.processes.forget(mp.pid)
                    tail = _tail(log_path)
                    raise ProviderError(ProviderErrorKind.UNAVAILABLE, f"llama-server exited ({popen.returncode}): {tail}",
                                        provider="llamacpp")
                if await self.healthy(url, timeout=2.0):
                    break
                await asyncio.sleep(1.0)
            else:
                self.processes.kill(mp.pid)
                raise ProviderError(ProviderErrorKind.TRANSIENT, "llama-server did not become healthy in time", provider="llamacpp")
            self._servers[key] = st
            self._start_reaper()
            return url + "/v1"

    async def stop_server(self, key: str, reason: str = "requested") -> bool:
        st = self._servers.get(key)
        if st is None:
            return False
        if not st.owned:
            self._servers.pop(key, None)
            self._log(f"released reference to external server {st.url} (not stopped)")
            return False
        if st.process is not None:
            self.processes.kill(st.process.pid)
        if st.popen is not None:
            with contextlib.suppress(subprocess.TimeoutExpired):
                await asyncio.to_thread(st.popen.wait, 10)
        self._servers.pop(key, None)
        self._log(f"stopped llama-server ({key}): {reason}")
        return True

    def touch(self, key: str) -> None:
        st = self._servers.get(key)
        if st is not None:
            st.last_used = time.monotonic()

    # ------------------------------------------------------------------ ollama
    def ollama_used(self, model: str) -> None:
        self._ollama_used[model] = time.monotonic()
        self._start_reaper()

    async def ollama_unload(self, model: str) -> bool:
        base = self.settings.ollama_url.rstrip("/")
        try:
            async with httpx.AsyncClient(timeout=15.0) as c:
                r = await c.post(base + "/api/generate", json={"model": model, "keep_alive": 0})
            ok = r.status_code == 200
        except httpx.HTTPError:
            ok = False
        self._ollama_used.pop(model, None)
        self._log(f"unloaded ollama model {model}: {'ok' if ok else 'failed'}")
        return ok

    async def ollama_loaded(self) -> list[dict[str, object]]:
        base = self.settings.ollama_url.rstrip("/")
        try:
            async with httpx.AsyncClient(timeout=3.0) as c:
                r = await c.get(base + "/api/ps")
            return list(r.json().get("models", [])) if r.status_code == 200 else []
        except (httpx.HTTPError, ValueError):
            return []

    # ------------------------------------------------------------------ lazy components
    def register_component(self, name: str, unload: Callable[[], None], size_mb: float = 0.0) -> None:
        self._components[name] = LazyComponent(name=name, unload=unload, size_mb=size_mb)
        self._log(f"loaded {name}")
        self._start_reaper()

    def component_used(self, name: str) -> None:
        c = self._components.get(name)
        if c is not None:
            c.last_used = time.monotonic()

    def unload_component(self, name: str) -> None:
        c = self._components.pop(name, None)
        if c is not None:
            try:
                c.unload()
            finally:
                self._log(f"unloaded {name}")

    # ------------------------------------------------------------------ idle reaper
    def _start_reaper(self) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        if self._reaper is None or self._reaper.done():
            self._reaper = loop.create_task(self._reap_loop(), name="scar-model-reaper")

    async def _reap_loop(self) -> None:
        interval = max(5.0, min(60.0, self.settings.model_idle_timeout / 4))
        while self._servers or self._components or self._ollama_used:
            await asyncio.sleep(interval)
            await self.reap_idle()

    async def reap_idle(self) -> list[str]:
        timeout = self.settings.model_idle_timeout
        now = time.monotonic()
        stopped: list[str] = []
        for key, st in list(self._servers.items()):
            if now - st.last_used >= timeout:
                await self.stop_server(key, f"idle for {int(now - st.last_used)}s")
                stopped.append(key)
        for model, used in list(self._ollama_used.items()):
            if now - used >= timeout:
                await self.ollama_unload(model)
                stopped.append(f"ollama:{model}")
        for name, comp in list(self._components.items()):
            if now - comp.last_used >= timeout:
                self.unload_component(name)
                stopped.append(name)
        return stopped

    async def shutdown(self) -> None:
        for key in list(self._servers):
            await self.stop_server(key, "shutdown")
        for model in list(self._ollama_used):
            await self.ollama_unload(model)
        for name in list(self._components):
            self.unload_component(name)
        if self._reaper is not None:
            self._reaper.cancel()

    def status(self) -> list[dict[str, object]]:
        out: list[dict[str, object]] = []
        now = time.monotonic()
        for key, st in self._servers.items():
            out.append({"name": f"llama.cpp ({key})", "url": st.url, "owned": st.owned,
                        "pid": st.process.pid if st.process else None, "gpu_layers": st.gpu_layers,
                        "idle_s": int(now - st.last_used)})
        for model, used in self._ollama_used.items():
            out.append({"name": f"ollama {model}", "owned": False, "idle_s": int(now - used)})
        for c in self._components.values():
            out.append({"name": c.name, "owned": True, "idle_s": int(now - c.last_used)})
        return out


def _tail(path: Path, n: int = 400) -> str:
    try:
        data = path.read_bytes()[-n:]
        return data.decode("utf-8", errors="replace").strip().replace("\n", " | ")
    except OSError:
        return ""


def require_file(path: str, what: str, doc: str) -> Path:
    p = Path(os.path.expandvars(path)).expanduser()
    if not path or not p.exists():
        raise CapabilityUnavailable(f"{what} not found ({path or 'not configured'})", doc)
    return p
