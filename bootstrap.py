#!/usr/bin/env python3
"""
Cross-distro bootstrap for Secure Message.
Detects package manager, installs system + Python deps via GUI.
"""

from __future__ import annotations

import os
import sys
import subprocess
import shutil
import threading
from pathlib import Path

import pylib  # private package dir (PEP 668 safe)

# ---------------------------------------------------------------------------
# Distro / package manager detection
# ---------------------------------------------------------------------------

def detect_package_manager() -> tuple[str, list[str]]:
    """
    Returns (pm_name, install_cmd_prefix)
    install_cmd_prefix already includes the package names placeholder later.
    """
    # Prefer checking binaries first (more reliable than os-release alone)
    if shutil.which("apt-get"):
        return "apt", ["apt-get", "install", "-y"]
    if shutil.which("dnf"):
        return "dnf", ["dnf", "install", "-y"]
    if shutil.which("yum") and not shutil.which("dnf"):
        return "yum", ["yum", "install", "-y"]
    if shutil.which("pacman"):
        return "pacman", ["pacman", "-S", "--noconfirm", "--needed"]
    if shutil.which("zypper"):
        return "zypper", ["zypper", "--non-interactive", "install"]
    if shutil.which("apk"):
        return "apk", ["apk", "add", "--no-cache"]
    if shutil.which("emerge"):
        return "emerge", ["emerge", "--ask=n"]
    if shutil.which("xbps-install"):
        return "xbps", ["xbps-install", "-y"]
    return "unknown", []


# Packages needed per family
SYSTEM_PACKAGES = {
    "apt": [
        "python3-gi", "python3-gi-cairo",
        "gir1.2-gtk-4.0", "gir1.2-adw-1",
        "libgtk-4-1", "libadwaita-1-0", "librsvg2-common",
    ],
    "dnf": [
        "python3-gobject", "python3-cairo", "gtk4", "libadwaita",
        "gobject-introspection", "librsvg2",
    ],
    "yum": [
        "python3-gobject", "gtk4", "libadwaita",
    ],
    "pacman": [
        "python-gobject", "gtk4", "libadwaita", "librsvg",
    ],
    "zypper": [
        "python3-gobject", "gtk4", "libadwaita",
        "typelib-1_0-Gtk-4_0", "typelib-1_0-Adw-1", "gdk-pixbuf-loader-rsvg",
    ],
    "apk": [
        "py3-gobject3", "gtk4.0", "libadwaita",
        "gobject-introspection",
    ],
    "emerge": [],  # too varied; skip auto
    "xbps": [
        "python3-gobject", "gtk4", "libadwaita",
    ],
}


# Needed only for the Voice features (recording / playback)
AUDIO_PACKAGES = {
    "apt": ["gir1.2-gstreamer-1.0", "gir1.2-gst-plugins-base-1.0", "gstreamer1.0-plugins-base",
            "gstreamer1.0-plugins-good", "gstreamer1.0-pipewire"],
    "dnf": ["gstreamer1", "gstreamer1-plugins-base", "gstreamer1-plugins-good", "pipewire-gstreamer"],
    "yum": ["gstreamer1", "gstreamer1-plugins-base", "gstreamer1-plugins-good"],
    "pacman": ["gstreamer", "gst-plugins-base", "gst-plugins-good", "gst-plugin-pipewire"],
    "zypper": ["typelib-1_0-Gst-1_0", "gstreamer-plugins-base", "gstreamer-plugins-good"],
    "apk": ["gstreamer", "gst-plugins-base", "gst-plugins-good"],
    "xbps": ["gstreamer1", "gst-plugins-base1", "gst-plugins-good1"],
}


def need_system_packages() -> bool:
    try:
        import gi
        gi.require_version("Gtk", "4.0")
        gi.require_version("Adw", "1")
        from gi.repository import Gtk, Adw  # noqa
        return False
    except Exception:
        return True


