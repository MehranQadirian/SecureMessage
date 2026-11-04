"""Headless tests for file_vault (no GTK needed).  Run:  python3 -m pytest tests -q"""
import hashlib
import io
import os
import struct
import sys
import wave
import zipfile
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

import crypto_engine as ce
import file_vault as fv

PW = "correct horse battery"


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def make_png(path, w=64, h=64):
    def chunk(t, data):
        return struct.pack(">I", len(data)) + t + data + struct.pack(">I", zlib.crc32(t + data))
    rows = []
    for y in range(h):
        row = bytearray([0])
        for x in range(w):
            row += bytes((x * 4 % 256, y * 4 % 256, 128))
        rows.append(bytes(row))
    raw = b"".join(rows)
    Path(path).write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
                           + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def make_wav(path, seconds=1):
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(8000)
        w.writeframes(os.urandom(8000 * 2 * seconds))


@pytest.fixture
def d(tmp_path):
    return tmp_path


@pytest.mark.parametrize("size", [0, 1, fv.CHUNK - 1, fv.CHUNK, fv.CHUNK + 1, 3 * fv.CHUNK + 17])
@pytest.mark.parametrize("alg", list(ce.Algorithm))
def test_roundtrip_sizes_and_ciphers(d, size, alg):
    make_png(d / "c.png")
    (d / "s.bin").write_bytes(os.urandom(size))
    r = fv.hide(d / "c.png", d / "s.bin", d / "out.png", algorithm=alg, password=PW)
    assert r.mode == "append" and r.out_size > r.carrier_size
    info = fv.inspect_carrier(d / "out.png")
    assert info and not info.is_pq and info.algorithm == alg.value
    meta = fv.extract(d / "out.png", d / "back.bin", password=PW)
    assert meta.name == "s.bin" and meta.size == size
    assert sha(d / "back.bin") == sha(d / "s.bin")


def test_carrier_prefix_untouched_and_still_valid_image(d):
    from PIL import Image
    make_png(d / "c.png")
    (d / "s.txt").write_text("secret")
    fv.hide(d / "c.png", d / "s.txt", d / "o.png", password=PW)
    assert (d / "o.png").read_bytes().startswith((d / "c.png").read_bytes())
    Image.open(d / "o.png").load()


def test_wav_carrier_still_plays(d):
    make_wav(d / "c.wav")
    (d / "s.bin").write_bytes(os.urandom(300_000))
    fv.hide(d / "c.wav", d / "s.bin", d / "o.wav", password=PW)
    with wave.open(str(d / "o.wav")) as a, wave.open(str(d / "c.wav")) as b:
        assert a.readframes(a.getnframes()) == b.readframes(b.getnframes())


def test_any_secret_inside_any_carrier(d):
    """image in video-like, audio in image, zip in pdf-like, pdf in wav ..."""
    carriers = {"v.mp4": b"\x00\x00\x00\x18ftypmp42" + os.urandom(5000), "p.pdf": b"%PDF-1.4\n%%EOF\n",
                "a.mp3": b"ID3" + os.urandom(4000)}
    for n, data in carriers.items():
        (d / n).write_bytes(data)
    make_png(d / "i.png"); make_wav(d / "a.wav")
    with zipfile.ZipFile(d / "s.zip", "w") as z:
        z.writestr("x.txt", "hello")
    secrets_ = ["i.png", "a.wav", "s.zip", "p.pdf"]
    for cn in carriers:
        for sn in secrets_:
            out = d / f"{cn}.{sn}.out"
            fv.hide(d / cn, d / sn, out, password=PW)
            fv.extract(out, d / f"r_{cn}_{sn}", password=PW)
            assert sha(d / f"r_{cn}_{sn}") == sha(d / sn)


