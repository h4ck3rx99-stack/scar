"""Makes the pip-installed NVIDIA CUDA 12 DLLs (nvidia-*-cu12 wheels) loadable on Windows."""
import os
import sys
from pathlib import Path

_done = False


def add_nvidia_dll_dirs():
    global _done
    if _done:
        return
    _done = True
    for sp in sys.path:
        root = Path(sp) / "nvidia"
        if root.is_dir():
            for b in root.glob("*/bin"):
                os.add_dll_directory(str(b))
                os.environ["PATH"] = str(b) + os.pathsep + os.environ["PATH"]
