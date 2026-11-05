"""
Self-update for Secure Message (no GTK in here, so it can be tested on its own).

Flow:  check()  ->  download()  ->  install()  ->  the app restarts itself.

* Windows   : downloads the .msi, then a tiny helper script runs it (Windows asks for
              permission once) and starts the new version when it is done.
* AppImage  : downloads the new AppImage and replaces the running file.
* .deb/.rpm : downloads the package and installs it through PolicyKit (pkexec).
* Source    : can't update itself; the release page is opened instead.

Every download is checked against the SHA256SUMS.txt published with the release.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

REPO = "MehranQadirian/SecureMessage"
API_LATEST = f"https://api.github.com/repos/{REPO}/releases/latest"
RELEASES_PAGE = f"https://github.com/{REPO}/releases/latest"
CHECKSUMS_NAME = "SHA256SUMS.txt"
TIMEOUT = 20

# Release file names, e.g. Secure-Message-2.0.0-Win-x64.msi (see packaging/).
ASSET_PATTERNS = {
    "windows": re.compile(r"^Secure-Message-.+-Win-x64\.msi$", re.I),
    "appimage": re.compile(r"^Secure-Message-.+-x86_64\.AppImage$", re.I),
    "deb": re.compile(r"^Secure-Message-.+-amd64\.deb$", re.I),
    "rpm": re.compile(r"^Secure-Message-.+-x86_64\.rpm$", re.I),
}


class UpdateError(Exception):
    """Something went wrong; the message is safe to show to the person."""


@dataclass
class Release:
    version: str
    notes: str
    page: str
    assets: dict[str, str] = field(default_factory=dict)      # file name -> download URL


@dataclass
class Update:
    release: Release
    flavour: str                    # windows | appimage | deb | rpm | source
    asset_name: Optional[str]
    asset_url: Optional[str]

    @property
    def can_install(self) -> bool:
        return self.flavour != "source" and bool(self.asset_url)


# ---------------------------------------------------------------------------
# Versions
# ---------------------------------------------------------------------------

def parse_version(text: str) -> tuple:
    """'v2.1.0' -> (2, 1, 0, 1, ()). A pre-release ('2.1.0-beta.1') sorts before the release."""
    m = re.match(r"^\s*v?(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z.-]+))?", text or "")
    if not m:
        raise ValueError(f"Not a version: {text!r}")
    major, minor, patch, pre = int(m[1]), int(m[2]), int(m[3]), m[4]
    pre_key = tuple((0, int(p)) if p.isdigit() else (1, p) for p in pre.split(".")) if pre else ()
    return (major, minor, patch, 0 if pre else 1, pre_key)


def is_newer(candidate: str, current: str) -> bool:
    try:
        return parse_version(candidate) > parse_version(current)
    except ValueError:
        return False


# ---------------------------------------------------------------------------
# How is this copy of the app installed?
# ---------------------------------------------------------------------------

def _ok(cmd: list[str]) -> bool:
    try:
        return subprocess.run(cmd, capture_output=True, timeout=15).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def detect_flavour() -> str:
    if sys.platform == "win32":
        return "windows" if getattr(sys, "frozen", False) else "source"
    if sys.platform.startswith("linux"):
        appimage = os.environ.get("APPIMAGE")
        if appimage and Path(appimage).is_file():
            return "appimage"
        if str(Path(__file__).resolve()).startswith("/usr/share/securemessage/"):
            if shutil.which("dpkg") and _ok(["dpkg", "-s", "securemessage"]):
                return "deb"
            if shutil.which("rpm") and _ok(["rpm", "-q", "securemessage"]):
                return "rpm"
    return "source"


# ---------------------------------------------------------------------------
# Talking to GitHub
# ---------------------------------------------------------------------------

def _request(url: str, accept: str = "application/vnd.github+json") -> urllib.request.Request:
    return urllib.request.Request(url, headers={"User-Agent": "Secure-Message-Updater", "Accept": accept})


def fetch_latest() -> Release:
    try:
        with urllib.request.urlopen(_request(API_LATEST), timeout=TIMEOUT) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        raise UpdateError(f"Couldn't reach GitHub to look for updates ({exc}).") from exc
    if not isinstance(data, dict) or not data.get("tag_name"):
        raise UpdateError("GitHub returned an unexpected answer.")
    assets = {a["name"]: a["browser_download_url"] for a in data.get("assets", [])
              if isinstance(a, dict) and a.get("name") and a.get("browser_download_url")}
    return Release(version=str(data["tag_name"]).lstrip("vV"), notes=str(data.get("body") or ""),
                   page=str(data.get("html_url") or RELEASES_PAGE), assets=assets)


def pick_asset(flavour: str, assets: dict[str, str],
               machine: Optional[str] = None) -> tuple[Optional[str], Optional[str]]:
    machine = (machine or platform.machine()).lower()
    if flavour != "windows" and machine not in ("x86_64", "amd64"):
        return None, None                       # the Linux files are built for Intel/AMD only
    pattern = ASSET_PATTERNS.get(flavour)
    if pattern:
        for name, url in sorted(assets.items()):
            if pattern.match(name):
                return name, url
    return None, None


def check(current_version: str, flavour: Optional[str] = None) -> Optional[Update]:
    """Returns an Update if a newer release exists, otherwise None."""
    release = fetch_latest()
    if not is_newer(release.version, current_version):
        return None
    flavour = flavour or detect_flavour()
    name, url = pick_asset(flavour, release.assets)
    return Update(release, flavour, name, url)


# ---------------------------------------------------------------------------
# Download + verify
# ---------------------------------------------------------------------------

def parse_checksums(text: str) -> dict[str, str]:
    out = {}
    for line in text.splitlines():
        m = re.match(r"^([0-9a-fA-F]{64})\s+\*?(.+?)\s*$", line)
        if m:
            out[m[2]] = m[1].lower()
    return out


def download(update: Update, progress: Optional[Callable[[float, str], None]] = None,
             cancelled: Optional[Callable[[], bool]] = None, dest_dir: Optional[Path] = None) -> Path:
    """Download and verify the installer. progress(fraction 0..1, text)."""
    if not update.can_install:
        raise UpdateError("There is no installer for this system in the latest release.")
    say = progress or (lambda *_: None)
    dest_dir = Path(dest_dir or tempfile.mkdtemp(prefix="secure-message-update-"))
    dest_dir.mkdir(parents=True, exist_ok=True)
    sums_url = update.release.assets.get(CHECKSUMS_NAME)
    if not sums_url:
        raise UpdateError("The release has no checksum file, so the download can't be verified.")
    try:
        with urllib.request.urlopen(_request(sums_url, "*/*"), timeout=TIMEOUT) as resp:
            expected = parse_checksums(resp.read().decode("utf-8", "replace")).get(update.asset_name)
    except Exception as exc:
        raise UpdateError(f"Couldn't download the checksum file ({exc}).") from exc
    if not expected:
        raise UpdateError("The checksum file doesn't list this installer.")

    target = dest_dir / update.asset_name
    digest = hashlib.sha256()
    try:
        with urllib.request.urlopen(_request(update.asset_url, "application/octet-stream"),
                                    timeout=TIMEOUT) as resp, open(target, "wb") as out:
            total = int(resp.headers.get("Content-Length") or 0)
            done = 0
            while True:
                if cancelled and cancelled():
                    raise UpdateError("Cancelled.")
                chunk = resp.read(256 * 1024)
                if not chunk:
                    break
                out.write(chunk)
                digest.update(chunk)
                done += len(chunk)
                if total:
                    say(done / total, f"{done / 1_048_576:.1f} of {total / 1_048_576:.1f} MB")
                else:
                    say(0.0, f"{done / 1_048_576:.1f} MB")
    except UpdateError:
        target.unlink(missing_ok=True)
        raise
    except Exception as exc:
        target.unlink(missing_ok=True)
        raise UpdateError(f"The download failed ({exc}).") from exc
    if digest.hexdigest() != expected:
        target.unlink(missing_ok=True)
        raise UpdateError("The downloaded file doesn't match its checksum, so it was discarded.")
    say(1.0, "Verified")
    return target


# ---------------------------------------------------------------------------
# Install + restart
# ---------------------------------------------------------------------------

def _restart_later(cmd: list[str], delay: float = 1.5) -> None:
    """Start `cmd` after this process has quit (a running instance would swallow it)."""
    subprocess.Popen(["sh", "-c", f'sleep {delay}; exec "$@"', "sh", *cmd],
                     start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _install_windows(installer: Path) -> None:
    exe = Path(sys.executable)
    script = installer.with_suffix(".cmd")
    # ping is the delay: `timeout` refuses to run without a console.
    script.write_text(
        "@echo off\r\n"
        "ping -n 3 127.0.0.1 >nul\r\n"
        f'msiexec /i "{installer}" /passive /norestart\r\n'
        f'start "" "{exe}"\r\n'
        'del "%~f0"\r\n', encoding="utf-8")
    flags = (getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
             | getattr(subprocess, "CREATE_NO_WINDOW", 0))
    subprocess.Popen(["cmd.exe", "/c", str(script)], creationflags=flags, close_fds=True)


def _install_appimage(installer: Path) -> None:
    current = Path(os.environ["APPIMAGE"])
    staged = current.with_name(current.name + ".new")
    try:
        shutil.copyfile(installer, staged)
        os.chmod(staged, 0o755)
        os.replace(staged, current)                 # the running copy keeps working until it quits
    except OSError as exc:
        staged.unlink(missing_ok=True)
        raise UpdateError(f"Couldn't replace {current} ({exc}). Is the folder read-only?") from exc
    _restart_later([str(current)])


def _install_package(installer: Path, flavour: str) -> None:
    import bootstrap                                 # reuses its pkexec handling
    if flavour == "deb":
        cmd = ["apt-get", "install", "-y", "--allow-downgrades", str(installer)]
    elif shutil.which("dnf"):
        cmd = ["dnf", "install", "-y", str(installer)]
    elif shutil.which("zypper"):
        cmd = ["zypper", "--non-interactive", "--no-gpg-checks", "install", "--allow-unsigned-rpm", str(installer)]
    else:
        cmd = ["rpm", "-U", "--replacepkgs", str(installer)]
    ok, msg = bootstrap.run_privileged(cmd)
    if not ok:
        raise UpdateError(f"The installation didn't finish: {msg}")
    _restart_later(["securemessage"])


def install(update: Update, installer: Path) -> None:
    """Install the verified file. On return the caller should quit the app; it restarts itself."""
    if update.flavour == "windows":
        _install_windows(installer)
    elif update.flavour == "appimage":
        _install_appimage(installer)
    elif update.flavour in ("deb", "rpm"):
        _install_package(installer, update.flavour)
    else:
        raise UpdateError("This copy of Secure Message can't update itself.")
