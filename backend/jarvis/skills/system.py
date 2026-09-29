"""PC status and controls: battery, CPU/RAM/GPU, disk, uptime, volume, brightness, lock, screenshot."""
import ctypes
import datetime
import subprocess
import time
from pathlib import Path

import psutil


def _gpu():
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=utilization.gpu,memory.used,memory.total,temperature.gpu", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=2, creationflags=subprocess.CREATE_NO_WINDOW).stdout.strip()
        util, used, total, temp = [x.strip() for x in out.split(",")]
        return {"gpu_percent": int(util), "vram_used_gb": round(int(used) / 1024, 1), "vram_total_gb": round(int(total) / 1024, 1), "gpu_temp_c": int(temp)}
    except Exception:  # noqa: BLE001 - no NVIDIA GPU / driver
        return {}


def _plural(n, word):
    return f"{n} {word}{'s' * (n != 1)}"


def status() -> dict:
    bat = psutil.sensors_battery()
    vm = psutil.virtual_memory()
    disk = psutil.disk_usage(str(Path.home().anchor))
    boot = datetime.datetime.fromtimestamp(psutil.boot_time())
    up = datetime.datetime.now() - boot
    info = {
        "cpu_percent": psutil.cpu_percent(interval=0.3),
        "ram_used_percent": vm.percent,
        "ram_free_gb": round(vm.available / 2**30, 1),
        "disk_free_gb": round(disk.free / 2**30),
        "disk_used_percent": disk.percent,
        "uptime": _plural(up.days, "day") + " " + _plural(up.seconds // 3600, "hour") if up.days
        else _plural(up.seconds // 3600, "hour") + " " + _plural(up.seconds % 3600 // 60, "minute"),
        **_gpu(),
    }
    if bat:
        info["battery_percent"] = round(bat.percent)
        info["charging"] = bool(bat.power_plugged)
        if not bat.power_plugged and bat.secsleft not in (psutil.POWER_TIME_UNKNOWN, psutil.POWER_TIME_UNLIMITED):
            info["battery_time_left"] = f"{bat.secsleft // 3600} h {bat.secsleft % 3600 // 60} min"
    return info


def top_processes(n=5):
    procs = []
    for p in psutil.process_iter(["name", "memory_info"]):
        try:
            procs.append((p.info["memory_info"].rss, p.info["name"]))
        except (psutil.NoSuchProcess, psutil.AccessDenied, TypeError):
            pass
    agg = {}
    for rss, name in procs:
        agg[name] = agg.get(name, 0) + rss
    return [f"{name} {rss / 2**30:.1f} GB" for name, rss in sorted(agg.items(), key=lambda x: -x[1])[:n]]


def battery():
    bat = psutil.sensors_battery()
    if not bat:
        return None
    return round(bat.percent), bool(bat.power_plugged)


# ------------------------------------------------------------------ volume
def _endpoint():
    from pycaw.pycaw import AudioUtilities
    return AudioUtilities.GetSpeakers().EndpointVolume


def get_volume() -> int:
    return round(_endpoint().GetMasterVolumeLevelScalar() * 100)


def set_volume(percent: int) -> int:
    percent = max(0, min(100, int(percent)))
    ep = _endpoint()
    ep.SetMute(0, None)
    ep.SetMasterVolumeLevelScalar(percent / 100, None)
    return percent


def change_volume(delta: int) -> int:
    return set_volume(get_volume() + delta)


def set_mute(mute: bool):
    _endpoint().SetMute(1 if mute else 0, None)


# -------------------------------------------------------------- brightness
def get_brightness():
    import screen_brightness_control as sbc
    for method in ("wmi", None):          # laptop panel (WMI) first; external monitors (DDC/CI) are slow and flaky
        try:
            vals = sbc.get_brightness(method=method) if method else sbc.get_brightness()
            if vals:
                return vals[0]
        except Exception:  # noqa: BLE001 - not every screen supports it
            continue
    return None


def set_brightness(percent: int):
    """Set every screen that supports it. Returns the level, or None if no screen could be changed."""
    import screen_brightness_control as sbc
    percent = max(0, min(100, int(percent)))
    try:
        sbc.set_brightness(percent, method="wmi")      # the laptop's own panel: instant and reliable
        return percent
    except Exception:  # noqa: BLE001 - desktop PC / no WMI panel: try monitors one by one
        pass
    ok = False
    try:
        displays = sbc.list_monitors()
    except Exception:  # noqa: BLE001
        displays = []
    for d in displays:
        try:
            sbc.set_brightness(percent, display=d, max_tries=2)
            ok = True
        except Exception:  # noqa: BLE001 - e.g. a virtual display or a monitor without DDC/CI
            continue
    return percent if ok else None


def change_brightness(delta: int):
    cur = get_brightness()
    return None if cur is None else set_brightness(cur + delta)


# ------------------------------------------------------------------- other
def lock():
    ctypes.windll.user32.LockWorkStation()


def screen_off():
    # SC_MONITORPOWER = 2 (off), broadcast to all windows.
    ctypes.windll.user32.PostMessageW(0xFFFF, 0x0112, 0xF170, 2)


def screenshot() -> Path:
    from PIL import ImageGrab
    folder = Path.home() / "Pictures" / "Jarvis"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"screenshot_{time.strftime('%Y-%m-%d_%H-%M-%S')}.png"
    ImageGrab.grab(all_screens=True).save(path)
    return path
