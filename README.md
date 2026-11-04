# SecureMessage

Linux desktop app for encrypting short messages between trusted colleagues.
GTK4 + libadwaita, styled after macOS.

## What it does

One window, two columns:

| Left | Right |
|------|-------|
| Message, password, algorithm, **Decrypt** / **Encrypt** | Result with **Copy** |

* **Encrypt** takes plain text. **Decrypt** takes an encrypted message and tells you
  clearly if the text isn't one, or if the password is wrong.
* Enter in the password field runs whichever action the text calls for.
* Pasting or typing an encrypted message shows an "Encrypted text detected" hint.
* Line-wrapped encrypted messages decrypt fine.
* Responsive: below ~760 px the columns stack and the page scrolls; works with very long texts.
* Persian / Arabic text is aligned right-to-left automatically.

## Appearance

* Light and dark themes; follows the system until you pick one with the sun/moon button
  (your choice is remembered in `~/.config/securemessage/settings.json`).
* Symbolic SVG icons (`icons/hicolor/scalable/actions/sm-*-symbolic.svg`) that GTK
  recolours for the active theme.
* Toasts in four kinds, told apart by colour *and* icon shape:
  success (check), info (i), warning (triangle), error (cross).
  An identical toast is never shown twice; hovering pauses its timer.
* Buttons give feedback: Copy / Paste turn into a green check, Encrypt / Decrypt show a
  spinner while working and a check when finished; invalid fields get a red ring.

## Shortcuts

`Ctrl+E` encrypt · `Ctrl+D` decrypt · `Ctrl+Shift+C` copy result · `Ctrl+Q` quit

## Files — encrypt or hide ANY file

Open **Files** (next to Voice in the Message card) to encrypt, or hide, any kind of file —
pictures, video, audio, ZIP archives, PDFs, anything.

1. **Choose a file.** Any type, any content.
2. **Protect with** a password, or a post-quantum key (same as text and voice).
3. **The encrypted file should be**:
   - **A plain file** — saved as `name-encrypted.smx`. Anyone can see it's an encrypted
     SecureMessage file, just not open it without the key.
   - **Hidden in a picture** — choose any picture; the result is a PNG that looks identical
     to the eye, with the encrypted file hidden in its low pixel bits.
   - **Hidden in audio** — choose any audio file (WAV, MP3, OGG, FLAC, M4A…); the result
     plays that audio normally, with the encrypted file hidden inside.

**Decrypting** works the same way everywhere in the app: open the file first — SecureMessage
works out whether it's a plain encrypted file, or a picture or audio file with something hidden
inside — and *then* asks for the password or key passphrase. The original file is restored
byte-for-byte under its original name. A file you don't have the key for, or one with nothing
hidden in it, gets a clear explanation instead of a password prompt.

This is how you can put a photo inside a video-sized audio file, a voice recording inside a
picture, a ZIP or a PDF inside a song, or just encrypt a file plainly — any file type, in any
combination.

**Keep hidden files lossless.** Pictures must stay PNG; audio must stay WAV. Re-saving as JPEG/
MP3, or sending through an app that recompresses images or audio, destroys the hidden data.
A picture or audio file that's too small for what you're hiding will say so and tell you how
much room it has.

**About hiding things inside video:** SecureMessage does not hide data inside video files. Video
is heavily compressed; hiding data losslessly would mean re-encoding it uncompressed, producing
a huge file that plainly isn't the original video — which defeats the point of disguising it.
Videos work fine as the file *being* encrypted (point 1 above), or as the payload hidden inside
a picture or audio file if they're small enough.

## Packaging (for distributing SecureMessage)

Scripts in `packaging/` build installable packages from this source tree. Both were tested
during development as described below — read that before relying on either one blind.

### Linux — `packaging/linux-build.sh`

