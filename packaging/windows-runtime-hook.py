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
