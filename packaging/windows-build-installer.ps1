<#
.SYNOPSIS
    Build a self-contained Windows installer (.msi) for Secure Message.

.DESCRIPTION
    *** This script has not been run on an actual Windows machine. ***
    It was written and reasoned through carefully (the same real GTK4 + libadwaita code
    Secure Message already runs and was tested with on Linux, via the standard MSYS2
    runtime that real GTK apps use on Windows — e.g. GIMP, Inkscape), but there is no
    Windows environment available to run and debug it in. Treat the first run as a
    first draft: read any error it gives you, and tell me what went wrong so it can be
    fixed properly, rather than assuming it is broken on your end.

    What it does:
      1. Checks for MSYS2 (installs it with winget if missing and you allow it).
      2. Installs Python, PyGObject, GTK4, libadwaita and GStreamer *inside* MSYS2's
         UCRT64 environment — this is the actual native Windows build of GTK; it is
         not WSL and does not need Linux.
      3. Installs Secure Message's own Python dependencies (cryptography, numpy,
         pqcrypto, ...) into that same environment with pip.
      4. Freezes the application with PyInstaller into a self-contained onedir build
         (Python + GTK + every .dll it needs), so the person who installs the MSI
         needs nothing else — no internet access, no MSYS2, no Python.
      5. Builds an .msi with the WiX Toolset (installed via `dotnet tool install` if
         missing) so double-clicking it installs Secure Message like any other Windows
         application, with a Start Menu shortcut and an uninstaller.

    Run this from an elevated ("Run as Administrator") PowerShell window, from the
    repository's root folder or from packaging\:

        powershell -ExecutionPolicy Bypass -File packaging\windows-build-installer.ps1

    Output: dist\windows\Secure-Message-<version>-Win-x64.msi
#>

