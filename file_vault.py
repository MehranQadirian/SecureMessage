"""
File Vault -- hide ANY file inside ANY other file, encrypted.

    secret file (image, video, audio, voice note, zip, pdf, anything, any size)
        -> random 256-bit file key
        -> split into 1 MiB chunks, each sealed with AES-256-GCM / ChaCha20-Poly1305
        -> the file key + original name are sealed with seal()          (real security)
           [password (Argon2id)  or  hybrid ML-KEM-768 + X25519 public key]
        -> stored inside a carrier file (photo, video, song, pdf, zip ...)   (cover)

The carrier keeps opening and playing exactly as before.  Two storage modes, chosen
automatically:

  append   The encrypted payload is written *after* the carrier's own data and located by
           a 16-byte footer.  Image viewers, video / audio players and document readers
           ignore bytes after the end of the media, so PNG, JPEG, GIF, WebP, MP4, MKV, AVI,
           MP3, WAV, FLAC, OGG ... all keep working.  Capacity is unlimited.
  zip      For .zip carriers (a ZIP is found from its END, so appended bytes would break it)
           the payload becomes one stored member of the archive; the archive stays valid.

What this is, and is not
  * Confidentiality + integrity come from the encryption: without the password / secret key
    nobody can read, or undetectably alter, the hidden file or even learn its name and size.
  * It is concealment, not deniability: the footer is recognisable, so someone who knows the
    format (or looks at an unexpectedly large file) can tell that *something* is attached.
  * Anything that RE-ENCODES the carrier (social-media upload, "compress image", video
    transcoding, messenger "photo" mode) drops the hidden data.  Send the carrier as a file.
  * Formats read from the end (PDF, DOCX/XLSX/PPTX, APK, JAR ...) may be rejected by strict
    readers when bytes are appended; see ``carrier_warning``.
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import struct
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Callable, Optional

from cryptography.hazmat.primitives.ciphers.aead import AESGCM, ChaCha20Poly1305

import crypto_engine as ce

PAYLOAD_MAGIC = b"SMVP"
FOOTER_MAGIC = b"SMVAULT1"
_FOOTER = struct.Struct(">8sQ")                 # magic, payload length
FOOTER_LEN = _FOOTER.size                       # 16
ZIP_MEMBER = "_sm/vault.bin"
CHUNK = 1 << 20                                 # 1 MiB of plaintext per sealed chunk
TAG = 16
COPY_BLOCK = 4 << 20
MAX_HEADER = 64 * 1024
FORMAT_VERSION = 1

Progress = Optional[Callable[[int, int], None]]

# Formats that are located from their END: appended bytes can make strict readers refuse them.
_END_ANCHORED = {
    ".pdf": "PDF viewers look for the file's end marker; most repair the file, a few may refuse it.",
    ".docx": "Office files are ZIP archives read from their end; Word may offer to 'repair' it.",
    ".xlsx": "Office files are ZIP archives read from their end; Excel may offer to 'repair' it.",
    ".pptx": "Office files are ZIP archives read from their end; PowerPoint may offer to 'repair' it.",
    ".odt": "OpenDocument files are ZIP archives read from their end and may fail to open.",
    ".ods": "OpenDocument files are ZIP archives read from their end and may fail to open.",
    ".epub": "EPUB files are ZIP archives read from their end and may fail to open.",
    ".apk": "APK files are ZIP archives read from their end; Android may reject the package.",
    ".jar": "JAR files are ZIP archives read from their end and may fail to load.",
}


class VaultError(ValueError):
    """A problem the user can understand (wrong password, not a vault, damaged data ...)."""


# ---------------------------------------------------------------------------
# data types
# ---------------------------------------------------------------------------

@dataclass
class VaultInfo:
    """What can be learned WITHOUT the password: where it is and what protects it."""
    mode: str                         # "append" | "zip"
    payload_len: int
    is_pq: bool
    key_id: Optional[bytes]
    algorithm: str
    header_len: int


@dataclass
class VaultMeta:
    """The hidden file's details (needs the password / secret key)."""
    name: str
    size: int
    algorithm: ce.Algorithm
    key: bytes = b""
    chunk: int = CHUNK