def _importable(module: str) -> bool:
    try:
        __import__(module)
        return True
    except Exception:
        return False


def need_python_packages() -> list[str]:
    """pip names of required packages that are missing."""
    missing = []
    for mod, pkg in [("cryptography", "cryptography"), ("argon2", "argon2-cffi"), ("numpy", "numpy"),
                     ("PIL", "Pillow")]:
        if not _importable(mod):
            missing.append(pkg)
    # ML-KEM: PQClean build preferred, pure-Python fallback
    if not (_importable("pqcrypto.kem.ml_kem_768") or _importable("kyber_py.ml_kem")):
        missing.append("pqcrypto")
    return missing


def install_python_packages(pkgs: list[str], log=None) -> tuple[bool, str]:
    ok, out = pylib.pip_install(pkgs, log)
    if not ok and "pqcrypto" in pkgs:          # no wheel for this platform -> pure Python
        rest = [p for p in pkgs if p != "pqcrypto"]
        ok, out = pylib.pip_install(rest + ["kyber-py"], log)
    return ok, out


def run_privileged(cmd: list[str]) -> tuple[bool, str]:
    """
    Run command with privilege escalation preferring GUI methods.
    Returns (success, message).
    """
    # 1. pkexec (PolicyKit - shows native password dialog)
    if shutil.which("pkexec"):
        full = ["pkexec"] + cmd
        try:
            r = subprocess.run(full, capture_output=True, text=True, timeout=600)
            if r.returncode == 0:
                return True, "OK"
            return False, r.stderr.strip() or r.stdout.strip() or f"exit {r.returncode}"
        except subprocess.TimeoutExpired:
            return False, "Timeout"
        except Exception as e:
            return False, str(e)

    # 2. Fallback: gksu / kdesu
    for helper in ("gksu", "kdesu", "beesu"):
        if shutil.which(helper):
            full = [helper] + cmd
            try:
                r = subprocess.run(full, capture_output=True, text=True, timeout=600)
                if r.returncode == 0:
                    return True, "OK"
                return False, r.stderr.strip() or f"exit {r.returncode}"
            except Exception as e:
                return False, str(e)

    # 3. Last resort: plain sudo (may ask in terminal)
    if shutil.which("sudo"):
        full = ["sudo"] + cmd
        try:
            r = subprocess.run(full, capture_output=True, text=True, timeout=600)
            if r.returncode == 0:
                return True, "OK"
            return False, r.stderr.strip() or f"exit {r.returncode}"
        except Exception as e:
            return False, str(e)

    return False, "No privilege escalation tool found (pkexec/sudo)"


# ---------------------------------------------------------------------------
# GUI bootstrap window (uses only tkinter - available almost everywhere)
# ---------------------------------------------------------------------------

