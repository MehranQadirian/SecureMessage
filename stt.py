"""
Offline speech-to-text (faster-whisper / Whisper "base").

Nothing is sent anywhere: the audio is transcribed on this computer. The only
network use is a one-time download of the model (~145 MB) and, if needed, the
`faster-whisper` Python package (installed into the app's private folder).
"""

from __future__ import annotations

import importlib.util
import threading
from pathlib import Path
from typing import Callable, Optional

import numpy as np

import pylib
from gi.repository import GLib

MODEL_SIZE = "base"
LANGUAGES = [
    ("Auto-detect", None), ("Persian (فارسی)", "fa"), ("English", "en"),
    ("Arabic (العربية)", "ar"), ("Turkish", "tr"), ("German", "de"),
    ("French", "fr"), ("Spanish", "es"), ("Russian", "ru"),
]

_model = None
_lock = threading.Lock()


class SpeechError(ValueError):
    pass


def package_installed() -> bool:
    pylib.activate()
    return importlib.util.find_spec("faster_whisper") is not None


def model_dir() -> Path:
    return Path(GLib.get_user_data_dir()) / "securemessage" / "models"


def install_package(log: Optional[Callable[[str], None]] = None) -> None:
    ok, out = pylib.pip_install(["faster-whisper"], log)
    if not ok or not package_installed():
        raise SpeechError("Couldn't install speech recognition.\n" + out)


def _load_model():
    global _model
    with _lock:
        if _model is None:
            try:
                from faster_whisper import WhisperModel
                _model = WhisperModel(MODEL_SIZE, device="cpu", compute_type="int8",
                                      download_root=str(model_dir()))
            except Exception as exc:
                raise SpeechError(
                    "Couldn't load the speech model. It needs an internet connection the first "
                    f"time (about 145 MB). {exc}") from exc
        return _model


def model_ready() -> bool:
    return _model is not None


def transcribe(pcm: np.ndarray, language: Optional[str] = None) -> str:
    """pcm: int16 mono 16 kHz. Returns the recognised text."""
    x = pcm.astype(np.float32) / 32768.0
    if len(x) < 4000 or float(np.sqrt(np.mean(x ** 2))) < 0.002:
        raise SpeechError("No speech was heard. Check the microphone and try again.")
    model = _load_model()
    try:
        segments, _info = model.transcribe(x, language=language, vad_filter=True, beam_size=5)
        text = " ".join(s.text.strip() for s in segments).strip()
    except Exception as exc:
        raise SpeechError(f"Speech recognition failed: {exc}") from exc
    if not text:
        raise SpeechError("No speech was recognised. Try speaking closer to the microphone.")
    return text


def load_model():
    """Public wrapper: loads (and, the first time, downloads) the speech model."""
    return _load_model()
