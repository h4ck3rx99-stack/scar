"""SCAR - Systemic Cognitive Autonomous Responder."""

import contextlib as _contextlib
import os as _os
import sys as _sys

__version__ = "1.0.0"

if _sys.platform == "win32":
    # PyWinRT wheels bundle an older msvcp140.dll. Whichever copy loads first is used process-wide, and onnxruntime
    # (memory embeddings, VAD, wake word) crashes with an access violation against the old one. Pin the system copy.
    with _contextlib.suppress(OSError):
        import ctypes as _ctypes

        _ctypes.WinDLL(_os.path.join(_os.environ.get("SYSTEMROOT", r"C:\Windows"), "System32", "msvcp140.dll"))