```bash
./packaging/linux-build.sh            # builds whichever of deb/rpm/appimage it has tools for
./packaging/linux-build.sh deb        # just one target
./packaging/linux-build.sh all        # fail instead of silently skipping a missing tool
```

Produces, in `dist/linux/`:
- **`.deb`** — needs `dpkg-dev` (present on virtually every Debian/Ubuntu system already).
- **`.rpm`** — needs `rpm-build`.
- **AppImage** — needs network access the first time (downloads `linuxdeploy`, its GTK
  plugin, and `appimagetool`) and, at build time only, GTK4's *development* package
  (`libgtk-4-dev` / `gtk4-devel`) so the GTK bundler plugin can find `gtk4.pc`.

These were built and actually run end-to-end while developing this feature: the `.deb` was
installed with `apt install ./securemessage_*.deb` and launched from `/usr/bin/securemessage`;
the `.rpm`'s metadata and file list were inspected with `rpm -qpi`/`-qpl`; the AppImage was
extracted and run directly. The AppImage build also turned up a real bug in the upstream GTK
bundling plugin (its `gdk-pixbuf` loader cache doesn't resolve inside the AppImage, so every
SVG icon silently failed to load) — the script works around it by patching the loader
environment before the final package is produced; this was verified by running the patched
AppImage and confirming the icons render correctly.

None of the three bundle Python, GTK or libadwaita: those come from the distribution's own
package manager (each package declares them as dependencies), and SecureMessage installs its
own pure-Python packages (cryptography, numpy, pqcrypto, ...) into a private folder the first
time it runs, exactly as it does on a plain `python3 main.py` run — "dependency ها را خودش نصب
کند" is already how the app behaves, packaged or not.

### Windows — `packaging/windows-build-installer.ps1`

```powershell
powershell -ExecutionPolicy Bypass -File packaging\windows-build-installer.ps1
```

Produces `dist\windows\SecureMessage-<version>-x64.msi`: a normal double-click installer with
a Start Menu entry, a desktop shortcut and an uninstaller.

**Important — this script has not been run on an actual Windows machine**, because none was
available while building it. It installs MSYS2 (the real, native build of GTK4 + libadwaita
for Windows — the same approach GIMP and Inkscape ship with), freezes the app with PyInstaller
into a self-contained folder, and packages that with the WiX Toolset. The logic was reasoned
through carefully and a real bug was already caught and fixed on review (a PowerShell
here-string used `` ` `` line continuations for a command that actually runs inside *bash*,
which treats `` ` `` as command substitution, not continuation — fixed to single-line commands).
But static review only goes so far: **run it on Windows, and if it stops with an error, share
that error** rather than assuming the whole approach is broken — most likely it's a specific
package name or path that needs adjusting, not the overall design.

Why not a native Win32 rewrite instead? Because that would mean a second, complete user
interface — the main window, Voice, Files, Keys — written from scratch in a different toolkit,
impossible to verify without a Windows machine, and a permanent maintenance burden keeping two
UIs in sync. Bundling the real GTK runtime is the same thing production GTK apps actually ship
on Windows, and it means the Linux and Windows builds are provably the same, already-tested code.

## Run

```bash
cd SecureMessage
python3 main.py
```

Requires GTK 4, libadwaita ≥ 1.4 and librsvg's pixbuf loader (installed automatically on
first run for common distros). If you hit rendering problems on an old GPU driver, try
`GSK_RENDERER=cairo python3 main.py`.

## Protection methods

| Method | How it works | Quantum-safe? |
|--------|--------------|---------------|
| **Password** | Argon2id -> AES-256-GCM or ChaCha20-Poly1305 | Yes at the cipher level: Grover's algorithm only halves a 256-bit key to ~128 bits. Strength depends on a good password (12+ characters). |
| **Post-quantum key** | Hybrid key encapsulation to the recipient's public key: **ML-KEM-768** (NIST FIPS 203) + **X25519**, combined with HKDF-SHA256, then the same AEAD ciphers | Yes. An attacker must break both ML-KEM and X25519. |