@dataclass
class HideResult:
    out_path: Path
    mode: str
    hidden_name: str
    hidden_size: int
    carrier_size: int
    out_size: int


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------

def carrier_warning(path: str | os.PathLike) -> Optional[str]:
    """A short warning if appending to this kind of file may stop it from opening."""
    return _END_ANCHORED.get(Path(path).suffix.lower())


def safe_name(name: str) -> str:
    """Strip any directory part and control characters: a hidden name must never escape a folder."""
    name = name.replace("\\", "/").split("/")[-1]
    name = "".join(ch for ch in name if ch.isprintable()).strip().strip(".")
    return name[:200] or "hidden-file"


def _nonce(counter: int, final: bool) -> bytes:
    return counter.to_bytes(8, "big") + b"\x00\x00\x00" + (b"\x01" if final else b"\x00")


def _aead(algorithm: ce.Algorithm, key: bytes):
    return AESGCM(key) if algorithm == ce.Algorithm.AES_256_GCM else ChaCha20Poly1305(key)


def _tick(progress: Progress, done: int, total: int) -> None:
    if progress is not None:
        progress(min(done, total), total)


def _is_zip_carrier(path: Path) -> bool:
    return path.suffix.lower() == ".zip" and zipfile.is_zipfile(path)


class _Limited:
    """Read at most `length` bytes of a stream (so a payload never reads into the footer)."""

    def __init__(self, fh: BinaryIO, length: int):
        self.fh, self.left = fh, length

    def read(self, n: int = -1) -> bytes:
        n = self.left if n < 0 else min(n, self.left)
        data = self.fh.read(n)
        self.left -= len(data)
        return data


# ---------------------------------------------------------------------------
# opening a payload for reading
# ---------------------------------------------------------------------------

class _Opened:
    """Context manager: yields (stream limited to the payload, payload length, mode)."""

    def __init__(self, path: str | os.PathLike):
        self.path = Path(path)
        self._closers: list = []

    def __enter__(self):
        p = self.path
        try:
            if _is_zip_carrier(p):
                zf = zipfile.ZipFile(p)
                self._closers.append(zf)
                try:
                    zi = zf.getinfo(ZIP_MEMBER)
                except KeyError:
                    raise VaultError("Nothing is hidden in this archive.") from None
                stream = zf.open(zi)
                self._closers.append(stream)
                return _Limited(stream, zi.file_size), zi.file_size, "zip"
            fh = open(p, "rb")
            self._closers.append(fh)
            size = fh.seek(0, os.SEEK_END)
            if size < FOOTER_LEN + len(PAYLOAD_MAGIC):
                raise VaultError("Nothing is hidden in this file.")
            fh.seek(size - FOOTER_LEN)
            magic, length = _FOOTER.unpack(fh.read(FOOTER_LEN))
            start = size - FOOTER_LEN - length
            if magic != FOOTER_MAGIC or length < len(PAYLOAD_MAGIC) + 4 or start < 0:
                raise VaultError("Nothing is hidden in this file.")
            fh.seek(start)
            return _Limited(fh, length), length, "append"
        except OSError as exc:
            self.__exit__()
            raise VaultError(f"Couldn't read the file: {exc.strerror or exc}") from exc
        except Exception:
            self.__exit__()
            raise

    def __exit__(self, *exc):
        while self._closers:
            try:
                self._closers.pop().close()
            except Exception:  # pragma: no cover
                pass
        return False


