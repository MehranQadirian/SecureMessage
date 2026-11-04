"""Microphone recording and playback through GStreamer (PipeWire / PulseAudio / ALSA)."""

from __future__ import annotations

import math
import os
import tempfile
import threading
from typing import Callable, Optional

import numpy as np

import gi
gi.require_version("Gst", "1.0")
from gi.repository import Gst, GLib  # noqa: E402

import voice_codec as vc

_gst_ready = False


def gst_available() -> bool:
    global _gst_ready
    try:
        if not _gst_ready:
            Gst.init(None)
            _gst_ready = True
        return Gst.ElementFactory.find("appsink") is not None and Gst.ElementFactory.find("playbin") is not None
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Recorder
# ---------------------------------------------------------------------------

class Recorder:
    """
    Records 16 kHz mono 16-bit audio into memory.

    `level` (0..1, smoothed loudness) can be polled from the UI thread at any time;
    `on_error(message)` is called on the UI thread if the microphone fails.
    """

    SOURCE = "autoaudiosrc"   # overridable for tests

    def __init__(self, on_error: Optional[Callable[[str], None]] = None):
        self.on_error = on_error
        self._pipe = None
        self._chunks: list[bytes] = []
        self._lock = threading.Lock()
        self.level = 0.0
        self.recording = False
        self._samples = 0

    @property
    def seconds(self) -> float:
        return self._samples / vc.SPEECH_RATE

    def start(self):
        if not gst_available():
            raise RuntimeError("Audio support (GStreamer) is not installed.")
        self._chunks, self._samples, self.level = [], 0, 0.0
        desc = (f"{self.SOURCE} ! audioconvert ! audioresample ! "
                f"audio/x-raw,format=S16LE,rate={vc.SPEECH_RATE},channels=1 ! "
                "appsink name=sink emit-signals=true sync=false max-buffers=50 drop=false")
        try:
            self._pipe = Gst.parse_launch(desc)
        except GLib.Error as exc:
            raise RuntimeError(f"Couldn't open the microphone: {exc.message}") from exc
        sink = self._pipe.get_by_name("sink")
        sink.connect("new-sample", self._on_sample)
        bus = self._pipe.get_bus()
        bus.add_signal_watch()
        bus.connect("message::error", self._on_bus_error)
        self.recording = True
        if self._pipe.set_state(Gst.State.PLAYING) == Gst.StateChangeReturn.FAILURE:
            self._teardown()
            raise RuntimeError("Couldn't start the microphone. Is one connected and allowed?")

    def _on_sample(self, sink):
        sample = sink.emit("pull-sample")
        if sample is None:
            return Gst.FlowReturn.OK
        buf = sample.get_buffer()
        ok, info = buf.map(Gst.MapFlags.READ)
        if ok:
            data = bytes(info.data)
            buf.unmap(info)
            x = np.frombuffer(data, dtype="<i2")
            if len(x):
                rms = float(np.sqrt(np.mean((x.astype(np.float32) / 32768.0) ** 2)) + 1e-9)
                db = 20 * math.log10(rms)
                target = min(1.0, max(0.0, (db + 55.0) / 50.0))
                self.level = max(target, self.level * 0.80)
                with self._lock:
                    self._chunks.append(data)
                    self._samples += len(x)
        return Gst.FlowReturn.OK

    def _on_bus_error(self, _bus, msg):
        err, _dbg = msg.parse_error()
        self._teardown()
        if self.on_error:
            GLib.idle_add(self.on_error, f"Microphone error: {err.message}")

    def _teardown(self):
        self.recording = False
        if self._pipe is not None:
            bus = self._pipe.get_bus()
            bus.remove_signal_watch()
            self._pipe.set_state(Gst.State.NULL)
            self._pipe = None

    def stop(self) -> np.ndarray:
        """Stop and return everything recorded (int16 mono, 16 kHz)."""
        self._teardown()
        self.level = 0.0
        with self._lock:
            data = b"".join(self._chunks)
        return np.frombuffer(data, dtype="<i2").copy()


# ---------------------------------------------------------------------------
# Player (plays WAV bytes through a private temp file)
# ---------------------------------------------------------------------------