[CmdletBinding()]
param(
    [switch]$SkipMsys2Setup,   # reuse an MSYS2 install you already prepared
    [switch]$SkipFreeze        # reuse a PyInstaller build you already have in build\windows
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$appName = "Secure Message"
$dist = Join-Path $root "dist\windows"
$build = Join-Path $root "build\windows"
$msys2 = if ($env:MSYS2_LOCATION) { $env:MSYS2_LOCATION } else { "C:\msys64" }
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
Say "$appName $version — building the Windows installer"

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
$frozenDir = Join-Path $build $appName
$exePath = Join-Path $frozenDir "$appName.exe"
$iconIco = (Join-Path $root "icons\windows\securemessage.ico") -replace '\\', '/'
if (-not $SkipFreeze) {
    Say "Freezing the app with PyInstaller (this takes a few minutes)..."
    $srcUnix = $root -replace '\\', '/'
    $buildUnix = $build -replace '\\', '/'

    # sys.frozen (set automatically by PyInstaller) makes bootstrap.ensure_ready() a no-op
    # (see bootstrap.py): everything needed is already in this bundle, so there is nothing
    # left to install on first run. One line, for the same reason as $mingwPkgs above.
    # --windowed: a GUI program with no console, so no black CMD window appears before the
    # app. Errors go to %LOCALAPPDATA%\Secure Message\logs (see windows-runtime-hook.py).
    # The Voice and Files windows are imported lazily, so every module is listed explicitly.
    $hidden = (@("voice_studio","files_studio","keys_window","stt","audio","vault","stego_image",
                 "voice_codec","pylib","file_vault","file_vault_window","selftest","updater","update_window") |
               ForEach-Object { "--hidden-import $_" }) -join " "
    $pyiArgs = "--noconfirm --clean --windowed --name '$appName' " +
               "--distpath '$buildUnix' --workpath '$buildUnix/pyinstaller-work' " +
               "--icon '$iconIco' " +
               "--collect-all gi --collect-all cairo --collect-all cryptography --collect-all kyber_py " +
               "--collect-all numpy --collect-all PIL --collect-all argon2 " +
               "$hidden " +
               "--runtime-hook '$srcUnix/packaging/windows-runtime-hook.py' " +
               "--add-data 'icons;icons' main.py"
    Run-InMsys2 "cd '$srcUnix' && /ucrt64/bin/python.exe -m PyInstaller $pyiArgs"

    Say "Copying the GTK4/libadwaita runtime (DLLs, icons, GSettings schemas) next to the app..."
    $gtkBin = "$msys2\ucrt64\bin"
    $gtkShare = "$msys2\ucrt64\share"

    # Bundle GObject Introspection typelibs (GTK4, GDK4, Adwaita, GLib...)
    $giTypelib = "$msys2\ucrt64\lib\girepository-1.0"
    $giDest = "$frozenDir\_internal\lib\girepository-1.0"

    if (-not (Test-Path $giTypelib)) {
        throw "GObject Introspection typelib directory not found: $giTypelib"
    }

    New-Item -ItemType Directory -Force -Path $giDest | Out-Null

    $typelibs = @(Get-ChildItem "$giTypelib\*.typelib")

    if ($typelibs.Count -eq 0) {
        throw "No GObject Introspection typelibs found."
    }

    Copy-Item "$giTypelib\*.typelib" -Destination $giDest -Force

    Say "Copied $($typelibs.Count) GI typelib files."

    Copy-Item "$gtkBin\*.dll" -Destination "$frozenDir\_internal" -Force
    New-Item -ItemType Directory -Force -Path "$frozenDir\_internal\lib\gdk-pixbuf-2.0" | Out-Null
    Copy-Item "$msys2\ucrt64\lib\gdk-pixbuf-2.0\*" -Destination "$frozenDir\_internal\lib\gdk-pixbuf-2.0" -Recurse -Force
    New-Item -ItemType Directory -Force -Path "$frozenDir\_internal\share" | Out-Null
    foreach ($d in @("glib-2.0", "icons", "gtk-4.0")) {
        if (Test-Path "$gtkShare\$d") {
            Copy-Item "$gtkShare\$d" -Destination "$frozenDir\_internal\share\$d" -Recurse -Force
        }
    }
    # GStreamer plugins (needed by the Voice window and for audio files)
    $gstPlugins = "$msys2\ucrt64\lib\gstreamer-1.0"
    if (-not (Test-Path $gstPlugins)) { throw "GStreamer plugins not found: $gstPlugins" }
    $gstDest = "$frozenDir\_internal\lib\gstreamer-1.0"
    New-Item -ItemType Directory -Force -Path $gstDest | Out-Null
    Copy-Item "$gstPlugins\*.dll" -Destination $gstDest -Force
    Say "Copied $((Get-ChildItem "$gstDest\*.dll").Count) GStreamer plugins."
} else {
    Say "Skipping the freeze step (-SkipFreeze given); reusing $frozenDir"
}
if (-not (Test-Path $exePath)) {
    throw "PyInstaller didn't produce '$appName.exe' — check the log above."
}

# ---------------------------------------------------------------------------
# 2b) Self test: run the frozen app once, headless, so a bundle in which the Voice or
#     Files windows can't load fails the build here instead of on a user's computer.
# ---------------------------------------------------------------------------
Say "Self-testing the frozen app (numpy, Pillow, crypto, Voice and Files modules)..."
$report = Join-Path $build "selftest-report.txt"
Remove-Item $report -ErrorAction SilentlyContinue
$proc = Start-Process -FilePath $exePath -ArgumentList @("--selftest", "`"$report`"") -Wait -PassThru
if (Test-Path $report) { Get-Content $report | ForEach-Object { Write-Host "    $_" } }
else { Write-Host "    (no report was written)" }
if ($proc.ExitCode -ne 0) {
    throw "The frozen app failed its self test (exit code $($proc.ExitCode)). See the report above."
}

# Same checks again in the load order of the real app (GTK first, NumPy second, no preload):
# this is informational. It shows whether the order itself was what broke NumPy on some PCs.
Say "Diagnostic: GTK-first load order without the NumPy preload (informational only)..."
$report2 = Join-Path $build "selftest-report-gtk-first.txt"
Remove-Item $report2 -ErrorAction SilentlyContinue
$env:SM_NO_NUMPY_PRELOAD = "1"
$proc2 = Start-Process -FilePath $exePath -ArgumentList @("--selftest", "`"$report2`"", "--gtk-first") -Wait -PassThru
Remove-Item Env:\SM_NO_NUMPY_PRELOAD -ErrorAction SilentlyContinue
if (Test-Path $report2) { Get-Content $report2 | ForEach-Object { Write-Host "    $_" } }
Say "Diagnostic finished with exit code $($proc2.ExitCode)."

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
wix eula accept wix7
if ($LASTEXITCODE -ne 0) {
    throw "WiX EULA acceptance failed."
}

wix extension add -g WixToolset.UI.wixext/7.0.1
if ($LASTEXITCODE -ne 0) {
    throw "WiX UI extension installation failed."
}

$productId = [guid]::NewGuid().ToString()
$upgradeCode = "6E3B1A2E-6B1E-4B8C-9D2B-5E7A8C9F0A11"   # keep this fixed across versions
$wxs = Join-Path $build "SecureMessage.wxs"

@"
<?xml version="1.0" encoding="UTF-8"?>
<Wix xmlns="http://wixtoolset.org/schemas/v4/wxs" xmlns:ui="http://wixtoolset.org/schemas/v4/wxs/ui">
  <Package Name="$appName" Manufacturer="Mehran Ghadirian" Version="$version"
           UpgradeCode="$upgradeCode" Language="1033" Scope="perMachine">

    <MajorUpgrade DowngradeErrorMessage="A newer version of $appName is already installed." />
    <MediaTemplate EmbedCab="yes" />
    <Icon Id="AppIcon.ico" SourceFile="$($iconIco -replace '/', '\')" />
    <Property Id="ARPPRODUCTICON" Value="AppIcon.ico" />

    <StandardDirectory Id="ProgramFiles64Folder">
      <Directory Id="INSTALLFOLDER" Name="$appName" />
    </StandardDirectory>
    <StandardDirectory Id="ProgramMenuFolder">
      <Directory Id="AppProgramMenuFolder" Name="$appName" />
    </StandardDirectory>

    <ComponentGroup Id="AppFiles" Directory="INSTALLFOLDER">
      <Files Include="$($frozenDir -replace '/', '\')\**" />
    </ComponentGroup>

    <Component Id="StartMenuShortcut" Directory="AppProgramMenuFolder" Guid="*">
      <Shortcut Id="StartMenuShortcut" Name="$appName"
                Target="[INSTALLFOLDER]$appName.exe" WorkingDirectory="INSTALLFOLDER"
                Icon="AppIcon.ico" />
      <RemoveFolder Id="RemoveAppProgramMenuFolder" On="uninstall" />
      <RegistryValue Root="HKCU" Key="Software\SecureMessage" Name="installed" Type="integer"
                      Value="1" KeyPath="yes" />
    </Component>
    <Component Id="DesktopShortcut" Directory="INSTALLFOLDER" Guid="*">
      <Shortcut Id="DesktopShortcut" Name="$appName" Directory="DesktopFolder"
                Target="[INSTALLFOLDER]$appName.exe" WorkingDirectory="INSTALLFOLDER"
                Icon="AppIcon.ico" />
      <RegistryValue Root="HKCU" Key="Software\SecureMessage" Name="desktop_shortcut" Type="integer"
                      Value="1" KeyPath="yes" />
    </Component>
    <StandardDirectory Id="DesktopFolder" />

    <Feature Id="Main" Title="$appName" Level="1">
      <ComponentGroupRef Id="AppFiles" />
      <ComponentRef Id="StartMenuShortcut" />
      <ComponentRef Id="DesktopShortcut" />
    </Feature>

    <ui:WixUI Id="WixUI_InstallDir" InstallDirectory="INSTALLFOLDER" />
  </Package>
</Wix>
"@ | Set-Content -Encoding utf8 $wxs

$msi = Join-Path $dist "Secure-Message-$version-Win-x64.msi"
Say "Building $msi ..."
wix build $wxs -ext WixToolset.UI.wixext -arch x64 -out $msi

if ($LASTEXITCODE -ne 0) {
    throw "WiX build failed with exit code $LASTEXITCODE."
}

if (-not (Test-Path -LiteralPath $msi -PathType Leaf)) {
    throw "WiX build reported success, but the MSI file was not created: $msi"
}

Say "Successfully created: $msi"
Get-Item -LiteralPath $msi | Format-List Name, Length, FullName
