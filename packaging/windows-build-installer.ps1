<#
.SYNOPSIS
    Build a self-contained Windows installer (.msi) for SecureMessage.

.DESCRIPTION
    *** This script has not been run on an actual Windows machine. ***
    It was written and reasoned through carefully (the same real GTK4 + libadwaita code
    SecureMessage already runs and was tested with on Linux, via the standard MSYS2
    runtime that real GTK apps use on Windows — e.g. GIMP, Inkscape), but there is no
    Windows environment available to run and debug it in. Treat the first run as a
    first draft: read any error it gives you, and tell me what went wrong so it can be
    fixed properly, rather than assuming it is broken on your end.

    What it does:
      1. Checks for MSYS2 (installs it with winget if missing and you allow it).
      2. Installs Python, PyGObject, GTK4, libadwaita and GStreamer *inside* MSYS2's
         UCRT64 environment — this is the actual native Windows build of GTK; it is
         not WSL and does not need Linux.
      3. Installs SecureMessage's own Python dependencies (cryptography, numpy,
         pqcrypto, ...) into that same environment with pip.
      4. Freezes the application with PyInstaller into a self-contained onedir build
         (Python + GTK + every .dll it needs), so the person who installs the MSI
         needs nothing else — no internet access, no MSYS2, no Python.
      5. Builds an .msi with the WiX Toolset (installed via `dotnet tool install` if
         missing) so double-clicking it installs SecureMessage like any other Windows
         application, with a Start Menu shortcut and an uninstaller.

    Run this from an elevated ("Run as Administrator") PowerShell window, from the
    repository's root folder or from packaging\:

        powershell -ExecutionPolicy Bypass -File packaging\windows-build-installer.ps1

    Output: dist\windows\SecureMessage-<version>-x64.msi
#>

