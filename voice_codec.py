"""
Voice encryption.

  recording (16 kHz mono PCM)
      -> zlib -> seal()  [password, or hybrid ML-KEM-768 + X25519]      (real security)
      -> hidden inside a completely artificial synthetic voice          (cover)

The result is an ordinary stereo WAV that plays a robotic, computer-made voice.
The ciphertext sits in the two least-significant bits of every sample (about
-84 dB, far below anything audible). Decrypting reads those bits back, opens the
sealed data and restores the original recording bit-for-bit.

The security comes entirely from seal(); the synthetic voice only disguises the file.
Keep the file lossless: converting it to MP3/OGG or sending it through an app that
re-encodes audio destroys the hidden data.
"""

from __future__ import annotations

import io
import struct
import wave
import zlib
from dataclasses import dataclass
from typing import Optional

import numpy as np

import crypto_engine as ce

SPEECH_RATE = 16000
COVER_RATE = 44100
EMBED_BITS = 2
_HDR_MAGIC = b"SMVE"
_HDR_FMT = "<4sBBII"                       # magic, version, bits, payload length, crc32
_HDR_LEN = struct.calcsize(_HDR_FMT)       # 14 bytes
_HDR_SAMPLES = 128                         # header lives in the first 128 samples, 1 LSB each
_PAYLOAD_MAGIC = b"SMV1"
MAX_SECONDS = 15 * 60


class VoiceError(ValueError):
    pass


# ---------------------------------------------------------------------------
# WAV helpers
# ---------------------------------------------------------------------------

def read_wav(source) -> tuple[np.ndarray, int, int]:
    """Return (samples int16 shaped (frames, channels), rate, channels). `source`: path or bytes."""
    try:
        fh = io.BytesIO(source) if isinstance(source, (bytes, bytearray)) else source
        with wave.open(fh, "rb") as w:
            ch, width, rate, n = w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()
            raw = w.readframes(n)
    except (wave.Error, EOFError, OSError) as exc:
        raise VoiceError("That isn't a readable WAV file.") from exc
    if width != 2:
        raise VoiceError("Only 16-bit WAV files are supported.")
    data = np.frombuffer(raw, dtype="<i2").reshape(-1, ch)
    return data, rate, ch


def wav_bytes(samples: np.ndarray, rate: int) -> bytes:
    samples = np.asarray(samples, dtype="<i2")
    ch = 1 if samples.ndim == 1 else samples.shape[1]
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(ch)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(samples.tobytes())
    return buf.getvalue()


def to_speech(samples: np.ndarray, rate: int) -> np.ndarray:
    """Mix down to mono and resample to 16 kHz (int16)."""
    x = samples.astype(np.float32)
    if x.ndim == 2:
        x = x.mean(axis=1)
    if rate != SPEECH_RATE and len(x):
        if rate > SPEECH_RATE:                        # cheap anti-alias before decimating
            k = max(1, int(round(rate / SPEECH_RATE)))
            x = np.convolve(x, np.ones(k, dtype=np.float32) / k, mode="same")
        n_out = int(round(len(x) * SPEECH_RATE / rate))
        x = np.interp(np.linspace(0, len(x) - 1, n_out), np.arange(len(x)), x)
    return np.clip(np.rint(x), -32768, 32767).astype("<i2")


def duration_of(pcm: np.ndarray, rate: int = SPEECH_RATE) -> float:
    return len(pcm) / float(rate)


# ---------------------------------------------------------------------------
# Artificial voice (formant synthesis – no samples, no models)
# ---------------------------------------------------------------------------

_VOWELS = [  # F1, F2, F3 in Hz
    (730, 1090, 2440), (530, 1840, 2480), (270, 2290, 3010),
    (570, 840, 2410), (300, 870, 2240), (660, 1720, 2410), (400, 1900, 2600),
]
_STYLES = {"deep": 98.0, "warm": 128.0, "bright": 205.0, "robot": 150.0}


