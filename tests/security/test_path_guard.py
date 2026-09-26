"""Filesystem guard against path tricks (C4.7, D2): junctions, symlinks, UNC, ADS, case, 8.3, \\\\?\\ prefixes."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from scar.core.types import PolicyDecision, RiskLevel
from scar.security.path_guard import PathCategory, PathGuard, PathOp, canonicalize, split_stream


@pytest.fixture
def guard(tmp_path: Path) -> PathGuard:
    (tmp_path / "proj").mkdir()
    return PathGuard([tmp_path / "proj"], protected_paths=[str(tmp_path / "proj" / "vault")])


def test_inside_allowed_root(guard: PathGuard, tmp_path: Path) -> None:
    c = guard.check(tmp_path / "proj" / "a.txt", PathOp.WRITE)
    assert c.category == PathCategory.ALLOWED and c.risk == RiskLevel.MEDIUM
    assert guard.check(tmp_path / "proj" / "a.txt", PathOp.READ).risk == RiskLevel.LOW


def test_case_insensitive(guard: PathGuard, tmp_path: Path) -> None:
    upper = str(tmp_path / "PROJ" / "A.TXT").upper()
    assert guard.check(upper, PathOp.READ).category == PathCategory.ALLOWED


def test_dotdot_escape(guard: PathGuard, tmp_path: Path) -> None:
    c = guard.check(str(tmp_path / "proj") + os.sep + ".." + os.sep + "evil.txt", PathOp.WRITE)
    assert c.category == PathCategory.OUTSIDE and c.risk >= RiskLevel.HIGH


def test_long_path_prefix(guard: PathGuard, tmp_path: Path) -> None:
    c = guard.check("\\\\?\\" + str(tmp_path / "proj" / "a.txt"), PathOp.READ)
    assert c.category == PathCategory.ALLOWED
    c = guard.check("\\\\?\\C:\\Windows\\System32\\x.dll", PathOp.WRITE)
    assert c.category == PathCategory.SYSTEM and c.risk == RiskLevel.CRITICAL


@pytest.mark.windows
def test_junction_into_windows(guard: PathGuard, tmp_path: Path) -> None:
    link = tmp_path / "proj" / "win"
    r = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), os.environ["WINDIR"]], capture_output=True)
    assert r.returncode == 0
    try:
        c = guard.check(link / "System32" / "drivers" / "x.sys", PathOp.WRITE)
        assert c.category == PathCategory.SYSTEM and c.risk == RiskLevel.CRITICAL
    finally:
        os.rmdir(link)


def test_symlink_to_secret(guard: PathGuard, tmp_path: Path) -> None:
    ssh = Path.home() / ".ssh"
    link = tmp_path / "proj" / "keys"
    try:
        link.symlink_to(ssh, target_is_directory=True)
    except OSError:
        pytest.skip("creating symlinks needs Developer Mode or admin rights on Windows")
    c = guard.check(link / "id_ed25519", PathOp.READ)
    assert c.category == PathCategory.SECRET and c.denied


@pytest.mark.windows
def test_short_name_expansion(tmp_path: Path) -> None:
    long_dir = tmp_path / "averylongdirectoryname"
    long_dir.mkdir()
    import ctypes

    buf = ctypes.create_unicode_buffer(1024)
    if not ctypes.windll.kernel32.GetShortPathNameW(str(long_dir), buf, 1024) or "~" not in buf.value:
        pytest.skip("8.3 names disabled on this volume")
    g = PathGuard([long_dir])
    assert g.check(buf.value + "\\f.txt", PathOp.WRITE).category == PathCategory.ALLOWED
    g2 = PathGuard([tmp_path / "other"], protected_paths=[str(long_dir)])
    assert g2.check(buf.value + "\\f.txt", PathOp.WRITE).category == PathCategory.PROTECTED


def test_alternate_data_stream(guard: PathGuard, tmp_path: Path) -> None:
    assert split_stream("C:\\x\\file.txt:hidden:$DATA") == ("C:\\x\\file.txt", "hidden:$DATA")
    c = guard.check(str(tmp_path / "proj" / "a.txt") + ":payload", PathOp.WRITE)
    assert c.stream == "payload" and c.risk >= RiskLevel.HIGH
    # stream on a secret file is still the secret file
    c = guard.check(str(Path.home() / ".ssh" / "id_rsa") + ":x", PathOp.READ)
    assert c.denied


def test_unc_and_admin_shares(guard: PathGuard) -> None:
    assert guard.check("\\\\fileserver\\share\\doc.txt", PathOp.READ).category == PathCategory.NETWORK
    assert guard.check("\\\\fileserver\\share\\doc.txt", PathOp.WRITE).risk == RiskLevel.HIGH
    c = guard.check("\\\\localhost\\c$\\Windows\\notepad.exe", PathOp.WRITE)
    assert c.category == PathCategory.SYSTEM and c.risk == RiskLevel.CRITICAL
    c = guard.check("\\\\?\\UNC\\127.0.0.1\\c$\\Users", PathOp.READ)
    assert c.category == PathCategory.SYSTEM


def test_device_paths_denied(guard: PathGuard) -> None:
    for p in ("\\\\.\\PhysicalDrive0", "NUL", "COM1.txt", "\\\\.\\C:"):
        assert guard.check(p, PathOp.WRITE).denied, p


def test_secret_files_and_stores(guard: PathGuard, tmp_path: Path) -> None:
    assert guard.check(tmp_path / "proj" / ".env", PathOp.READ).denied
    assert not guard.check(tmp_path / "proj" / ".env.example", PathOp.READ).denied
    assert guard.check(tmp_path / "proj" / "server.pem", PathOp.READ).denied
    local = os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local"))
    assert guard.check(Path(local) / "Google" / "Chrome" / "User Data" / "Default" / "Login Data", PathOp.READ).denied
    assert guard.check("~/.aws/credentials", PathOp.READ).denied


def test_protected_and_permanent_delete(guard: PathGuard, tmp_path: Path) -> None:
    c = guard.check(tmp_path / "proj" / "vault" / "x", PathOp.WRITE)
    assert c.category == PathCategory.PROTECTED and c.risk == RiskLevel.CRITICAL
    assert guard.check(tmp_path / "proj" / "a", PathOp.DELETE_PERMANENT).risk == RiskLevel.CRITICAL
    assert guard.check(tmp_path / "proj" / "a", PathOp.DELETE).risk == RiskLevel.HIGH


def test_nul_byte_rejected(guard: PathGuard) -> None:
    c = guard.check("C:\\x\x00.txt", PathOp.READ)
    assert c.decision_floor == PolicyDecision.DENY


@settings(max_examples=150, deadline=None)
@given(st.lists(st.text(alphabet="abcXYZ012_-. ", min_size=1, max_size=8), min_size=1, max_size=4),
       st.sampled_from([PathOp.READ, PathOp.WRITE, PathOp.DELETE]))
def test_property_anything_under_ssh_is_denied(parts: list[str], op: PathOp) -> None:
    g = PathGuard([Path.home()])
    parts = [p.strip(" .") or "x" for p in parts]
    p = Path.home().joinpath(".ssh", *parts)
    assert g.check(str(p), op).denied
    # case and separator variants
    assert g.check(str(p).upper().replace("\\", "/"), op).denied


@settings(max_examples=150, deadline=None)
@given(st.lists(st.text(alphabet="abc012_-", min_size=1, max_size=6), min_size=0, max_size=4))
def test_property_dotdot_never_escapes_to_allowed(parts: list[str]) -> None:
    root = Path(os.environ.get("TEMP", "C:\\Temp")) / "scar_prop_root"
    g = PathGuard([root])
    escaped = str(root) + os.sep + os.sep.join(parts + [".."] * (len(parts) + 1) + ["outside.txt"])
    c = g.check(escaped, PathOp.WRITE)
    assert c.category != PathCategory.ALLOWED


def test_canonicalize_relative_uses_base(tmp_path: Path) -> None:
    canon, stream, _ = canonicalize("sub/../f.txt", base=tmp_path)
    assert canon.lower() == str(tmp_path / "f.txt").lower() and stream is None


@pytest.mark.skipif(sys.platform != "win32", reason="Windows env vars")
def test_env_var_expansion(guard: PathGuard) -> None:
    assert guard.check("%USERPROFILE%\\.ssh\\id_rsa", PathOp.READ).denied