[CmdletBinding()]
param(
    [switch]$SkipMsys2Setup,   # reuse an MSYS2 install you already prepared
    [switch]$SkipFreeze        # reuse a PyInstaller build you already have in build\windows
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$dist = Join-Path $root "dist\windows"
$build = Join-Path $root "build\windows"
$msys2 = "C:\msys64"
$mingw = Join-Path $msys2 "ucrt64.exe"
$pacman = Join-Path $msys2 "usr\bin\bash.exe"

function Say($msg) { Write-Host "==> $msg" -ForegroundColor Cyan }
function Run-InMsys2([string]$cmd) {
    # Runs a command inside the MSYS2 UCRT64 shell (where the native Windows GTK
    # toolchain and its Python live), the same way you would from the MSYS2 shortcut.
    & "$msys2\usr\bin\bash.exe" -lc "export MSYSTEM=UCRT64; export PATH=/ucrt64/bin:/usr/bin:`$PATH; $cmd"
    if ($LASTEXITCODE -ne 0) { throw "MSYS2 command failed: $cmd" }
}

New-Item -ItemType Directory -Force -Path $dist, $build | Out-Null

$version = (Select-String -Path (Join-Path $root "main.py") -Pattern 'APP_VERSION = "([^"]+)"').Matches[0].Groups[1].Value
Say "SecureMessage $version — building the Windows installer"

# ---------------------------------------------------------------------------
# 1) MSYS2 — the native-Windows GTK4 + libadwaita + Python toolchain
# ---------------------------------------------------------------------------
if (-not $SkipMsys2Setup) {
    if (-not (Test-Path $mingw)) {
        Say "MSYS2 not found at $msys2."
        if (Get-Command winget -ErrorAction SilentlyContinue) {
            Say "Installing MSYS2 with winget (you may see a UAC prompt)..."
            winget install -e --id MSYS2.MSYS2 --accept-package-agreements --accept-source-agreements
        } else {
            throw "MSYS2 isn't installed and winget isn't available. Install it from " +
                  "https://www.msys2.org, accept the defaults (installs to C:\msys64), " +
                  "then re-run this script."
        }
    }

    Say "Installing GTK4, libadwaita, GStreamer and Python into MSYS2's UCRT64 environment..."
    Run-InMsys2 "pacman -Syu --noconfirm"
    # One line on purpose: this string is run by *bash* (inside MSYS2), not PowerShell, so
    # it must use bash's own line-continuation (\) if split — easiest to just not split it.
    $mingwPkgs = "mingw-w64-ucrt-x86_64-python mingw-w64-ucrt-x86_64-python-pip " +
                 "mingw-w64-ucrt-x86_64-python-gobject mingw-w64-ucrt-x86_64-gtk4 mingw-w64-ucrt-x86_64-libadwaita " +
                 "mingw-w64-ucrt-x86_64-gobject-introspection mingw-w64-ucrt-x86_64-librsvg " +
                 "mingw-w64-ucrt-x86_64-gst-plugins-base mingw-w64-ucrt-x86_64-gst-plugins-good " +
                 "mingw-w64-ucrt-x86_64-python-pillow mingw-w64-ucrt-x86_64-python-numpy " +
                 "mingw-w64-ucrt-x86_64-python-cryptography mingw-w64-ucrt-x86_64-python-cffi " +
                 "mingw-w64-ucrt-x86_64-python-argon2_cffi " +
                 "mingw-w64-ucrt-x86_64-pyinstaller"
    Run-InMsys2 "pacman -S --needed --noconfirm $mingwPkgs"
    Say "Installing the pure-Python packages PyInstaller can't get from MSYS2's own repo..."
    Run-InMsys2 "/ucrt64/bin/python.exe -m pip install --upgrade kyber-py"
} else {
    Say "Skipping MSYS2 setup (-SkipMsys2Setup given); assuming it's already prepared."
}

# ---------------------------------------------------------------------------
# 2) Freeze with PyInstaller — one self-contained folder, nothing needed at install time
# ---------------------------------------------------------------------------
$frozenDir = Join-Path $build "SecureMessage"
$iconIco = (Join-Path $root "icons\windows\securemessage.ico") -replace '\\', '/'
if (-not $SkipFreeze) {
    Say "Freezing the app with PyInstaller (this takes a few minutes)..."
    $srcUnix = $root -replace '\\', '/'
    $buildUnix = $build -replace '\\', '/'

    # sys.frozen (set automatically by PyInstaller) makes bootstrap.ensure_ready() a no-op
    # (see bootstrap.py): everything needed is already in this bundle, so there is nothing
    # left to install on first run. One line, for the same reason as $mingwPkgs above.
    $pyiArgs = "--noconfirm --clean --windowed --name SecureMessage " +
               "--distpath '$buildUnix' --workpath '$buildUnix/pyinstaller-work' " +
               "--icon '$iconIco' " +
               "--collect-all gi --collect-all cairo --collect-all cryptography --collect-all kyber_py " +
               "--collect-all numpy --collect-all PIL --collect-all argon2 " +
               "--add-data 'icons;icons' main.py"
    Run-InMsys2 "cd '$srcUnix' && /ucrt64/bin/python.exe -m PyInstaller $pyiArgs"

    Say "Copying the GTK4/libadwaita runtime (DLLs, icons, GSettings schemas) next to the app..."
    $gtkBin = "$msys2\ucrt64\bin"
    $gtkShare = "$msys2\ucrt64\share"
    Copy-Item "$gtkBin\*.dll" -Destination "$frozenDir\SecureMessage\_internal" -Force
    New-Item -ItemType Directory -Force -Path "$frozenDir\SecureMessage\_internal\lib\gdk-pixbuf-2.0" | Out-Null
    Copy-Item "$msys2\ucrt64\lib\gdk-pixbuf-2.0\*" -Destination "$frozenDir\SecureMessage\_internal\lib\gdk-pixbuf-2.0" -Recurse -Force
    New-Item -ItemType Directory -Force -Path "$frozenDir\SecureMessage\_internal\share" | Out-Null
    foreach ($d in @("glib-2.0", "icons", "gtk-4.0")) {
        if (Test-Path "$gtkShare\$d") {
            Copy-Item "$gtkShare\$d" -Destination "$frozenDir\SecureMessage\_internal\share\$d" -Recurse -Force
        }
    }
    # Make the frozen app find its bundled GTK runtime instead of looking at the system.
    @"
import os, sys
_base = os.path.join(os.path.dirname(sys.executable), "_internal")
os.environ["GSETTINGS_SCHEMA_DIR"] = os.path.join(_base, "share", "glib-2.0", "schemas")
os.environ["XDG_DATA_DIRS"] = os.path.join(_base, "share")
os.environ["GDK_PIXBUF_MODULEDIR"] = os.path.join(_base, "lib", "gdk-pixbuf-2.0", "2.10.0", "loaders")
"@ | Out-File -Encoding utf8 (Join-Path $root "_winenv.py")
    Say "NOTE: _winenv.py was generated as a helper; if GTK can't find its schemas/icons at" `
        "runtime, import it at the very top of main.py (before the Gtk/Adw import) and re-freeze."
} else {
    Say "Skipping the freeze step (-SkipFreeze given); reusing $frozenDir"
}
if (-not (Test-Path (Join-Path $frozenDir "SecureMessage\SecureMessage.exe"))) {
    throw "PyInstaller didn't produce SecureMessage.exe — check the log above."
}

# ---------------------------------------------------------------------------
# 3) WiX Toolset — build the .msi
# ---------------------------------------------------------------------------
Say "Checking for the WiX Toolset..."
if (-not (Get-Command wix -ErrorAction SilentlyContinue)) {
    if (-not (Get-Command dotnet -ErrorAction SilentlyContinue)) {
        throw "Need the .NET SDK to install the WiX Toolset. Get it from " +
              "https://dotnet.microsoft.com/download, then re-run this script."
    }
    Say "Installing the WiX Toolset CLI (dotnet tool install -g wix)..."
    dotnet tool install -g wix
    $env:PATH += ";$env:USERPROFILE\.dotnet\tools"
}
wix extension add -g WixToolset.UI.wixext 2>$null

$productId = [guid]::NewGuid().ToString()
$upgradeCode = "6E3B1A2E-6B1E-4B8C-9D2B-5E7A8C9F0A11"   # keep this fixed across versions
$wxs = Join-Path $build "SecureMessage.wxs"

@"
<?xml version="1.0" encoding="UTF-8"?>
<Wix xmlns="http://wixtoolset.org/schemas/v4/wxs" xmlns:ui="http://wixtoolset.org/schemas/v4/wxs/ui">
  <Package Name="SecureMessage" Manufacturer="Mehran Ghadirian" Version="$version"
           UpgradeCode="$upgradeCode" Language="1033" Scope="perMachine">

    <MajorUpgrade DowngradeErrorMessage="A newer version of SecureMessage is already installed." />
    <MediaTemplate EmbedCab="yes" />
    <Icon Id="AppIcon.ico" SourceFile="$($iconIco -replace '/', '\')" />
    <Property Id="ARPPRODUCTICON" Value="AppIcon.ico" />

    <StandardDirectory Id="ProgramFiles64Folder">
      <Directory Id="INSTALLFOLDER" Name="SecureMessage" />
    </StandardDirectory>
    <StandardDirectory Id="ProgramMenuFolder">
      <Directory Id="AppProgramMenuFolder" Name="SecureMessage" />
    </StandardDirectory>

    <ComponentGroup Id="AppFiles" Directory="INSTALLFOLDER">
      <Files Include="$($frozenDir -replace '/', '\')\SecureMessage\**" />
    </ComponentGroup>

    <Component Id="StartMenuShortcut" Directory="AppProgramMenuFolder" Guid="*">
      <Shortcut Id="StartMenuShortcut" Name="SecureMessage"
                Target="[INSTALLFOLDER]SecureMessage.exe" WorkingDirectory="INSTALLFOLDER"
                Icon="AppIcon.ico" />
      <RemoveFolder Id="RemoveAppProgramMenuFolder" On="uninstall" />
      <RegistryValue Root="HKCU" Key="Software\SecureMessage" Name="installed" Type="integer"
                      Value="1" KeyPath="yes" />
    </Component>
    <Component Id="DesktopShortcut" Directory="INSTALLFOLDER" Guid="*">
      <Shortcut Id="DesktopShortcut" Name="SecureMessage" Directory="DesktopFolder"
                Target="[INSTALLFOLDER]SecureMessage.exe" WorkingDirectory="INSTALLFOLDER"
                Icon="AppIcon.ico" />
      <RegistryValue Root="HKCU" Key="Software\SecureMessage" Name="desktop_shortcut" Type="integer"
                      Value="1" KeyPath="yes" />
    </Component>
    <StandardDirectory Id="DesktopFolder" />

    <Feature Id="Main" Title="SecureMessage" Level="1">
      <ComponentGroupRef Id="AppFiles" />
      <ComponentRef Id="StartMenuShortcut" />
      <ComponentRef Id="DesktopShortcut" />
    </Feature>

    <ui:WixUI Id="WixUI_InstallDir" InstallDirectory="INSTALLFOLDER" />
  </Package>
</Wix>
"@ | Set-Content -Encoding utf8 $wxs

$msi = Join-Path $dist "SecureMessage-$version-x64.msi"
Say "Building $msi ..."
wix build $wxs -ext WixToolset.UI.wixext -arch x64 -out $msi

Say "Done: $msi"
Get-Item $msi | Format-List Name, Length, FullName
