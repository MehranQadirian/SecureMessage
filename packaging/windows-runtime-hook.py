import os
import sys

# PyInstaller onedir runtime directory
BASE = getattr(
    sys,
    "_MEIPASS",
    os.path.join(os.path.dirname(sys.executable), "_internal")
)

def add_path(name, path):
    if os.path.isdir(path):
        current = os.environ.get(name, "")
        paths = [p for p in current.split(os.pathsep) if p]
        if path not in paths:
            os.environ[name] = path + (
                os.pathsep + current if current else ""
            )

# Windows DLL search paths
for path in (BASE, os.path.join(BASE, "lib")):
    if os.path.isdir(path):
        if hasattr(os, "add_dll_directory"):
            os.add_dll_directory(path)
        add_path("PATH", path)

# GObject Introspection typelibs
typelib_dir = os.path.join(BASE, "lib", "girepository-1.0")
add_path("GI_TYPELIB_PATH", typelib_dir)

# GTK runtime data
share_dir = os.path.join(BASE, "share")
os.environ["GTK_DATA_PREFIX"] = BASE
os.environ["GTK_EXE_PREFIX"] = BASE
os.environ["XDG_DATA_DIRS"] = share_dir

schema_dir = os.path.join(share_dir, "glib-2.0", "schemas")
if os.path.isdir(schema_dir):
    os.environ["GSETTINGS_SCHEMA_DIR"] = schema_dir

# GDK Pixbuf loaders
pixbuf_dir = os.path.join(
    BASE, "lib", "gdk-pixbuf-2.0", "2.10.0", "loaders"
)

if os.path.isdir(pixbuf_dir):
    os.environ["GDK_PIXBUF_MODULEDIR"] = pixbuf_dir

    cache = os.path.join(pixbuf_dir, "loaders.cache")
    if os.path.isfile(cache):
        os.environ["GDK_PIXBUF_MODULE_FILE"] = cache

# GIO modules
gio_modules = os.path.join(BASE, "lib", "gio", "modules")
if os.path.isdir(gio_modules):
    add_path("GIO_EXTRA_MODULES", gio_modules)

# ---------------------------------------------------------------------------
# GStreamer (Voice recording / playback)
# ---------------------------------------------------------------------------
gst_plugins = os.path.join(BASE, "lib", "gstreamer-1.0")
if os.path.isdir(gst_plugins):
    os.environ["GST_PLUGIN_PATH"] = gst_plugins
    os.environ["GST_PLUGIN_SYSTEM_PATH"] = gst_plugins
    # Scan plugins inside this process. The default helper (gst-plugin-scanner.exe) is a
    # console program and would flash a black window the first time Voice is opened.
    os.environ["GST_REGISTRY_FORK"] = "no"
    _local = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    _cache = os.path.join(_local, "Secure Message")
    try:
        os.makedirs(_cache, exist_ok=True)
        os.environ["GST_REGISTRY"] = os.path.join(_cache, "gst-registry.bin")
    except OSError:
        pass

# ---------------------------------------------------------------------------
# No console in the windowed build: keep output and errors in a log file, and tell
# the person if the app dies before any window could be shown.
# ---------------------------------------------------------------------------
def _log_dir():
    root = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    path = os.path.join(root, "Secure Message", "logs")
    try:
        os.makedirs(path, exist_ok=True)
    except OSError:
        return None
    return path


_logs = _log_dir()
if _logs and (sys.stdout is None or sys.stderr is None):
    try:
        _fh = open(os.path.join(_logs, "secure-message.log"), "a", buffering=1, encoding="utf-8")
        if sys.stdout is None:
            sys.stdout = _fh
        if sys.stderr is None:
            sys.stderr = _fh
    except OSError:
        pass

_default_hook = sys.excepthook


def _fatal(exc_type, exc, tb):
    import traceback
    text = "".join(traceback.format_exception(exc_type, exc, tb))
    try:
        if sys.stderr is not None:
            sys.stderr.write(text)
    except Exception:
        pass
    if "--selftest" not in sys.argv:
        try:
            import ctypes
            where = f"\n\nDetails were saved in:\n{_logs}" if _logs else ""
            ctypes.windll.user32.MessageBoxW(
                0, f"Secure Message couldn't start.\n\n{exc_type.__name__}: {exc}{where}",
                "Secure Message", 0x10)
        except Exception:
            pass
    _default_hook(exc_type, exc, tb)


sys.excepthook = _fatal