def run_bootstrap_gui() -> bool:
    """
    Show a progress window, install everything needed, return True on success.
    """
    try:
        import tkinter as tk
        from tkinter import ttk, messagebox
    except ImportError:
        # Extremely minimal fallback
        print("tkinter missing - trying console install")
        return _console_bootstrap()

    root = tk.Tk()
    root.title("Secure Message - Setup")
    root.geometry("520x320")
    root.resizable(False, False)

    # Center
    root.update_idletasks()
    x = (root.winfo_screenwidth() // 2) - 260
    y = (root.winfo_screenheight() // 2) - 160
    root.geometry(f"+{x}+{y}")

    frame = ttk.Frame(root, padding=20)
    frame.pack(fill="both", expand=True)

    title = ttk.Label(frame, text="Preparing Secure Message", font=("", 14, "bold"))
    title.pack(anchor="w", pady=(0, 8))

    status = ttk.Label(frame, text="Checking requirements…", wraplength=460, justify="left")
    status.pack(anchor="w", pady=(0, 6))

    log = tk.Text(frame, height=8, width=60, state="disabled", font=("monospace", 9))
    log.pack(fill="both", expand=True, pady=(0, 8))

    progress = ttk.Progressbar(frame, mode="indeterminate", length=460)
    progress.pack(pady=(0, 8))
    progress.start(14)

    result = {"ok": False, "error": None}

    def log_msg(msg: str):
        def _do():
            log.configure(state="normal")
            log.insert("end", msg + "\n")
            log.see("end")
            log.configure(state="disabled")
            status.config(text=msg)
        root.after(0, _do)

    def worker():
        try:
            # ---- Python packages ----
            py_missing = need_python_packages()
            if py_missing:
                log_msg(f"Installing Python packages: {', '.join(py_missing)}")
                ok, out = install_python_packages(py_missing, lambda l: None)
                if not ok:
                    raise RuntimeError("pip failed:\n" + out)
                log_msg("Python packages installed.")
            else:
                log_msg("Python packages already present.")

            # ---- System packages ----
            if need_system_packages():
                pm, cmd_prefix = detect_package_manager()
                if pm == "unknown" or not cmd_prefix:
                    log_msg("Unknown package manager. Please install GTK4 and libadwaita manually.")
                    result["error"] = "Unknown package manager"
                    root.after(0, finish)
                    return

                packages = SYSTEM_PACKAGES.get(pm, [])
                if not packages:
                    log_msg(f"No package list defined for {pm}. Skipping system install.")
                else:
                    log_msg(f"Detected package manager: {pm}")
                    log_msg("Installing system packages (password prompt may appear)…")
                    full_cmd = cmd_prefix + packages
                    # For apt, also update first
                    if pm == "apt":
                        log_msg("  → apt-get update")
                        ok, msg = run_privileged(["apt-get", "update", "-qq"])
                        if not ok:
                            log_msg(f"  update warning: {msg}")

                    log_msg(f"  → {' '.join(full_cmd)}")
                    ok, msg = run_privileged(full_cmd)
                    if not ok:
                        result["error"] = f"System install failed: {msg}"
                        log_msg(result["error"])
                        root.after(0, finish)
                        return
                    log_msg("System packages installed.")
            else:
                log_msg("GTK4 / Adwaita already available.")

            result["ok"] = True
            log_msg("All ready. Starting application…")
        except Exception as e:
            result["error"] = str(e)
            log_msg(f"Error: {e}")
        root.after(0, finish)

    def finish():
        progress.stop()
        if result["error"]:
            messagebox.showerror("Setup error", result["error"])
            root.destroy()
        else:
            root.destroy()

    threading.Thread(target=worker, daemon=True).start()
    root.mainloop()
    return result["ok"] and not result["error"]


def _console_bootstrap() -> bool:
    """Fallback without any GUI."""
    print("=== Secure Message bootstrap (console) ===")
    py_missing = need_python_packages()
    if py_missing:
        print("Installing Python packages:", py_missing)
        ok, out = install_python_packages(py_missing, print)
        if not ok:
            print(out)
            return False
    if need_system_packages():
        pm, prefix = detect_package_manager()
        pkgs = SYSTEM_PACKAGES.get(pm, [])
        if pkgs:
            print(f"Detected {pm}. Installing system packages (sudo may ask password)…")
            if pm == "apt":
                run_privileged(["apt-get", "update", "-qq"])
            ok, msg = run_privileged(prefix + pkgs)
            if not ok:
                print("Failed:", msg)
                return False
    return True


def ensure_ready() -> bool:
    """
    Call this before importing Gtk/Adw.
    Returns True if everything is ready.
    """
    if getattr(sys, "frozen", False):
        # A packaged build (PyInstaller, for the Windows installer) already bundles
        # Python, GTK and every dependency, so there is nothing left to install.
        return True
    py_ok = not need_python_packages()
    sys_ok = not need_system_packages()
    if py_ok and sys_ok:
        return True
    return run_bootstrap_gui()


if __name__ == "__main__":
    ok = ensure_ready()
    print("Ready:" if ok else "Failed")
    sys.exit(0 if ok else 1)