def _read_header(stream: _Limited, payload_len: int) -> bytes:
    if stream.read(4) != PAYLOAD_MAGIC:
        raise VaultError("Nothing is hidden in this file.")
    raw = stream.read(4)
    if len(raw) != 4:
        raise VaultError("The hidden data is incomplete.")
    (hlen,) = struct.unpack(">I", raw)
    if hlen > MAX_HEADER or 8 + hlen + TAG > payload_len:
        raise VaultError("The hidden data is damaged.")
    header = stream.read(hlen)
    if len(header) != hlen:
        raise VaultError("The hidden data is incomplete.")
    return header


def inspect_carrier(path: str | os.PathLike) -> Optional[VaultInfo]:
    """Return what protects the file hidden in `path`, or None if nothing is hidden."""
    try:
        with _Opened(path) as (stream, length, mode):
            header = _read_header(stream, length)
    except VaultError:
        return None
    try:
        info = ce.inspect_blob(header)
    except ValueError:
        return None
    return VaultInfo(mode, length, info.is_pq, info.key_id, info.algorithm.value, len(header))


# ---------------------------------------------------------------------------
# hiding
# ---------------------------------------------------------------------------

def _emit_payload(write: Callable[[bytes], object], header: bytes, src: BinaryIO, key: bytes,
                  algorithm: ce.Algorithm, expected: int, progress: Progress,
                  done0: int, total: int) -> int:
    """Write the payload; returns its length. The header hash is bound into every chunk."""
    aad = hashlib.sha256(header).digest()
    aead = _aead(algorithm, key)
    n = 0
    for part in (PAYLOAD_MAGIC, struct.pack(">I", len(header)), header):
        write(part)
        n += len(part)
    counter, consumed = 0, 0
    cur = src.read(CHUNK)
    while True:
        nxt = src.read(CHUNK) if len(cur) == CHUNK else b""
        consumed += len(cur)
        final = not nxt
        sealed = aead.encrypt(_nonce(counter, final), cur, aad)
        write(sealed)
        n += len(sealed)
        counter += 1
        _tick(progress, done0 + consumed, total)
        if final:
            break
        cur = nxt
    if consumed != expected:
        raise VaultError("The file changed while it was being encrypted. Try again.")
    return n


def hide(carrier: str | os.PathLike, secret: str | os.PathLike, out: str | os.PathLike, *,
         algorithm: ce.Algorithm = ce.Algorithm.AES_256_GCM,
         password: Optional[str] = None, recipient: Optional[bytes] = None,
         progress: Progress = None) -> HideResult:
    """Encrypt `secret` and store it inside a copy of `carrier`, written to `out`."""
    carrier, secret, out = Path(carrier), Path(secret), Path(out)
    if not carrier.is_file():
        raise VaultError("Choose the file to hide it in (the carrier).")
    if not secret.is_file():
        raise VaultError("Choose the file you want to hide.")
    if not password and recipient is None:
        raise VaultError("A password or a recipient key is required.")
    if out.exists() and out.resolve() in (carrier.resolve(), secret.resolve()):
        raise VaultError("Save the result under a new name, not over one of the source files.")
    carrier_size, secret_size = carrier.stat().st_size, secret.stat().st_size
    if carrier_size == 0:
        raise VaultError("The carrier file is empty.")
    if inspect_carrier(carrier) is not None:
        raise VaultError("This carrier already hides a file. Use a clean copy of it.")

    key = secrets.token_bytes(32)
    meta = {"v": FORMAT_VERSION, "name": safe_name(secret.name), "size": secret_size,
            "alg": algorithm.value, "chunk": CHUNK, "key": key.hex()}
    header = ce.seal(json.dumps(meta, separators=(",", ":")).encode("utf-8"),
                     algorithm=algorithm, password=password, recipient=recipient)

    total = carrier_size + secret_size
    out.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=out.parent, prefix=f".{out.name}.", suffix=".part")
    os.close(fd)
    try:
        mode = "zip" if _is_zip_carrier(carrier) else "append"
        with open(tmp, "wb") as dst, open(carrier, "rb") as cf:
            copied = 0
            while True:
                block = cf.read(COPY_BLOCK)
                if not block:
                    break
                dst.write(block)
                copied += len(block)
                _tick(progress, copied, total)
            if mode == "append":
                with open(secret, "rb") as sf:
                    plen = _emit_payload(dst.write, header, sf, key, algorithm, secret_size,
                                         progress, carrier_size, total)
                dst.write(_FOOTER.pack(FOOTER_MAGIC, plen))
        if mode == "zip":
            with zipfile.ZipFile(tmp, "a") as zf, open(secret, "rb") as sf:
                zi = zipfile.ZipInfo(ZIP_MEMBER)
                zi.compress_type = zipfile.ZIP_STORED
                with zf.open(zi, "w", force_zip64=True) as member:
                    _emit_payload(member.write, header, sf, key, algorithm, secret_size,
                                  progress, carrier_size, total)
        os.chmod(tmp, 0o644)
        os.replace(tmp, out)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return HideResult(out, mode, meta["name"], secret_size, carrier_size, out.stat().st_size)