def test_zip_carrier_stays_valid_archive(d):
    with zipfile.ZipFile(d / "c.zip", "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("hello.txt", "hi there" * 100)
    (d / "s.bin").write_bytes(os.urandom(2_500_000))
    r = fv.hide(d / "c.zip", d / "s.bin", d / "o.zip", password=PW)
    assert r.mode == "zip"
    with zipfile.ZipFile(d / "o.zip") as z:
        assert z.testzip() is None
        assert z.read("hello.txt") == b"hi there" * 100
    fv.extract(d / "o.zip", d / "back.bin", password=PW)
    assert sha(d / "back.bin") == sha(d / "s.bin")
    assert fv.inspect_carrier(d / "c.zip") is None


def test_post_quantum_mode(d):
    pub, sec = ce.generate_identity()
    make_png(d / "c.png"); (d / "s.bin").write_bytes(os.urandom(100_000))
    fv.hide(d / "c.png", d / "s.bin", d / "o.png", recipient=pub)
    info = fv.inspect_carrier(d / "o.png")
    assert info.is_pq and info.key_id == ce.key_id(pub)
    fv.extract(d / "o.png", d / "b.bin", secret=sec)
    assert sha(d / "b.bin") == sha(d / "s.bin")
    _, other = ce.generate_identity()
    with pytest.raises(fv.VaultError):
        fv.extract(d / "o.png", d / "x.bin", secret=other)
    assert not (d / "x.bin").exists()


def test_wrong_password_and_no_leftovers(d):
    make_png(d / "c.png"); (d / "s.bin").write_bytes(b"x" * 10)
    fv.hide(d / "c.png", d / "s.bin", d / "o.png", password=PW)
    with pytest.raises(fv.VaultError):
        fv.extract(d / "o.png", d / "b.bin", password="nope")
    assert not (d / "b.bin").exists()
    assert not [p for p in d.iterdir() if p.name.endswith(".part")]


@pytest.mark.parametrize("where", ["body_first", "body_last", "truncate_footer"])
def test_tampering_is_detected(d, where):
    make_png(d / "c.png"); (d / "s.bin").write_bytes(os.urandom(2 * fv.CHUNK + 5))
    fv.hide(d / "c.png", d / "s.bin", d / "o.png", password=PW)
    raw = bytearray((d / "o.png").read_bytes())
    if where == "body_first":
        raw[len(raw) - fv.FOOTER_LEN - 3 * fv.CHUNK + 100] ^= 1
    elif where == "body_last":
        raw[len(raw) - fv.FOOTER_LEN - 10] ^= 1
    else:
        raw = raw[:-5]
    (d / "t.png").write_bytes(bytes(raw))
    if where == "truncate_footer":
        assert fv.inspect_carrier(d / "t.png") is None
        return
    with pytest.raises(fv.VaultError):
        fv.extract(d / "t.png", d / "b.bin", password=PW)
    assert not (d / "b.bin").exists()


def test_chunk_dropping_and_truncation_detected(d):
    """Removing the last chunk and re-writing a consistent footer must still fail (final flag)."""
    make_png(d / "c.png"); (d / "s.bin").write_bytes(os.urandom(3 * fv.CHUNK))
    fv.hide(d / "c.png", d / "s.bin", d / "o.png", password=PW)
    raw = (d / "o.png").read_bytes()
    _, plen = fv._FOOTER.unpack(raw[-fv.FOOTER_LEN:])
    body = raw[:-fv.FOOTER_LEN - (fv.CHUNK + fv.TAG)]
    forged = body + fv._FOOTER.pack(fv.FOOTER_MAGIC, plen - (fv.CHUNK + fv.TAG))
    (d / "f.png").write_bytes(forged)
    with pytest.raises(fv.VaultError):
        fv.extract(d / "f.png", d / "b.bin", password=PW)


def test_refuses_double_hide_and_overwriting_sources(d):
    make_png(d / "c.png"); (d / "s.bin").write_bytes(b"abc")
    fv.hide(d / "c.png", d / "s.bin", d / "o.png", password=PW)
    with pytest.raises(fv.VaultError):
        fv.hide(d / "o.png", d / "s.bin", d / "o2.png", password=PW)
    with pytest.raises(fv.VaultError):
        fv.hide(d / "c.png", d / "s.bin", d / "c.png", password=PW)
    with pytest.raises(fv.VaultError):
        fv.hide(d / "c.png", d / "s.bin", d / "z.png")          # no password / key


def test_name_hidden_and_sanitised(d):
    make_png(d / "c.png")
    (d / "top-secret-plan.pdf").write_bytes(b"%PDF-1.4 plan")
    fv.hide(d / "c.png", d / "top-secret-plan.pdf", d / "o.png", password=PW)
    blob = (d / "o.png").read_bytes()
    assert b"top-secret-plan" not in blob and b"%PDF" not in blob
    assert fv.safe_name("../../etc/passwd") == "passwd"
    assert fv.safe_name("..\\..\\x.txt") == "x.txt"
    assert fv.safe_name("\x00\x07") == "hidden-file"


def test_plain_file_has_nothing_hidden(d):
    make_png(d / "c.png")
    assert fv.inspect_carrier(d / "c.png") is None
    with pytest.raises(fv.VaultError):
        fv.read_meta(d / "c.png", password=PW)


def test_progress_reaches_total(d):
    make_png(d / "c.png"); (d / "s.bin").write_bytes(os.urandom(3 * fv.CHUNK))
    seen = []
    fv.hide(d / "c.png", d / "s.bin", d / "o.png", password=PW, progress=lambda a, b: seen.append((a, b)))
    assert seen[-1][0] == seen[-1][1] and all(a <= b for a, b in seen)
