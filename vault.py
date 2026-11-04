"""
File Vault — encrypt (and optionally hide) any file: pictures, video, audio, archives,
PDFs, anything.

    file bytes -> pack (name + zlib) -> seal()  [password, or post-quantum key]
              -> plain .smx file, OR hidden inside a picture (PNG) or an audio file (WAV)

Decrypting reverses this and restores the original file, byte for byte, under its
original name.
"""

from __future__ import annotations

import mimetypes
import struct
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import numpy as np

import crypto_engine as ce
import stego_image as si
import voice_codec as vc

MAGIC = b"SMFP"
_HDR = "<4sBH"                        # magic, flags (bit0 = zlib-compressed), name length
_HDR_LEN = struct.calcsize(_HDR)
MAX_FILE_SIZE = 300 * 1024 * 1024     # kept in memory while encrypting
PLAIN_SUFFIX = ".smx"


class VaultError(ValueError):
    pass


@dataclass
class UnpackedFile:
    name: str
    mime: str
    data: bytes

    @property
    def suggested_suffix(self) -> str:
        return Path(self.name).suffix or ""


# ---------------------------------------------------------------------------
# Packing (filename + content, independent of how it's later protected/hidden)
# ---------------------------------------------------------------------------

def _truncate_name(name: str, max_bytes: int = 255) -> bytes:
    """Encode to UTF-8 within `max_bytes`, trimming whole characters so the
    result is never cut in the middle of a multi-byte sequence."""
    encoded = name.encode("utf-8")
    while len(encoded) > max_bytes:
        name = name[:-1]
        encoded = name.encode("utf-8")
    return encoded


def _safe_name(name: str) -> str:
    """Basename only, so a crafted or legacy filename can't point outside the
    folder the person picks in the Save dialog (e.g. '../../x' or 'C:\\x')."""
    name = name.replace("\\", "/")
    base = Path(name).name.strip()
    return base or "file"


def pack_file(path: str) -> bytes:
    p = Path(path)
    try:
        data = p.read_bytes()
    except OSError as exc:
        raise VaultError(f"Couldn't read the file: {exc}") from exc
    if len(data) > MAX_FILE_SIZE:
        raise VaultError(f"That file is larger than {MAX_FILE_SIZE // (1024 * 1024)} MB.")
    if not data:
        raise VaultError("That file is empty.")
    name = _truncate_name(p.name)
    compressed = zlib.compress(data, 6)
    use_compressed = len(compressed) < len(data)
    header = struct.pack(_HDR, MAGIC, 1 if use_compressed else 0, len(name)) + name
    return header + (compressed if use_compressed else data)


def unpack_file(container: bytes) -> UnpackedFile:
    if len(container) < _HDR_LEN:
        raise VaultError("The decrypted data is damaged.")
    magic, flags, name_len = struct.unpack(_HDR, container[:_HDR_LEN])
    if magic != MAGIC or len(container) < _HDR_LEN + name_len:
        raise VaultError("The decrypted data is damaged.")
    name = _safe_name(container[_HDR_LEN:_HDR_LEN + name_len].decode("utf-8", "replace"))
    body = container[_HDR_LEN + name_len:]
    try:
        data = zlib.decompress(body) if flags & 1 else body
    except zlib.error as exc:
        raise VaultError("The decrypted data is damaged.") from exc
    mime, _ = mimetypes.guess_type(name)
    return UnpackedFile(name, mime or "application/octet-stream", data)


def sealed_len_estimate(packed_len: int, post_quantum: bool) -> int:
    overhead = (6 + ce.KEY_ID_LEN + ce.KEM_CT_LEN + ce.X_LEN if post_quantum
               else 6 + ce.SALT_LEN) + ce.NONCE_LEN + 16
    return packed_len + overhead


# ---------------------------------------------------------------------------
# Encrypt / decrypt (works the same whatever the disguise)
# ---------------------------------------------------------------------------

def seal_packed(packed: bytes, *, algorithm: ce.Algorithm = ce.Algorithm.AES_256_GCM,
                password: Optional[str] = None, recipient: Optional[bytes] = None) -> bytes:
    return ce.seal(packed, algorithm=algorithm, password=password, recipient=recipient)


def load_sealed(data: bytes) -> bytes:
    """Find the encrypted data in `data`, whatever disguises it (or none)."""
    try:
        arr = si.load_cover_image(data)
    except si.ImageStegoError:
        arr = None
    if arr is not None:
        try:
            return si.extract(arr)
        except si.ImageStegoError as exc:
            if "doesn't contain" not in str(exc):
                raise VaultError(str(exc)) from exc
    try:
        samples, _rate, _ch = vc.read_wav(data)
    except vc.VoiceError:
        samples = None
    if samples is not None:
        try:
            return vc.extract(samples)
        except vc.VoiceError as exc:
            if "doesn't contain" not in str(exc):
                raise VaultError(str(exc)) from exc
    if data[:4] == ce.MAGIC:
        return data
    raise VaultError("Couldn't find any encrypted or hidden data in that file.")


def inspect(data: bytes) -> tuple[bytes, ce.BlobInfo]:
    sealed = load_sealed(data)
    try:
        return sealed, ce.inspect_blob(sealed)
    except ValueError as exc:
        raise VaultError(str(exc)) from exc


def decrypt_sealed(sealed: bytes, *, password: Optional[str] = None,
                   secret: Optional[bytes] = None) -> UnpackedFile:
    container = ce.open_sealed(sealed, password=password, secret=secret)
    return unpack_file(container)


# ---------------------------------------------------------------------------
# Disguises
# ---------------------------------------------------------------------------

def plain_bytes(sealed: bytes) -> bytes:
    return sealed


def hide_in_image(sealed: bytes, cover: np.ndarray) -> bytes:
    stego = si.embed(cover, sealed)
    return si.image_to_png(stego)


def hide_in_audio(sealed: bytes, cover: np.ndarray, rate: int) -> tuple[bytes, int]:
    """Returns (wav_bytes, repeats) — `repeats` is 1 unless the cover had to be looped to fit."""
    fitted, bits, repeats = vc.prepare_cover(cover, len(sealed))
    stego = vc.embed(fitted, sealed, bits)
    return vc.wav_bytes(stego, rate), repeats