# ---------------------------------------------------------------------------
# revealing
# ---------------------------------------------------------------------------

def read_meta(path: str | os.PathLike, *, password: Optional[str] = None,
              secret: Optional[bytes] = None) -> VaultMeta:
    """Unlock the header (one Argon2 / ML-KEM operation). Wrong password -> VaultError."""
    with _Opened(path) as (stream, length, _mode):
        header = _read_header(stream, length)
    try:
        raw = ce.open_sealed(header, password=password, secret=secret)
        d = json.loads(raw.decode("utf-8"))
        meta = VaultMeta(name=safe_name(str(d["name"])), size=int(d["size"]),
                         algorithm=ce.Algorithm(d["alg"]), key=bytes.fromhex(d["key"]),
                         chunk=int(d.get("chunk", CHUNK)))
    except ValueError as exc:
        raise VaultError(str(exc)) from exc
    except (KeyError, TypeError):
        raise VaultError("The hidden data is damaged.") from None
    if len(meta.key) != 32 or meta.chunk != CHUNK or meta.size < 0:
        raise VaultError("The hidden data is damaged.")
    return meta


def extract(path: str | os.PathLike, out: str | os.PathLike, *,
            password: Optional[str] = None, secret: Optional[bytes] = None,
            meta: Optional[VaultMeta] = None, progress: Progress = None) -> VaultMeta:
    """Decrypt the hidden file into `out` (written atomically). Returns its details."""
    meta = meta or read_meta(path, password=password, secret=secret)
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=out.parent, prefix=f".{out.name}.", suffix=".part")
    try:
        with os.fdopen(fd, "wb") as dst, _Opened(path) as (stream, length, _mode):
            header = _read_header(stream, length)
            aad = hashlib.sha256(header).digest()
            aead = _aead(meta.algorithm, meta.key)
            remaining = length - 8 - len(header)
            if remaining < TAG:
                raise VaultError("The hidden data is incomplete.")
            counter, written = 0, 0
            while True:
                n = min(remaining, CHUNK + TAG)
                if n < TAG:
                    raise VaultError("The hidden data is damaged.")
                block = stream.read(n)
                if len(block) != n:
                    raise VaultError("The hidden data is incomplete.")
                remaining -= n
                final = remaining == 0
                try:
                    plain = aead.decrypt(_nonce(counter, final), block, aad)
                except Exception:
                    raise VaultError("The hidden data is damaged or has been changed.") from None
                dst.write(plain)
                written += len(plain)
                counter += 1
                _tick(progress, written, max(meta.size, 1))
                if final:
                    break
            if written != meta.size:
                raise VaultError("The hidden data is damaged or has been changed.")
        os.chmod(tmp, 0o600)
        os.replace(tmp, out)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return meta
