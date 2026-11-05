"""
Start-up self test:  `main.py --selftest <report-file>`

Imports everything the Voice and Files windows need and exercises numpy, Pillow,
the crypto engine and GStreamer once. The packaged Windows build runs this in CI,
so a broken bundle fails the build instead of reaching users. The report goes to a
file because the windowed Windows build has no console.
"""
from __future__ import annotations

import io
import sys
import traceback


def run(report_path: str) -> int:
    lines: list[str] = []
    hard_fail = False

    def step(name: str, fn, hard: bool = True):
        nonlocal hard_fail
        try:
            detail = fn()
            lines.append(f"OK    {name}" + (f" - {detail}" if detail else ""))
        except Exception:
            lines.append(f"{'FAIL' if hard else 'WARN'}  {name}\n{traceback.format_exc()}")
            hard_fail = hard_fail or hard

    def numpy_check():
        import numpy as np
        return f"numpy {np.__version__}, sum={int(np.arange(10).sum())}"

    def pillow_check():
        from PIL import Image
        img = Image.new("RGB", (8, 8), (10, 20, 30))
        buf = io.BytesIO()
        img.save(buf, "PNG")
        return f"Pillow {Image.__version__ if hasattr(Image, '__version__') else ''}, png={len(buf.getvalue())}B"

    def crypto_check():
        import crypto_engine as ce
        alg = ce.Algorithm.AES_256_GCM
        token = ce.encrypt("self-test", "Passw0rd!-self-test", alg)
        out = ce.decrypt(token, "Passw0rd!-self-test")
        assert "self-test" in str(out), out
        return f"{alg.value}, pq backend={getattr(ce, 'KEM_BACKEND', '?')}"

    def gst_check():
        import audio
        if not audio.gst_available():
            raise RuntimeError("GStreamer plugins (appsink/playbin) not found")
        return "appsink and playbin found"

    def mod(name):
        def _f():
            __import__(name)
        return _f

    step("numpy", numpy_check)
    step("Pillow", pillow_check)
    step("crypto engine", crypto_check)
    for name in ("stego_image", "voice_codec", "vault", "stt", "voice_studio", "files_studio", "keys_window"):
        step(f"import {name}", mod(name))
    step("GStreamer", gst_check, hard=False)          # voice recording only; not fatal

    text = "\n".join(lines) + ("\nRESULT: FAILED\n" if hard_fail else "\nRESULT: PASSED\n")
    try:
        with open(report_path, "w", encoding="utf-8") as fh:
            fh.write(text)
    except OSError:
        pass
    if sys.stdout is not None:
        print(text)
    return 1 if hard_fail else 0