class Player:
    def __init__(self, on_finished: Optional[Callable[[], None]] = None):
        self.on_finished = on_finished
        self._pipe = None
        self._paused = False
        self._path: Optional[str] = None
        self.duration = 0.0

    def load(self, wav: bytes, duration: float):
        self.stop()
        self._cleanup_file()
        fd, self._path = tempfile.mkstemp(prefix="sm-", suffix=".wav",
                                          dir=GLib.get_user_runtime_dir() or None)
        with os.fdopen(fd, "wb") as fh:
            fh.write(wav)
        os.chmod(self._path, 0o600)
        self.duration = duration

    def _make(self):
        pipe = Gst.ElementFactory.make("playbin", None)
        pipe.set_property("uri", Gst.filename_to_uri(self._path))
        fake = Gst.ElementFactory.make("fakesink", None)
        pipe.set_property("video-sink", fake)
        bus = pipe.get_bus()
        bus.add_signal_watch()
        bus.connect("message::eos", self._on_eos)
        bus.connect("message::error", self._on_eos)
        return pipe

    def _on_eos(self, *_):
        self.stop()
        if self.on_finished:
            self.on_finished()

    @property
    def active(self) -> bool:
        """A pipeline exists (playing or paused)."""
        return self._pipe is not None

    @property
    def playing(self) -> bool:
        return self._pipe is not None and not self._paused

    def play(self):
        if not self._path:
            return
        if self._pipe is not None and self._paused:          # resume
            self._paused = False
            self._pipe.set_state(Gst.State.PLAYING)
            return
        self.stop()
        self._pipe = self._make()
        self._paused = False
        self._pipe.set_state(Gst.State.PLAYING)

    def pause(self):
        if self._pipe is not None:
            self._paused = True
            self._pipe.set_state(Gst.State.PAUSED)

    def stop(self):
        if self._pipe is not None:
            self._pipe.get_bus().remove_signal_watch()
            self._pipe.set_state(Gst.State.NULL)
            self._pipe = None
        self._paused = False

    def position(self) -> float:
        if self._pipe is None:
            return 0.0
        ok, pos = self._pipe.query_position(Gst.Format.TIME)
        return pos / Gst.SECOND if ok else 0.0

    def seek(self, seconds: float):
        if self._pipe is not None:
            self._pipe.seek_simple(Gst.Format.TIME, Gst.SeekFlags.FLUSH | Gst.SeekFlags.KEY_UNIT,
                                   int(seconds * Gst.SECOND))

    def _cleanup_file(self):
        if self._path:
            try:
                os.unlink(self._path)
            except OSError:
                pass
            self._path = None

    def close(self):
        self.stop()
        self._cleanup_file()


# ---------------------------------------------------------------------------
# Decoding any audio file (MP3, OGG, FLAC, M4A, ...) to 16-bit PCM
# ---------------------------------------------------------------------------

def decode_audio_file(path: str, max_seconds: int = 30 * 60) -> tuple[np.ndarray, int]:
    """Return (samples int16 shaped (frames, 1|2), sample_rate). Blocking; run in a thread."""
    if not gst_available():
        raise RuntimeError("Audio support (GStreamer) is needed to read this kind of file. "
                           "WAV files work without it.")
    uri = Gst.filename_to_uri(path)
    desc = (f'uridecodebin uri="{uri}" caps=audio/x-raw ! audioconvert ! '
            "audio/x-raw,format=S16LE,layout=interleaved,channels=[1,2] ! "
            "appsink name=sink sync=false")
    try:
        pipe = Gst.parse_launch(desc)
    except GLib.Error as exc:
        raise RuntimeError(f"Couldn't read that audio file: {exc.message}") from exc
    sink = pipe.get_by_name("sink")
    bus = pipe.get_bus()
    chunks, rate, channels, total, idle = [], 0, 0, 0, 0.0
    if pipe.set_state(Gst.State.PLAYING) == Gst.StateChangeReturn.FAILURE:
        pipe.set_state(Gst.State.NULL)
        raise RuntimeError("Couldn't read that audio file.")
    try:
        while True:
            sample = sink.emit("try-pull-sample", 200 * Gst.MSECOND)
            if sample is None:
                if bus.pop_filtered(Gst.MessageType.ERROR) is not None:
                    raise RuntimeError("That file isn't a readable audio file.")
                if sink.get_property("eos"):
                    break
                idle += 0.2
                if idle > 15:
                    raise RuntimeError("Reading the audio timed out.")
                continue
            idle = 0.0
            if not rate:
                s = sample.get_caps().get_structure(0)
                rate, channels = s.get_int("rate")[1], s.get_int("channels")[1]
            buf = sample.get_buffer()
            ok, info = buf.map(Gst.MapFlags.READ)
            if ok:
                chunks.append(bytes(info.data))
                buf.unmap(info)
                total += len(chunks[-1])
            if rate and total / (2 * channels * rate) > max_seconds:
                raise RuntimeError("That audio is longer than 30 minutes. Choose a shorter one.")
    finally:
        pipe.set_state(Gst.State.NULL)
    if not chunks:
        raise RuntimeError("That file has no audio.")
    data = np.frombuffer(b"".join(chunks), dtype="<i2").reshape(-1, channels)
    return np.ascontiguousarray(data), rate