def synthesize_cover(frames: int, rng: Optional[np.random.Generator] = None) -> np.ndarray:
    """A computer-made, speech-like voice. Returns int16 stereo (frames, 2) at 44.1 kHz."""
    rng = rng or np.random.default_rng()
    sr = COVER_RATE
    style = rng.choice(list(_STYLES))
    f0_base = _STYLES[style]
    out = np.zeros(frames, dtype=np.float64)
    pos = int(0.30 * sr)
    left_in_word = int(rng.integers(2, 5))

    while pos < frames - 400:
        length = min(int(rng.uniform(0.10, 0.23) * sr), frames - pos)
        t = np.arange(length) / sr
        dur = length / sr
        f1, f2, f3 = _VOWELS[int(rng.integers(len(_VOWELS)))]
        f0 = f0_base * (1.0 + rng.uniform(-0.10, 0.20))
        inst = f0 * (1.0 + 0.045 * np.sin(2 * np.pi * 5.6 * t) + rng.uniform(-0.12, 0.12) * t / dur)
        phase = 2 * np.pi * np.cumsum(inst) / sr
        ks = np.arange(1, int(4800 / f0) + 1)
        fk = ks * f0
        env = (1.0 / (1 + ((fk - f1) / 85) ** 2)
               + 0.75 / (1 + ((fk - f2) / 120) ** 2)
               + 0.40 / (1 + ((fk - f3) / 170) ** 2)) / ks ** 0.55
        sig = (env[:, None] * np.sin(ks[:, None] * phase[None, :])).sum(axis=0)
        sig /= max(1e-9, np.abs(sig).max())
        amp = np.minimum(1.0, t / 0.018) * np.minimum(1.0, (dur - t) / 0.035)
        seg = sig * amp
        if rng.random() < 0.45:                                   # fricative / plosive onset
            nb = min(int(0.03 * sr), length)
            burst = np.diff(rng.normal(size=nb + 1)) * np.hanning(nb)
            seg[:nb] += 0.32 * burst / max(1e-9, np.abs(burst).max())
        out[pos:pos + length] += seg
        left_in_word -= 1
        gap = rng.uniform(0.008, 0.035)
        if left_in_word <= 0:
            gap = rng.uniform(0.12, 0.32)
            left_in_word = int(rng.integers(2, 5))
        pos += length + int(gap * sr)

    tt = np.arange(frames) / sr
    if style == "robot":                                          # ring-modulated vocoder feel
        out *= 0.62 + 0.38 * np.sin(2 * np.pi * 58.0 * tt)
    delay = int(0.007 * sr)                                       # short comb -> synthetic timbre
    out[delay:] += 0.35 * out[:-delay].copy()
    out /= max(1e-9, np.abs(out).max())
    out *= 0.55 * 32767

    left = out
    right = np.roll(out, 14) * 0.97                               # tiny inter-channel offset
    return np.rint(np.stack([left, right], axis=1)).astype("<i2")


# ---------------------------------------------------------------------------
# Hiding / revealing bytes in the low bits
# ---------------------------------------------------------------------------