Quantum computers threaten public-key schemes (RSA, ECDH) – not 256-bit symmetric ciphers.
That's why the public-key mode uses ML-KEM. No cryptography is "unbreakable"; these are the
current NIST-standardised, quantum-resistant choices.

Keys live in `~/.local/share/securemessage/keys.json` (mode 0600). Your secret key is stored
encrypted with your passphrase. Press **Keys** to create a key pair, copy your public key and
add contacts.

## Voice

Open **Voice** (microphone button in the Message card).

* **Record** with a live level meter, then play it back.
* **Speech to text** runs offline (Whisper "base" via faster-whisper). It is installed into
  a private folder on first use and downloads a ~145 MB model once. Choose the spoken language
  (Persian, English, ...). *Transcribe and encrypt text* does both in one step.
* **Encrypt voice**: under *Protect with* choose a **Password**, or – optionally – a
  **Post-quantum key** (ML-KEM-768 + X25519) for a contact or yourself. The recording is locked and
  hidden inside a completely artificial, synthesised voice. The result is a normal WAV that plays
  robotic speech.
* **The encrypted file should sound like**: keep the default *Artificial voice*, or choose
  **My own audio** – a song, a talk, your own voice. The encrypted file then plays *your* audio
  (same sample rate and channels) with the hidden voice inside. WAV works everywhere; MP3, OGG, FLAC
  and M4A are decoded through GStreamer. The app tells you whether the recording fits; if your audio is
  shorter than needed it is repeated. The result is always a lossless WAV, so it is larger than an MP3.
* **Decrypt a voice**: first **open** the encrypted file (or drop it on the card). The app detects what
  protects it and *then* asks for the password – or the passphrase of your key. The original recording
  is restored bit for bit. If the voice was made for someone else's key, the app tells you instead of
  asking for a password.
* **Save** the original, the encrypted file, or both.

Important: the hidden data lives in the low bits of the WAV. Keep it lossless – converting to
MP3/OGG or sending it through an app that re-encodes audio destroys the hidden message.
The artificial voice only disguises the file; the security comes from the encryption.

## Run

```bash
cd SecureMessage
python3 main.py
```

Requires GTK 4, libadwaita >= 1.4, librsvg's pixbuf loader, and for Voice GStreamer
(`gir1.2-gstreamer-1.0 gstreamer1.0-plugins-good gstreamer1.0-pipewire` on Debian/Ubuntu).
Python packages (`numpy`, `pqcrypto`, ...) are installed automatically into a private folder, so
this works on distros that block `pip install --user`. If you hit rendering problems on an old
GPU driver, try `GSK_RENDERER=cairo python3 main.py`.

## Structure

```
main.py            main window + app logic
voice_studio.py    record / transcribe / voice encryption window
files_studio.py    encrypt / hide / decrypt any file (images, video, audio, archives, PDFs…)
vault.py           generic file packing + encryption (used by the Files window)
stego_image.py     hides encrypted data inside a picture (lossless PNG)
packaging/         build scripts: linux-build.sh, windows-build-installer.ps1
.gitignore         keeps build output, caches and local keys/settings out of git
keys_window.py     post-quantum key manager
keystore.py        identities and contacts (secret keys encrypted)
voice_codec.py     artificial-voice synthesis + hiding / recovering data
audio.py           GStreamer recorder and player
stt.py             offline speech-to-text
protection.py      parameters shared by text and voice
pylib.py           private pip folder (PEP 668 safe)
theme.py           light / dark palettes and CSS
widgets.py         icons, feedback button, toast host
settings.py        remembered theme and algorithm
crypto_engine.py   encryption
bootstrap.py       dependency setup
icons/             symbolic icons + app icon

Developer: Mehran Ghadirian — https://github.com/mehranqadirian
(the profile picture in Help is downloaded once from GitHub and cached; offline it shows initials)
```
