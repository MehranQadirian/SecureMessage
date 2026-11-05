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

# Pure-Python packages shipped inside the .deb/.rpm (e.g. kyber-py), so the first run
# needs no internet. Absent in a source checkout and in the Windows bundle.
VENDOR = Path(__file__).resolve().parent / "vendor"


def activate() -> None:
    changed = False
    for folder in (PYLIB, VENDOR):
        if folder.is_dir() and str(folder) not in sys.path:
            sys.path.append(str(folder))
            changed = True
    if changed:
        importlib.invalidate_caches()


def pip_install(packages: list[str], log=None) -> tuple[bool, str]:
    """Install into PYLIB. Returns (ok, last output lines)."""
    if getattr(sys, "frozen", False):
        # In a packaged build sys.executable is the app itself, so "-m pip" would just
        # start a second copy of the app instead of pip.
        return False, ("This packaged version of Secure Message can't download extra "
                       "components. Run it from source with Python to add them.")
    PYLIB.mkdir(parents=True, exist_ok=True)
    cmd = [sys.executable, "-m", "pip", "install", "--disable-pip-version-check",
           "--upgrade", "--target", str(PYLIB), *packages]
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)     # no console flash on Windows
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                creationflags=flags)
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
