"""
Private Python package directory.

Modern distros (PEP 668) refuse `pip install --user`, so packages the app needs are
installed with `pip --target` into ~/.local/share/securemessage/pylib instead. This
never touches the system Python and works the same on every distro.
"""

from __future__ import annotations

import importlib
import subprocess
import sys
from pathlib import Path


def _data_dir() -> Path:
    import os
    base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / "securemessage" / "pylib"


PYLIB = _data_dir()


def activate() -> None:
    if PYLIB.is_dir() and str(PYLIB) not in sys.path:
        sys.path.append(str(PYLIB))
        importlib.invalidate_caches()


def pip_install(packages: list[str], log=None) -> tuple[bool, str]:
    """Install into PYLIB. Returns (ok, last output lines)."""
    PYLIB.mkdir(parents=True, exist_ok=True)
    cmd = [sys.executable, "-m", "pip", "install", "--disable-pip-version-check",
           "--upgrade", "--target", str(PYLIB), *packages]
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        tail: list[str] = []
        for line in proc.stdout:
            line = line.rstrip()
            tail = (tail + [line])[-6:]
            if log:
                log(line)
        ok = proc.wait() == 0
    except OSError as exc:
        return False, str(exc)
    activate()
    return ok, "\n".join(tail)


activate()
