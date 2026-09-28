"""`scar doctor`: environment, dependency, provider, device and security checks with remediation hints."""

from __future__ import annotations

import asyncio
import importlib
import shutil
import socket
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from scar.cli.render import check_line, console
from scar.config.settings import Settings


@dataclass
class CheckResult:
    ok: bool | None
    label: str
    hint: str = ""
    section: str = ""


class Doctor:
    def __init__(self, settings: Settings, services: Any, deep: bool = False) -> None:
        self.settings = settings
        self.s = services
        self.deep = deep
        self.results: list[CheckResult] = []

    def add(self, section: str, ok: bool | None, label: str, hint: str = "") -> None:
        self.results.append(CheckResult(ok, label, hint, section))

    async def run(self) -> list[CheckResult]:
        self.platform()
        self.dependencies()
        self.gpu()
        await self.local_models()
        self.audio()
        self.browsers()
        await self.network()
        await self.providers()
        self.database()
        self.windows_automation()
        self.policy()
        self.keyring()
        await self.daemon()
        return self.results

    # ---------------------------------------------------------------- sections
    def platform(self) -> None:
        v = sys.version_info
        self.add("system", v[:2] in ((3, 11), (3, 12)), f"Python {v.major}.{v.minor}.{v.micro}",
                 "" if v[:2] in ((3, 11), (3, 12)) else "SCAR targets Python 3.11/3.12")
        import platform

        win = sys.platform == "win32"
        release = platform.release()
        if win and release == "10" and int([*platform.version().split("."), "0", "0", "0"][2] or 0) >= 22000:
            release = "11"  # Windows 11 still reports release 10; build 22000+ is Windows 11
        self.add("system", win or None, f"{platform.system()} {release} ({platform.version()})",
                 "" if win else "Windows automation features are unavailable on this OS")
        from scar.tools.windows.win32 import self_elevated

        if win:
            elev = self_elevated()
            self.add("system", not elev or None, "running without administrator rights" if not elev else "running elevated",
                     "" if not elev else "SCAR is designed to run non-elevated")

    def dependencies(self) -> None:
        mods = {"pydantic": "core", "httpx": "core", "structlog": "core", "faiss": "memory vectors", "fastembed": "embeddings",
                "playwright": "browser", "trafilatura": "web extraction", "pypdf": "PDF", "docx": "DOCX", "fpdf": "PDF writing",
                "sounddevice": "audio", "faster_whisper": "local STT", "openwakeword": "wake word", "edge_tts": "cloud TTS",
                "uiautomation": "UI Automation", "mss": "screen capture", "pynput": "hotkeys", "watchdog": "folder monitors",
                "winrt.windows.media.ocr": "OCR", "windows_toasts": "notifications", "keyring": "secrets", "ddgs": "search fallback"}
        missing = []
        for mod, purpose in mods.items():
            try:
                importlib.import_module(mod)
            except ImportError:
                missing.append(f"{mod} ({purpose})")
        self.add("system", not missing, "Python dependencies installed" if not missing else f"missing: {', '.join(missing)}",
                 "" if not missing else "run `uv sync`")

    def gpu(self) -> None:
        from scar.resources.monitor import sample

        snap = sample()
        if snap.gpus:
            g = snap.gpus[0]
            self.add("gpu", True, f"{g.name} (compute {g.compute_capability}), VRAM {g.mem_free_mb:.0f}/{g.mem_total_mb:.0f} MiB free, "
                                  f"{g.util_percent:.0f}% busy")
            try:
                import pynvml

                self.add("gpu", True, f"NVML driver {pynvml.nvmlSystemGetDriverVersion()}, CUDA {pynvml.nvmlSystemGetCudaDriverVersion() / 1000:.1f}")
            except Exception as exc:  # noqa: BLE001 - informational only
                self.add("gpu", None, f"driver/CUDA version unavailable: {exc}")
        else:
            self.add("gpu", None, "no NVIDIA GPU via NVML", snap.nvml_error or "local models will run on CPU")

    async def local_models(self) -> None:
        lm = self.s.local_models
        binary = lm.server_bin()
        self.add("local models", bool(binary) or None, f"llama-server: {binary or 'not found'}",
                 "" if binary else "install llama.cpp (winget install ggml.llamacpp) or set SCAR_LOCAL_LLM_SERVER_BIN")
        mp = self.settings.local_llm_model_path
        if mp:
            self.add("local models", Path(mp).exists(), f"GGUF model: {mp}", "" if Path(mp).exists() else "file not found")
        else:
            self.add("local models", None, "SCAR_LOCAL_LLM_MODEL_PATH not set", "only needed for a SCAR-managed llama.cpp server")
        healthy = await lm.healthy(self.settings.local_llm_url)
        self.add("local models", healthy or None, f"llama.cpp server at {self.settings.local_llm_url}: {'healthy' if healthy else 'not running'}",
                 "" if healthy else "SCAR starts one on demand when a model path is configured")
        try:
            async with httpx.AsyncClient(timeout=2.0) as c:
                r = await c.get(self.settings.ollama_url.rstrip("/") + "/api/tags")
            models = [m["name"] for m in r.json().get("models", [])]
            self.add("local models", True, f"Ollama running: {', '.join(models[:6]) or 'no models'}")
        except (httpx.HTTPError, ValueError):
            self.add("local models", None, f"Ollama not reachable at {self.settings.ollama_url}", "optional local fallback")
        self.add("local models", True, f"local inference policy: {self.settings.local_inference_policy}, idle unload after "
                                       f"{self.settings.model_idle_timeout:.0f}s")

    def audio(self) -> None:
        try:
            import sounddevice as sd

            devs = sd.query_devices()
            ins = [d for d in devs if d["max_input_channels"] > 0]
            outs = [d for d in devs if d["max_output_channels"] > 0]
            default_in, default_out = sd.default.device
            self.add("audio", bool(ins), f"microphones: {len(ins)} (default: {devs[default_in]['name'] if default_in is not None and default_in >= 0 else 'none'})",
                     "" if ins else "connect a microphone for voice mode")
            self.add("audio", bool(outs), f"speakers: {len(outs)} (default: {devs[default_out]['name'] if default_out is not None and default_out >= 0 else 'none'})")
        except Exception as exc:  # noqa: BLE001 - PortAudio errors vary
            self.add("audio", False, f"audio unavailable: {exc}", "check audio drivers")

    def browsers(self) -> None:
        from scar.tools.browser.manager import CHROME_PATHS, EDGE_PATHS, detect_channel

        chrome = any(Path(p).exists() for p in CHROME_PATHS)
        edge = any(Path(p).exists() for p in EDGE_PATHS)
        pw_dir = Path.home() / "AppData/Local/ms-playwright"
        bundled = pw_dir.exists() and any(pw_dir.glob("chromium-*"))
        self.add("browser", chrome or edge or bundled, f"browsers: Chrome {'yes' if chrome else 'no'}, Edge {'yes' if edge else 'no'}, "
                 f"Playwright Chromium {'yes' if bundled else 'no'} → using {detect_channel(self.settings.browser_channel)}",
                 "" if (chrome or edge or bundled) else "run `uv run playwright install chromium`")

    async def network(self) -> None:
        try:
            await asyncio.to_thread(socket.create_connection, ("1.1.1.1", 443), 3)
            self.add("network", True, "internet reachable")
        except OSError:
            self.add("network", False, "no internet connection", "cloud providers and web research need network access")

    async def providers(self) -> None:
        router = self.s.router
        for pid, spec in router.catalog.providers.items():
            ok, why = router.credential_status(spec)
            if not ok:
                self.add("providers", None, f"{pid}: not configured ({why})",
                         f"optional — see {spec.setup_doc}")
                continue
            if spec.local:
                continue
            try:
                client = router.client(spec)
                models = await asyncio.wait_for(router.available_models(spec, client), timeout=15)
                cats = [c for c, lst in router.catalog.categories.items() if any(x.provider == pid for x in lst)]
                wanted = [m for c in cats for x in router.catalog.categories[c] if x.provider == pid for m in x.models]
                have = router.resolve_from_list(wanted, models)
                self.add("providers", bool(have), f"{pid}: reachable; models available: {', '.join(dict.fromkeys(have)) or 'none of the configured'}",
                         spec.data_terms)
                if self.deep and have:
                    probe = await router.probe(pid)
                    self.add("providers", probe.get("ok", False), f"{pid}: test completion {'ok' if probe.get('ok') else probe.get('detail')}")
            except Exception as exc:  # noqa: BLE001 - report any provider failure
                h = self.s.health.get(pid, "")
                self.add("providers", False, f"{pid}: {str(exc)[:120]}", h.last_error[:80] if h.last_error else spec.setup_doc)
        for h in self.s.health.all():
            import time

            if h.cooldown_until > time.time():
                self.add("providers", None, f"{h.provider}/{h.model} cooling down ({int(h.cooldown_until - time.time())}s): {h.last_error[:80]}")
        # search / tts / stt summaries
        search_ok = [p for p in ("brave", "tavily", "searxng", "ddgs") if self.s.search.configured(p)[0]]
        self.add("providers", bool(search_ok) or None, f"search providers: {', '.join(search_ok) or 'none'}",
                 "" if search_ok else "set BRAVE_API_KEY or TAVILY_API_KEY, or keep ddgs installed")
        stt = ["groq whisper"] if self.s.secrets.has("GROQ_API_KEY") else []
        stt.append("faster-whisper (local)")
        self.add("providers", True, f"speech-to-text chain: {' → '.join(stt)}")
        tts = (["azure"] if self.s.secrets.has("AZURE_SPEECH_KEY") else []) + ["edge-tts (unofficial)", "Windows SAPI (local)"]
        self.add("providers", True, f"text-to-speech chain: {' → '.join(tts)} → text")
        reasoning_cloud = [pid for pid, spec in router.catalog.providers.items() if not spec.local and router.credential_status(spec)[0]
                           and any(c.provider == pid for c in router.catalog.categories.get("reasoning", []))]
        if not reasoning_cloud:
            self.add("providers", None, "no cloud reasoning provider configured",
                     "set GROQ_API_KEY or GEMINI_API_KEY (free) — see docs/providers.md; local Ollama/llama.cpp is used as fallback")
        if self.settings.privacy_mode != "strict":
            gem = router.catalog.providers.get("gemini")
            if gem and router.credential_status(gem)[0]:
                self.add("providers", None, "Gemini free tier may use your prompts to improve Google products",
                         "set SCAR_PRIVACY_MODE=strict or SCAR_PRIVACY_OVERRIDES=screen=local_only,... for sensitive data")

    def database(self) -> None:
        try:
            integrity = self.s.db.integrity_check()
            self.add("storage", integrity == "ok", f"database integrity: {integrity} (schema v{self.s.db.schema_version()})",
                     "" if integrity == "ok" else "SCAR will back up and recreate a corrupt database on next start")
        except Exception as exc:  # noqa: BLE001
            self.add("storage", False, f"database error: {exc}")
        ok, rows = self.s.audit.verify()
        self.add("storage", ok, f"audit log hash chain intact ({rows} entries)" if ok else f"audit log tampered at entry {rows}")
        self.add("storage", True, f"data folder: {self.settings.data_path}")

    def windows_automation(self) -> None:
        if sys.platform != "win32":
            self.add("windows", None, "Windows automation unavailable on this OS")
            return
        from scar.tools.windows.win32 import dpi_awareness, list_monitors, set_dpi_awareness

        set_dpi_awareness()
        self.add("windows", True, f"DPI awareness: {dpi_awareness()}; monitors: " +
                 ", ".join(f"{m.right - m.left}x{m.bottom - m.top}@{m.scale:.0%}" for m in list_monitors()))
        try:
            import uiautomation as ua

            with ua.UIAutomationInitializerInThread():
                root = ua.GetRootControl()
                self.add("windows", root is not None, "UI Automation (COM) working")
        except Exception as exc:  # noqa: BLE001
            self.add("windows", False, f"UI Automation failed: {exc}")
        from scar.tools.screen.ocr import OcrEngine

        ok, why = OcrEngine().available()
        self.add("windows", ok, "Windows OCR available" if ok else why, "" if ok else "install an OCR language pack (Settings → Language)")
        ks = self.s.killswitch
        self.add("windows", True, f"kill switch hotkey: {ks.hotkey}")

    def policy(self) -> None:
        problems = self.s.policy.validate()
        disabled = [k for k, v in self.s.policy.hard_deny.items() if not v]
        self.add("security", not problems, f"permission policy valid ({len(self.s.policy.rules)} rules)" if not problems else "; ".join(problems))
        if disabled:
            self.add("security", None, f"hard-deny protections disabled by hand: {', '.join(disabled)}")
        self.add("security", True, f"autonomy level {self.settings.autonomy_level}; allowed roots: "
                                   f"{', '.join(str(r) for r in self.settings.effective_allowed_roots)}")

    def keyring(self) -> None:
        try:
            import keyring

            backend = keyring.get_keyring()
            kind = f"{type(backend).__module__}.{type(backend).__name__}"
            ok = "Windows" in kind or "WinVault" in kind  # keyring.backends.Windows.WinVaultKeyring = Credential Manager
            self.add("security", ok or None, f"secret store: {type(backend).__name__}",
                     "" if ok else "Windows Credential Manager backend expected")
        except Exception as exc:  # noqa: BLE001
            self.add("security", False, f"keyring unavailable: {exc}")

    async def daemon(self) -> None:
        from scar.runtime.daemon import logon_task_installed
        from scar.runtime.ipc import daemon_alive

        alive = await daemon_alive(self.settings.data_path)
        self.add("daemon", True if alive else None, "daemon running" if alive else "daemon not running",
                 "" if alive else "`scar daemon start` for reminders/monitors while no terminal is open")
        if sys.platform == "win32" and shutil.which("schtasks"):
            self.add("daemon", True, f"start at logon: {'installed' if logon_task_installed() else 'not installed'}")


def print_results(results: list[CheckResult]) -> None:
    section = None
    for r in results:
        if r.section != section:
            section = r.section
            console.print(f"\n[bold]{section.title()}[/bold]")
        check_line(r.ok, r.label, r.hint)
