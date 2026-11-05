# Secure Message

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
     Secure Message file, just not open it without the key.
   - **Hidden in a picture** — choose any picture; the result is a PNG that looks identical
     to the eye, with the encrypted file hidden in its low pixel bits.
   - **Hidden in audio** — choose any audio file (WAV, MP3, OGG, FLAC, M4A…); the result
     plays that audio normally, with the encrypted file hidden inside.

**Decrypting** works the same way everywhere in the app: open the file first — Secure Message
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

**About hiding things inside video:** Secure Message does not hide data inside video files. Video
is heavily compressed; hiding data losslessly would mean re-encoding it uncompressed, producing
a huge file that plainly isn't the original video — which defeats the point of disguising it.
Videos work fine as the file *being* encrypted (point 1 above), or as the payload hidden inside
a picture or audio file if they're small enough.

## Packaging (for distributing Secure Message)

Releases are built automatically by `.github/workflows/release.yml` whenever `main` is pushed
and `APP_VERSION` in `main.py` has no release yet. The scripts it uses live in `packaging/`
and can be run by hand too.

| File in the release | For | Built by |
|---|---|---|
| `Secure-Message-<version>-Win-x64.msi` | Windows 10/11, 64-bit | `windows-build-installer.ps1` |
| `Secure-Message-<version>-amd64.deb` | Debian / Ubuntu family, 64-bit Intel/AMD | `linux-build.sh deb` |
| `Secure-Message-<version>-x86_64.rpm` | Fedora / RHEL / openSUSE family, 64-bit Intel/AMD | `linux-build.sh rpm` |
| `Secure-Message-<version>-x86_64.AppImage` | any other 64-bit Linux | `linux-build.sh appimage` |

Release notes come from `.github/release-notes.md` (`{{VERSION}}` is filled in automatically).

### Linux — `packaging/linux-build.sh`

```bash
./packaging/linux-build.sh            # builds whichever of deb/rpm/appimage it has tools for
./packaging/linux-build.sh deb        # just one target
./packaging/linux-build.sh all        # fail instead of silently skipping a missing tool
```

- **`.deb`** (`amd64`) needs `dpkg-dev`; **`.rpm`** (`x86_64`) needs `rpm`/`rpm-build`.
- **AppImage** needs network access (downloads `linuxdeploy`, its GTK plugin and
  `appimagetool`) and GTK4's development package (`libgtk-4-dev` / `gtk4-devel`).

The `.deb` and `.rpm` are small on purpose: they carry only the app. GTK, libadwaita, Python
and the Python libraries (`cryptography`, `numpy`, `Pillow`, `argon2`) are declared as
dependencies and installed by the package manager. Only `kyber-py` (pure Python, ~100 KB) is
bundled, so nothing has to be downloaded on first launch. The AppImage additionally bundles
the GTK stack, which is why it is much larger. The command installed by the packages is
`securemessage`; the application menu entry is "Secure Message".

### Windows — `packaging/windows-build-installer.ps1`

```powershell
powershell -ExecutionPolicy Bypass -File packaging\windows-build-installer.ps1
```

Produces `dist\windows\Secure-Message-<version>-Win-x64.msi`. It installs MSYS2 (the native
GTK4 + libadwaita build for Windows), freezes the app with PyInstaller (`--windowed`, so no
console window) into `Secure Message.exe`, bundles the GTK and GStreamer runtime, runs the
frozen app once with `--selftest` (numpy, Pillow, crypto, Voice and Files modules; the build
fails if anything is missing) and packages the result with the WiX Toolset.

Errors in the windowed app are written to `%LOCALAPPDATA%\Secure Message\logs`.

## Updates

*Help → Check for updates…* (and a quiet automatic check a few seconds after start, at most
every 12 hours; switch it off under the same menu) asks the GitHub API for the latest release of
`MehranQadirian/SecureMessage`. If it is newer, the file for this system is picked by its
name (see the table above), downloaded and checked against `SHA256SUMS.txt`, installed, and
the app restarts: Windows runs the `.msi`, an AppImage replaces itself, `.deb`/`.rpm` installs
through `pkexec`. A copy run from source only opens the release page. Logic: `updater.py`;
dialog: `update_window.py`.

## Run
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
