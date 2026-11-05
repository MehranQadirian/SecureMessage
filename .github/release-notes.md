## Secure Message {{VERSION}} — first public release

Secure Message encrypts and decrypts **text, voice recordings and any file** between people who trust each other. Everything happens on your own computer — nothing is uploaded anywhere.

### Which file should I download?

| Your computer | Download | How to install |
|---|---|---|
| **Windows 10 / 11** (64-bit) | `Secure-Message-{{VERSION}}-Win-x64.msi` | Double-click it and follow the installer. Start Menu and Desktop shortcuts are created. |
| **Ubuntu, Debian, Linux Mint, Pop!_OS** (64-bit Intel/AMD) | `Secure-Message-{{VERSION}}-amd64.deb` | `sudo apt install ./Secure-Message-{{VERSION}}-amd64.deb` |
| **Fedora, RHEL, Rocky, openSUSE** (64-bit Intel/AMD) | `Secure-Message-{{VERSION}}-x86_64.rpm` | `sudo dnf install ./Secure-Message-{{VERSION}}-x86_64.rpm` |
| **Any other Linux** (64-bit Intel/AMD) | `Secure-Message-{{VERSION}}-x86_64.AppImage` | `chmod +x Secure-Message-{{VERSION}}-x86_64.AppImage` then run it. No installation needed. |

`SHA256SUMS.txt` contains the checksum of every file so you can verify your download.

> **Why are the `.deb` and `.rpm` so small?** They contain only the app. GTK, libadwaita, Python and the Python libraries are installed from your distribution's own repositories (your package manager does this automatically), so they stay up to date with your system. The AppImage and the Windows installer are larger because they carry the GTK interface libraries with them.

### What it can do

- **Text** — encrypt and decrypt messages with a shared password (Argon2id + AES-256-GCM or ChaCha20-Poly1305), or with a post-quantum key pair (ML-KEM-768 + X25519).
- **Voice** — record or open an audio file, encrypt it, and optionally turn it into text on your computer.
- **Files** — encrypt any file, or hide it inside a picture or an audio file so it looks ordinary.
- **Keys** — create your own key pair and keep your contacts' public keys.
- Light and dark themes, Persian / Arabic right-to-left text, keyboard shortcuts (`Ctrl+E` encrypt, `Ctrl+D` decrypt).

### Good to know

- **Windows:** the app opens straight to its window — no console. If something goes wrong, details are written to `%LOCALAPPDATA%\Secure Message\logs`. Windows SmartScreen may show a warning because the installer is not code-signed yet: choose **More info → Run anyway**.
- **Linux:** the command is `securemessage`, and *Secure Message* also appears in your application menu.
- Offline speech-to-text is an optional extra. It downloads a ~145 MB model the first time you use it, and is available when running from Linux packages or from source (not in the Windows installer).
- Your keys and settings stay on your computer (`~/.local/share/securemessage` on Linux, your user profile on Windows). Back up your secret key — nobody, including the developer, can recover a lost one.

### Found a problem?

Please open an issue and include your operating system, the file you installed, and (on Windows) the log file mentioned above.
