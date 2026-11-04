#!/usr/bin/env bash
#
# linux-build.sh — build .deb, .rpm and an AppImage for SecureMessage.
#
# This does NOT bundle Python, GTK or libadwaita: those come from the distro's own
# package manager (every build declares them as dependencies), and SecureMessage
# installs its own pure-Python packages (cryptography, numpy, pqcrypto, ...) into a
# private folder the first time it runs — the same thing it already does on a plain
# `python3 main.py` run. This keeps the packages small and lets SecureMessage track
# the distro's own GTK/libadwaita/GStreamer instead of shipping a second copy.
#
# Usage:
#   ./packaging/linux-build.sh            # builds whichever of deb/rpm/appimage
#                                          # it has the tools for
#   ./packaging/linux-build.sh deb
#   ./packaging/linux-build.sh rpm
#   ./packaging/linux-build.sh appimage
#   ./packaging/linux-build.sh all        # fail instead of skipping a missing tool
#
# Requires, for each target (only the ones you build need to be installed):
#   deb       : dpkg-deb (package "dpkg-dev" — on nearly every Debian/Ubuntu already)
#   rpm       : rpmbuild  (package "rpm-build" / "rpm")
#   appimage  : wget (or curl) + FUSE, or just the extract-and-run fallback this
#               script uses automatically when FUSE isn't available (e.g. in a
#               container) — no extra packages needed.
#
# Run this from anywhere; it finds the project root on its own.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
APP_ID="com.securemessage.app"
APP_NAME="SecureMessage"
VERSION="$(grep -m1 'APP_VERSION = ' "$ROOT/main.py" | sed -E 's/.*"([^"]+)".*/\1/')"
ARCH="$(dpkg --print-architecture 2>/dev/null || uname -m)"
OUT="$ROOT/dist/linux"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

mkdir -p "$OUT"
echo "SecureMessage $VERSION — building for: ${*:-auto}"

# Runtime dependencies, written once and reused for both .deb and the AppImage's
# desktop file. Names differ between deb and rpm distros, so each target below
# uses its own list (see bootstrap.py's SYSTEM_PACKAGES for where these come from).
DEB_DEPS="python3 (>= 3.10), python3-gi, python3-gi-cairo, gir1.2-gtk-4.0, gir1.2-adw-1, libgtk-4-1, libadwaita-1-0, librsvg2-common, gir1.2-gstreamer-1.0, gir1.2-gst-plugins-base-1.0, gstreamer1.0-plugins-base, gstreamer1.0-plugins-good, python3-pip"
RPM_DEPS="python3 python3-gobject gtk4 libadwaita librsvg2 gstreamer1 gstreamer1-plugins-base gstreamer1-plugins-good python3-pip"

# ---------------------------------------------------------------------------
# Shared: the files every package installs (source tree + launcher + desktop entry)
# ---------------------------------------------------------------------------
stage_payload() {  # $1 = staging root
    local stage="$1"
    install -d "$stage/usr/share/securemessage" "$stage/usr/bin" \
               "$stage/usr/share/applications" \
               "$stage/usr/share/icons/hicolor/scalable/apps" \
               "$stage/usr/share/metainfo"

    # App source. node_modules-style junk and __pycache__ never belong in a package.
    rsync -a --exclude='__pycache__' --exclude='*.pyc' --exclude='.git' \
             --exclude='dist' --exclude='packaging' --exclude='icons/windows' \
             "$ROOT"/ "$stage/usr/share/securemessage/"

    cat > "$stage/usr/bin/securemessage" <<EOF
#!/bin/sh
exec python3 /usr/share/securemessage/main.py "\$@"
EOF
    chmod 755 "$stage/usr/bin/securemessage"

    install -m644 "$ROOT/icons/hicolor/scalable/apps/$APP_ID.svg" \
                  "$stage/usr/share/icons/hicolor/scalable/apps/$APP_ID.svg"

    cat > "$stage/usr/share/applications/$APP_ID.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=$APP_NAME
Comment=Encrypt and decrypt text, voice and files
Exec=securemessage
Icon=$APP_ID
Categories=Utility;Security;
Terminal=false
StartupWMClass=$APP_ID
EOF

    cat > "$stage/usr/share/metainfo/$APP_ID.metainfo.xml" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<component type="desktop-application">
  <id>$APP_ID</id>
  <name>$APP_NAME</name>
  <summary>Encrypt and decrypt text, voice and files</summary>
  <metadata_license>MIT</metadata_license>
  <project_license>MIT</project_license>
  <developer_name>Mehran Ghadirian</developer_name>
  <launchable type="desktop-id">$APP_ID.desktop</launchable>
</component>
EOF
}

need() { command -v "$1" >/dev/null 2>&1; }

# ---------------------------------------------------------------------------
# .deb
# ---------------------------------------------------------------------------
build_deb() {
    if ! need dpkg-deb; then echo "skip: .deb (dpkg-deb not found — install dpkg-dev)"; return 1; fi
    echo "--> .deb"
    local stage="$WORK/deb"
    stage_payload "$stage"
    install -d "$stage/DEBIAN"
    cat > "$stage/DEBIAN/control" <<EOF
Package: securemessage
Version: $VERSION
Section: utils
Priority: optional
Architecture: $ARCH
Depends: $DEB_DEPS
Maintainer: Mehran Ghadirian <https://github.com/mehranqadirian>
Description: Encrypt and decrypt text, voice and files
 SecureMessage encrypts text, voice recordings and arbitrary files with a
 password or a post-quantum key (ML-KEM-768 + X25519), and can hide the
 result inside a picture or an audio file.
EOF
    cat > "$stage/DEBIAN/postinst" <<'EOF'
#!/bin/sh
set -e
command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database -q /usr/share/applications || true
command -v gtk-update-icon-cache >/dev/null 2>&1 && gtk-update-icon-cache -qtf /usr/share/icons/hicolor || true
exit 0
EOF
    chmod 755 "$stage/DEBIAN/postinst"
    local file="$OUT/securemessage_${VERSION}_${ARCH}.deb"
    dpkg-deb --build --root-owner-group "$stage" "$file" >/dev/null
    echo "    $file"
}

# ---------------------------------------------------------------------------
# .rpm
# ---------------------------------------------------------------------------
build_rpm() {
    if ! need rpmbuild; then echo "skip: .rpm (rpmbuild not found — install rpm-build)"; return 1; fi
    echo "--> .rpm"
    local topdir="$WORK/rpmbuild"
    install -d "$topdir"/{BUILD,RPMS,SOURCES,SPECS,SRPMS}
    local stage="$WORK/rpm-root"
    stage_payload "$stage"
    (cd "$stage" && tar czf "$topdir/SOURCES/securemessage-$VERSION.tar.gz" .)

    cat > "$topdir/SPECS/securemessage.spec" <<EOF
Name:           securemessage
Version:        $VERSION
Release:        1%{?dist}
Summary:        Encrypt and decrypt text, voice and files
License:        MIT
URL:            https://github.com/mehranqadirian/securemessage
Source0:        securemessage-$VERSION.tar.gz
BuildArch:      noarch
Requires:       $RPM_DEPS

%description
SecureMessage encrypts text, voice recordings and arbitrary files with a
password or a post-quantum key (ML-KEM-768 + X25519), and can hide the
result inside a picture or an audio file.

%prep
%setup -q -c -T
tar xzf %{SOURCE0}

%install
rm -rf %{buildroot}
cp -a . %{buildroot}

%files
/usr/bin/securemessage
/usr/share/securemessage
/usr/share/applications/$APP_ID.desktop
/usr/share/icons/hicolor/scalable/apps/$APP_ID.svg
/usr/share/metainfo/$APP_ID.metainfo.xml

%post
update-desktop-database -q /usr/share/applications 2>/dev/null || :
gtk-update-icon-cache -qtf /usr/share/icons/hicolor 2>/dev/null || :

%changelog
* $(date "+%a %b %d %Y") Mehran Ghadirian <https://github.com/mehranqadirian> - $VERSION-1
- Automated build
EOF
    rpmbuild --define "_topdir $topdir" -bb "$topdir/SPECS/securemessage.spec" >/dev/null
    find "$topdir/RPMS" -name '*.rpm' -exec cp {} "$OUT/" \;
    echo "    $(find "$OUT" -maxdepth 1 -name '*.rpm' -newer "$topdir/SPECS/securemessage.spec")"
}

# ---------------------------------------------------------------------------
# AppImage (GTK/libadwaita bundled via linuxdeploy's official GTK plugin, so
# the result runs even on a system without GTK4/libadwaita installed)
# ---------------------------------------------------------------------------
build_appimage() {
    echo "--> AppImage"
    local appdir="$WORK/AppDir"
    local usr="$appdir/usr"
    install -d "$usr/share/securemessage" "$usr/bin" \
               "$usr/share/applications" \
               "$usr/share/icons/hicolor/scalable/apps"
    rsync -a --exclude='__pycache__' --exclude='*.pyc' --exclude='.git' \
             --exclude='dist' --exclude='packaging' --exclude='icons/windows' \
             "$ROOT"/ "$usr/share/securemessage/"

    cat > "$usr/bin/securemessage" <<'EOF'
#!/bin/sh
exec python3 "$(dirname "$(readlink -f "$0")")/../share/securemessage/main.py" "$@"
EOF
    chmod 755 "$usr/bin/securemessage"
    install -m644 "$ROOT/icons/hicolor/scalable/apps/$APP_ID.svg" \
                  "$usr/share/icons/hicolor/scalable/apps/$APP_ID.svg"
    ln -sf "usr/share/icons/hicolor/scalable/apps/$APP_ID.svg" "$appdir/$APP_ID.svg"

    cat > "$usr/share/applications/$APP_ID.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=$APP_NAME
Exec=securemessage
Icon=$APP_ID
Categories=Utility;Security;
Terminal=false
EOF
    ln -sf "usr/share/applications/$APP_ID.desktop" "$appdir/$APP_ID.desktop"

    local tools="$WORK/tools"; install -d "$tools"
    fetch() {  # $1 = url, $2 = output path
        if need wget; then wget -q -O "$2" "$1"; else curl -fsSL -o "$2" "$1"; fi
    }
    echo "    fetching linuxdeploy + GTK plugin + appimagetool..."
    fetch "https://github.com/linuxdeploy/linuxdeploy/releases/download/continuous/linuxdeploy-x86_64.AppImage" \
          "$tools/linuxdeploy"
    # linuxdeploy-plugin-gtk has no release assets; its usable script lives on the master branch.
    fetch "https://raw.githubusercontent.com/linuxdeploy/linuxdeploy-plugin-gtk/master/linuxdeploy-plugin-gtk.sh" \
          "$tools/linuxdeploy-plugin-gtk.sh"
    fetch "https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-x86_64.AppImage" \
          "$tools/appimagetool"
    chmod +x "$tools/linuxdeploy" "$tools/linuxdeploy-plugin-gtk.sh" "$tools/appimagetool"

    # Needs python3 + PyGObject itself present at build time (to copy them in);
    # install python3-gi beforehand the same way you would to run SecureMessage.
    if ! python3 -c "import gi" >/dev/null 2>&1; then
        echo "    error: python3-gi isn't installed on this build machine (needed so the"
        echo "           AppImage can bundle it). Install it, then re-run."
        return 1
    fi
    local pysite
    pysite="$(python3 -c 'import gi,os; print(os.path.dirname(os.path.dirname(gi.__file__)))')"
    install -d "$usr/lib/python3/dist-packages"
    cp -a "$pysite/gi" "$usr/lib/python3/dist-packages/" 2>/dev/null || true

    cd "$WORK"
    export NO_STRIP=1            # keep this reproducible / debuggable
    export PATH="$tools:$PATH"   # linuxdeploy looks for linuxdeploy-plugin-gtk.sh on PATH
    export DEPLOY_GTK_VERSION=4  # we use GTK4 + libadwaita
    local run_linuxdeploy=("$tools/linuxdeploy" --appdir "$appdir" --plugin gtk --output appimage
                           --desktop-file "$appdir/$APP_ID.desktop" --icon-file "$appdir/$APP_ID.svg")
    if ! "${run_linuxdeploy[@]}" 2>&1 | tail -n 30; then
        echo "    linuxdeploy needs FUSE to run directly; retrying with --appimage-extract-and-run"
        "$tools/linuxdeploy" --appimage-extract-and-run "${run_linuxdeploy[@]:1}"
    fi
    local raw
    raw="$(find "$WORK" -maxdepth 1 -name '*.AppImage' | head -1)"
    if [ -z "$raw" ]; then echo "    linuxdeploy did not produce an AppImage"; return 1; fi

    # The GTK plugin's gdk-pixbuf loaders.cache lists bare module filenames, which GModule
    # resolves through the dynamic linker's search path — not GDK_PIXBUF_MODULE_FILE's own
    # directory. Left as-is, every SVG icon (ours, and Adwaita's built-in ones) silently
    # fails to load inside the AppImage. Extract, patch the GTK hook to also export
    # GDK_PIXBUF_MODULEDIR/LD_LIBRARY_PATH, and repack.
    local extracted="$WORK/AppImage-extracted"
    (cd "$WORK" && "$raw" --appimage-extract >/dev/null && mv squashfs-root "$extracted")
    local hook="$extracted/apprun-hooks/linuxdeploy-plugin-gtk.sh"
    if [ -f "$hook" ]; then
        cat >> "$hook" <<'HOOKEOF'

# --- SecureMessage fix: see the comment above this block in linux-build.sh.
export GDK_PIXBUF_MODULEDIR="$APPDIR/usr/lib/gdk-pixbuf-2.0/2.10.0/loaders"
export LD_LIBRARY_PATH="$GDK_PIXBUF_MODULEDIR:$LD_LIBRARY_PATH"
HOOKEOF
    fi
    chmod -R a+rX "$extracted"
    chmod a+rx "$extracted/AppRun"
    local final="$OUT/SecureMessage-$VERSION-x86_64.AppImage"
    rm -f "$final"
    if ! ARCH=x86_64 "$tools/appimagetool" "$extracted" "$final" 2>&1 | tail -n 15; then
        ARCH=x86_64 "$tools/appimagetool" --appimage-extract-and-run "$extracted" "$final"
    fi
    chmod +x "$final"
    echo "    $final"
}

targets=("${@:-auto}")
strict=false
[ "${targets[0]}" = "all" ] && { targets=(deb rpm appimage); strict=true; }
[ "${targets[0]}" = "auto" ] && targets=(deb rpm appimage)

fail=0
for t in "${targets[@]}"; do
    case "$t" in
        deb) build_deb || { [ "$t" != auto ] && [ "$strict" = true ] && fail=1; } ;;
        rpm) build_rpm || { [ "$strict" = true ] && fail=1; } ;;
        appimage) build_appimage || { [ "$strict" = true ] && fail=1; } ;;
        *) echo "unknown target: $t (use deb, rpm, appimage or all)"; fail=1 ;;
    esac
done

echo
echo "Done. Output in: $OUT"
ls -la "$OUT" 2>/dev/null || true
exit $fail
