"""
Image steganography: hide an arbitrary (already-encrypted) payload inside a picture.

The picture looks unchanged; the payload lives in the low bits of each pixel channel.
The output is always a lossless PNG — JPEG, WEBP, GIF and other lossy formats would
destroy the hidden bits, so re-saving the result in a lossy format ruins the data.
"""

from __future__ import annotations

import io
import struct
import zlib

import numpy as np
from PIL import Image

_HDR_MAGIC = b"SMIE"
_HDR_FMT = "<4sBBII"                 # magic, version, bits, payload length, crc32
_HDR_LEN = struct.calcsize(_HDR_FMT)
_HDR_BITS = _HDR_LEN * 8             # header uses 1 bit per channel value

MAX_DIMENSION = 8000
MIN_PIXELS = 4096


class ImageStegoError(ValueError):
    pass


def load_cover_image(source) -> np.ndarray:
    """`source`: a path or bytes. Returns an (H, W, 3) uint8 array."""
    try:
        img = Image.open(io.BytesIO(source) if isinstance(source, (bytes, bytearray)) else source)
        img.load()
        img = img.convert("RGB")
    except Exception as exc:
        raise ImageStegoError("That isn't a readable image.") from exc
    if img.width * img.height < MIN_PIXELS:
        raise ImageStegoError("That image is too small to use.")
    if img.width > MAX_DIMENSION or img.height > MAX_DIMENSION:
        raise ImageStegoError("That image is too large.")
    return np.array(img, dtype=np.uint8)


def image_to_png(array: np.ndarray) -> bytes:
    buf = io.BytesIO()
    Image.fromarray(array, "RGB").save(buf, format="PNG", compress_level=6)
    return buf.getvalue()


def capacity_bytes(array: np.ndarray, bits: int) -> int:
    return max(0, (array.size * bits - _HDR_BITS) // 8)


def choose_bits(array: np.ndarray, payload_len: int) -> int:
    for bits in (1, 2, 3):
        if capacity_bytes(array, bits) >= payload_len:
            return bits
    raise ImageStegoError(
        f"This image is too small for that much data (it can hold about "
        f"{capacity_bytes(array, 3) / 1024:.0f} KB). Choose a bigger image or a smaller file.")


def embed(cover: np.ndarray, payload: bytes, bits: int | None = None) -> np.ndarray:
    if bits is None:
        bits = choose_bits(cover, len(payload))
    flat = cover.reshape(-1).astype(np.uint8, copy=True)
    header = struct.pack(_HDR_FMT, _HDR_MAGIC, 1, bits, len(payload), zlib.crc32(payload) & 0xFFFFFFFF)
    hb = np.unpackbits(np.frombuffer(header, dtype=np.uint8))
    if len(hb) > len(flat):
        raise ImageStegoError("That image is too small to use.")
    flat[:len(hb)] = (flat[:len(hb)] & 0xFE) | hb

    pb = np.unpackbits(np.frombuffer(payload, dtype=np.uint8))
    pad = (-len(pb)) % bits
    if pad:
        pb = np.concatenate([pb, np.zeros(pad, dtype=np.uint8)])
    weights = (1 << np.arange(bits - 1, -1, -1)).astype(np.uint8)
    vals = (pb.reshape(-1, bits) * weights).sum(axis=1).astype(np.uint8)
    end = _HDR_BITS + len(vals)
    if end > len(flat):
        raise ImageStegoError("This image is too small for that much data.")
    mask = np.uint8((1 << bits) - 1)
    seg = flat[_HDR_BITS:end]
    flat[_HDR_BITS:end] = (seg & ~mask) | vals
    return flat.reshape(cover.shape)


def extract(stego: np.ndarray) -> bytes:
    flat = stego.reshape(-1).astype(np.uint8)
    if len(flat) < _HDR_BITS:
        raise ImageStegoError("This image doesn't contain hidden data.")
    hb = flat[:_HDR_BITS] & 1
    try:
        magic, _ver, bits, length, crc = struct.unpack(_HDR_FMT, np.packbits(hb).tobytes())
    except struct.error as exc:
        raise ImageStegoError("This image doesn't contain hidden data.") from exc
    if magic != _HDR_MAGIC or not 1 <= bits <= 4:
        raise ImageStegoError("This image doesn't contain hidden data.")
    n = -(-length * 8 // bits)
    if _HDR_BITS + n > len(flat):
        raise ImageStegoError("The hidden data is cut off. Use the original, unedited image.")
    vals = flat[_HDR_BITS:_HDR_BITS + n] & np.uint8((1 << bits) - 1)
    shifts = np.arange(bits - 1, -1, -1).astype(np.uint8)
    bitmat = ((vals[:, None] >> shifts[None, :]) & 1).astype(np.uint8).reshape(-1)
    payload = np.packbits(bitmat).tobytes()[:length]
    if zlib.crc32(payload) & 0xFFFFFFFF != crc:
        raise ImageStegoError("The image was altered or re-saved in a lossy format, so the hidden "
                              "data is damaged. Use the original PNG file.")
    return payload