def frames_needed(payload_len: int, bits: int = EMBED_BITS, channels: int = 2) -> int:
    body = -(-payload_len * 8 // bits)
    return -(-(_HDR_SAMPLES + body) // channels)


MAX_COVER_SECONDS = 30 * 60
KEY_ID_OVERHEAD = ce.KEY_ID_LEN + ce.KEM_CT_LEN + ce.X_LEN + 12 + 16     # header + nonce + tag (post-quantum)


def cover_capacity_seconds(frames: int, channels: int, bits: int = 2) -> float:
    """Roughly how many seconds of recorded speech fit into a cover of this size."""
    cap_bytes = (frames * channels * bits - _HDR_SAMPLES) // 8
    return max(0.0, cap_bytes / (SPEECH_RATE * 2 * 0.85))      # ~15 % zlib saving


def load_cover_wav(source) -> tuple[np.ndarray, int]:
    """A user-chosen cover (16-bit WAV) as (frames, channels) int16 and its sample rate."""
    samples, rate, _ch = read_wav(source)
    if len(samples) < _HDR_SAMPLES * 4:
        raise VoiceError("That audio is too short to use.")
    if len(samples) / rate > MAX_COVER_SECONDS:
        raise VoiceError("That audio is longer than 30 minutes. Choose a shorter one.")
    return np.ascontiguousarray(samples), rate


def prepare_cover(samples: np.ndarray, payload_len: int) -> tuple[np.ndarray, int, int]:
    """
    Pick how many low bits to use so the payload fits. The fewest bits that fit are
    used (1 is inaudible, 3 is still far below the music). If even 3 are not enough the
    audio is repeated. Returns (cover, bits, repeats).
    """
    bits, repeats = choose_bits(samples.shape[0], samples.shape[1], payload_len)
    return (samples if repeats == 1 else np.tile(samples, (repeats, 1))), bits, repeats


def choose_bits(frames: int, channels: int, payload_len: int) -> tuple[int, int]:
    """(low bits per sample, repeats) needed to hide `payload_len` bytes in such a cover."""
    for bits in (1, 2, 3):
        if frames_needed(payload_len, bits, channels) <= frames:
            return bits, 1
    return 2, -(-frames_needed(payload_len, 2, channels) // frames)


def estimate_payload_len(pcm: np.ndarray, post_quantum: bool = False) -> int:
    """Size of the hidden data for this recording (compresses a copy to measure it)."""
    raw = np.asarray(pcm, dtype="<i2").tobytes()
    packed = len(zlib.compress(raw, 6)) if len(raw) < 12_000_000 else int(len(raw) * 0.9)
    overhead = 12 + (6 + KEY_ID_OVERHEAD if post_quantum else 6 + 16 + 12 + 16)
    return packed + overhead


def embed(cover: np.ndarray, payload: bytes, bits: int = EMBED_BITS) -> np.ndarray:
    flat = np.ascontiguousarray(cover, dtype="<i2").reshape(-1).copy().view(np.uint16)
    header = struct.pack(_HDR_FMT, _HDR_MAGIC, 1, bits, len(payload), zlib.crc32(payload) & 0xFFFFFFFF)
    hb = np.unpackbits(np.frombuffer(header, dtype=np.uint8)).astype(np.uint16)
    flat[:len(hb)] = (flat[:len(hb)] & 0xFFFE) | hb

    pb = np.unpackbits(np.frombuffer(payload, dtype=np.uint8))
    pad = (-len(pb)) % bits
    if pad:
        pb = np.concatenate([pb, np.zeros(pad, dtype=np.uint8)])
    weights = (1 << np.arange(bits - 1, -1, -1)).astype(np.uint16)
    vals = (pb.reshape(-1, bits).astype(np.uint16) * weights).sum(axis=1).astype(np.uint16)
    end = _HDR_SAMPLES + len(vals)
    if end > len(flat):
        raise VoiceError("The cover is too short for this recording.")
    mask = np.uint16((1 << bits) - 1)
    seg = flat[_HDR_SAMPLES:end]
    flat[_HDR_SAMPLES:end] = (seg & ~mask) | vals
    return flat.view("<i2").reshape(cover.shape)


def extract(samples: np.ndarray) -> bytes:
    flat = np.ascontiguousarray(samples, dtype="<i2").reshape(-1).view(np.uint16)
    if len(flat) < _HDR_SAMPLES:
        raise VoiceError("This audio doesn't contain an encrypted voice.")
    hb = (flat[:_HDR_LEN * 8] & 1).astype(np.uint8)
    magic, _ver, bits, length, crc = struct.unpack(_HDR_FMT, np.packbits(hb).tobytes())
    if magic != _HDR_MAGIC or not 1 <= bits <= 8:
        raise VoiceError("This audio doesn't contain an encrypted voice.")
    n = -(-length * 8 // bits)
    if _HDR_SAMPLES + n > len(flat):
        raise VoiceError("The encrypted voice is cut off. Use the original, lossless WAV file.")
    vals = flat[_HDR_SAMPLES:_HDR_SAMPLES + n] & np.uint16((1 << bits) - 1)
    shifts = np.arange(bits - 1, -1, -1).astype(np.uint16)
    bitmat = ((vals[:, None] >> shifts[None, :]) & 1).astype(np.uint8).reshape(-1)
    payload = np.packbits(bitmat).tobytes()[:length]
    if zlib.crc32(payload) & 0xFFFFFFFF != crc:
        raise VoiceError("The audio was altered or re-encoded, so the hidden data is damaged. "
                         "Use the original WAV file.")
    return payload


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

@dataclass
class EncryptedVoice:
    wav: bytes
    seconds: float
    original_seconds: float
    payload_bytes: int
    cover_kind: str = "synthetic"        # "synthetic" | "custom"
    repeats: int = 1                     # custom cover repeated this many times to fit
    bits: int = EMBED_BITS


def encrypt_voice(pcm: np.ndarray, *, algorithm: ce.Algorithm = ce.Algorithm.AES_256_GCM,
                  password: Optional[str] = None, recipient: Optional[bytes] = None,
                  cover: Optional[tuple[np.ndarray, int]] = None) -> EncryptedVoice:
    """
    pcm: int16 mono at 16 kHz.
    cover: optional (samples (frames, channels) int16, sample_rate) – the audio the result
           should sound like. Without it a synthetic voice is generated.
    """
    if len(pcm) < SPEECH_RATE // 4:
        raise VoiceError("The recording is too short.")
    if len(pcm) > MAX_SECONDS * SPEECH_RATE:
        raise VoiceError("The recording is longer than 15 minutes.")
    container = (_PAYLOAD_MAGIC + struct.pack("<II", SPEECH_RATE, len(pcm))
                 + zlib.compress(np.asarray(pcm, dtype="<i2").tobytes(), 6))
    sealed = ce.seal(container, algorithm=algorithm, password=password, recipient=recipient)

    if cover is not None:
        samples, rate = cover
        fitted, bits, repeats = prepare_cover(np.asarray(samples, dtype="<i2"), len(sealed))
        stego = embed(fitted, sealed, bits)
        return EncryptedVoice(wav_bytes(stego, rate), len(stego) / rate, len(pcm) / SPEECH_RATE,
                              len(sealed), "custom", repeats, bits)

    frames = max(frames_needed(len(sealed)) + COVER_RATE // 2, int(2.0 * COVER_RATE))
    stego = embed(synthesize_cover(frames), sealed)
    return EncryptedVoice(wav_bytes(stego, COVER_RATE), frames / COVER_RATE,
                          len(pcm) / SPEECH_RATE, len(sealed))


def read_sealed_voice(wav: bytes | str) -> bytes:
    samples, _rate, _ch = read_wav(wav)
    return extract(samples)


def inspect_voice(wav: bytes | str) -> ce.BlobInfo:
    try:
        return ce.inspect_blob(read_sealed_voice(wav))
    except VoiceError:
        raise
    except ValueError as exc:
        raise VoiceError("This audio doesn't contain an encrypted voice.") from exc


def decrypt_voice(wav: bytes | str, *, password: Optional[str] = None,
                  secret: Optional[bytes] = None) -> tuple[np.ndarray, int]:
    sealed = read_sealed_voice(wav)
    try:
        container = ce.open_sealed(sealed, password=password, secret=secret)
    except ValueError as exc:
        raise VoiceError(str(exc)) from exc
    if container[:4] != _PAYLOAD_MAGIC:
        raise VoiceError("The decrypted data isn't a voice recording.")
    rate, n = struct.unpack("<II", container[4:12])
    pcm = np.frombuffer(zlib.decompress(container[12:]), dtype="<i2")
    if len(pcm) != n:
        raise VoiceError("The recording is damaged.")
    return pcm, rate
