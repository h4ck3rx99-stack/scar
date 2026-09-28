"""Build the Windows installer (NSIS) for the SCAR desktop app.

    uv run python scripts/build_installer.py

Steps: build SCAR's wheel, export the hash-locked runtime requirements, bundle uv (to create SCAR's private Python
environment on first run), regenerate third-party notices, then `tauri build`. Output:
app/src-tauri/target/release/bundle/nsis/SCAR_<version>_x64-setup.exe
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BUNDLE = ROOT / "app" / "src-tauri" / "runtime"


def run(cmd: list[str] | str, cwd: Path = ROOT, **kw: object) -> None:
    print("›", cmd if isinstance(cmd, str) else " ".join(cmd), flush=True)
    subprocess.run(cmd, cwd=cwd, check=True, shell=isinstance(cmd, str), **kw)  # type: ignore[call-overload]


def main() -> None:
    shutil.rmtree(BUNDLE, ignore_errors=True)
    BUNDLE.mkdir(parents=True)
    run(["uv", "build", "--wheel", "--out-dir", str(BUNDLE)])
    req = subprocess.run(["uv", "export", "--frozen", "--no-dev", "--no-emit-project", "--format", "requirements-txt"],
                         cwd=ROOT, check=True, capture_output=True, text=True, encoding="utf-8").stdout
    (BUNDLE / "requirements.txt").write_text(req, encoding="utf-8")
    uv = shutil.which("uv")
    if not uv:
        sys.exit("uv not found on PATH")
    shutil.copy2(uv, BUNDLE / "uv.exe")
    run([sys.executable, str(ROOT / "scripts" / "gen_notices.py")])
    run("pnpm tauri build", cwd=ROOT / "app")
    out = sorted((ROOT / "app" / "src-tauri" / "target" / "release" / "bundle" / "nsis").glob("*.exe"))
    print("installer:", out[-1] if out else "not found")


if __name__ == "__main__":
    main()
