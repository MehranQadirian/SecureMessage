"""
Voice Studio – record, transcribe, encrypt and decrypt voice.

  Record  -> play it back -> speech-to-text (-> encrypt the text)
                          -> encrypt the voice itself (hidden inside an artificial voice)
  Save the original and/or the encrypted audio.
  Open an encrypted voice file -> decrypt -> the original recording is restored.
"""

from __future__ import annotations

import threading
from datetime import datetime

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Adw, Gdk, Gio, GLib, GObject  # noqa: E402

import crypto_engine as ce
import stt
import voice_codec as vc
from protection import MIN_PASSWORD_LENGTH, DecParams, EncParams, UserError, decrypt_kwargs
from widgets import (ActionButton, ToastHost, ToastKind, WaveView, fmt_time, make_icon,
                     make_pane, padded, traffic_lights)

try:
    import audio
    _AUDIO_OK = audio.gst_available()
except Exception:                      # GStreamer typelib missing
    audio, _AUDIO_OK = None, False


# ---------------------------------------------------------------------------
# Player row
# ---------------------------------------------------------------------------

class PlayerRow(Gtk.Box):
    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        self.player = audio.Player(on_finished=self._finished) if _AUDIO_OK else None
        self._timer = 0
        self.duration = 0.0
        if self.player is None:
            lbl = Gtk.Label(label="Playback needs GStreamer (see the Record section).", xalign=0)
            lbl.add_css_class("sm-dim")
            self.append(lbl)
            return
        self.stack = Gtk.Stack()
        self.stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self.stack.set_transition_duration(150)
        self.stack.add_named(make_icon("play", 16), "play")
        self.stack.add_named(make_icon("pause", 16), "pause")
        self.btn = Gtk.Button()
        self.btn.add_css_class("play-btn")
        self.btn.set_child(self.stack)
        self.btn.set_tooltip_text("Play / pause")
        self.btn.update_property([Gtk.AccessibleProperty.LABEL], ["Play or pause"])
        self.btn.connect("clicked", self._toggle)
        self.append(self.btn)
        self.scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 1, 0.01)
        self.scale.set_draw_value(False)
        self.scale.set_hexpand(True)
        self.scale.add_css_class("seek")
        self.scale.connect("change-value", self._on_seek)
        self.append(self.scale)
        self.time = Gtk.Label(label="0:00 / 0:00")
        self.time.add_css_class("sm-dim"); self.time.add_css_class("sm-small"); self.time.add_css_class("tnum")
        self.append(self.time)

    def set_audio(self, wav: bytes, seconds: float):
        if self.player is None:
            return
        self.duration = seconds
        self.player.load(wav, seconds)
        self.scale.set_range(0, max(0.1, seconds))
        self.scale.set_value(0)
        self._update(0.0)
        self.stack.set_visible_child_name("play")
        self._stop_timer()

    def _toggle(self, *_):
        p = self.player
        if p.playing:
            p.pause()
            self.stack.set_visible_child_name("play")
        else:
            p.play()
            self.stack.set_visible_child_name("pause")
            if not self._timer:
                self._timer = GLib.timeout_add(80, self._tick)

    def _on_seek(self, _scale, _scroll, value):
        p = self.player
        value = max(0.0, min(self.duration, value))
        if not p.active:
            p.play()
            self.stack.set_visible_child_name("pause")
            GLib.timeout_add(120, lambda: (p.seek(value), False)[1])
            if not self._timer:
                self._timer = GLib.timeout_add(80, self._tick)
        else:
            p.seek(value)
        self._update(value)
        return False

    def _tick(self):
        pos = self.player.position()
        self.scale.set_value(pos)
        self._update(pos)
        return self.player.active

    def _update(self, pos: float):
        self.time.set_text(f"{fmt_time(pos)} / {fmt_time(self.duration)}")

    def _finished(self):
        self.stack.set_visible_child_name("play")
        self.scale.set_value(0)
        self._update(0.0)
        self._stop_timer()

    def _stop_timer(self):
        if self._timer:
            GLib.source_remove(self._timer)
            self._timer = 0

    def stop(self):
        if self.player is not None:
            self.player.stop()
            self.stack.set_visible_child_name("play")
            self._stop_timer()

    def close(self):
        self._stop_timer()
        if self.player is not None:
            self.player.close()


# ---------------------------------------------------------------------------
# Studio window
# ---------------------------------------------------------------------------

def _reveal(child: Gtk.Widget) -> Gtk.Revealer:
    rev = Gtk.Revealer()
    rev.set_transition_type(Gtk.RevealerTransitionType.SLIDE_DOWN)
    rev.set_transition_duration(300)
    child.set_margin_bottom(16)          # spacing lives inside, so hidden cards leave no gap
    rev.set_child(child)
    return rev


def _caption(text: str) -> Gtk.Label:
    lbl = Gtk.Label(label=text, xalign=0)
    lbl.add_css_class("sm-caption")
    return lbl


class VoiceStudio(Adw.Window):
    def __init__(self, main):
        super().__init__(transient_for=main)
        self.main = main
        self.set_title("Voice")
        self.set_default_size(620, 820)
        self.set_size_request(360, 520)
        self._busy = False
        self._pcm = None            # original recording (int16, 16 kHz)
        self._enc = None            # vc.EncryptedVoice
        self._dec = None            # (pcm, rate)
        self._dec_blob = None
        self.cover = None            # {'samples','rate','name','seconds','capacity'} for a custom cover
        self.cover_kind = "synthetic"
        self._dec_info = None
        self._dec_can_open = False
        self._rec_timer = 0
        self._pulse = 0
        self.recorder = audio.Recorder(on_error=self._on_rec_error) if _AUDIO_OK else None

        view = Adw.ToolbarView()
        header = Adw.HeaderBar()
        header.set_show_start_title_buttons(False)
        header.set_show_end_title_buttons(False)
        header.pack_start(traffic_lights(self, only_close=True))
        header.set_title_widget(Adw.WindowTitle(title="Voice", subtitle="Record · transcribe · encrypt"))
        view.add_top_bar(header)

        col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        for side in ("top", "bottom", "start", "end"):
            getattr(col, f"set_margin_{side}")(16)
        rc = self._record_card()
        rc.set_margin_bottom(16)
        col.append(rc)
        self.rec_rev = _reveal(self._recording_card())
        col.append(self.rec_rev)
        self.enc_rev = _reveal(self._encrypted_card())
        col.append(self.enc_rev)
        open_card = self._open_card()
        open_card.set_margin_bottom(16)
        col.append(open_card)
        self.dec_rev = _reveal(self._decrypted_card())
        col.append(self.dec_rev)

        clamp = Adw.Clamp(maximum_size=640)
        clamp.set_child(col)
        sw = Gtk.ScrolledWindow()
        sw.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        sw.set_child(clamp)
        self.toasts = ToastHost()
        overlay = Gtk.Overlay()
        overlay.set_child(sw)
        overlay.add_overlay(self.toasts)
        view.set_content(overlay)
        self.set_content(view)
        self.connect("close-request", self._on_close)
        self.refresh_summary()
        GLib.idle_add(lambda: (self.rec_btn.grab_focus(), False)[1])

    # ------------------------------------------------------------------ cards
    def _protection_block(self) -> Gtk.Widget:
        """Chosen right where it is used: password, or (optionally) a post-quantum key."""
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.append(_caption("Protect with"))
        seg = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=2)
        seg.add_css_class("segmented")
        seg.set_homogeneous(True)
        self.m_buttons, first = [], None
        for label in ("Password", "Post-quantum key"):
            b = Gtk.ToggleButton(label=label)
            if first is None:
                first = b
            else:
                b.set_group(first)
            seg.append(b)
            self.m_buttons.append(b)
        want_pq = self.main.method == "pq" and ce.pq_available()
        self.m_buttons[1 if want_pq else 0].set_active(True)
        self.method = "pq" if want_pq else "password"
        if not ce.pq_available():
            self.m_buttons[1].set_sensitive(False)
            self.m_buttons[1].set_tooltip_text("Needs the pqcrypto package")
        for i, b in enumerate(self.m_buttons):
            b.connect("toggled", self._on_method, i)
        box.append(seg)

        pw = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.e_pwd = Gtk.PasswordEntry(show_peek_icon=True)
        self.e_pwd.add_css_class("sm-entry")
        try:
            self.e_pwd.set_property("placeholder-text", "Choose a password")
        except TypeError:
            pass
        self.e_pwd.connect("changed", lambda *_: self.e_pwd.remove_css_class("invalid"))
        pw.append(self.e_pwd)
        n1 = Gtk.Label(xalign=0, wrap=True, label="Share it separately. The receiver needs it to decrypt.")
        n1.add_css_class("sm-dim"); n1.add_css_class("sm-small")
        pw.append(n1)
        self.pw_rev = self._plain_reveal(pw)
        box.append(self.pw_rev)

        pq = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.e_key_model = Gtk.StringList()
        self.e_key_dd = Gtk.DropDown(model=self.e_key_model)
        self.e_key_dd.set_hexpand(True)
        self.e_key_dd.add_css_class("sm-dropdown")
        row.append(self.e_key_dd)
        keys_btn = ActionButton("Keys", "key", tooltip="Create your key pair or add contacts")
        keys_btn.connect("clicked", lambda *_: self.main.open_keys())
        row.append(keys_btn)
        pq.append(row)
        n2 = Gtk.Label(xalign=0, wrap=True,
                       label="Encrypt to someone's public key. Only the owner of the matching secret key "
                             "can open it, even with a quantum computer.")
        n2.add_css_class("sm-dim"); n2.add_css_class("sm-small")
        pq.append(n2)
        self.pq_rev = self._plain_reveal(pq)
        box.append(self.pq_rev)

        self.cipher_lbl = Gtk.Label(xalign=0, wrap=True)
        self.cipher_lbl.add_css_class("sm-dim"); self.cipher_lbl.add_css_class("sm-small")
        box.append(self.cipher_lbl)
        self._key_entries = []
        self.refresh_keys()
        self._apply_method()
        return box

    # ---- what the encrypted file should sound like ------------------------------
    def _disguise_block(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.append(_caption("The encrypted file should sound like"))
        seg = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=2)
        seg.add_css_class("segmented")
        seg.set_homogeneous(True)
        self.c_buttons, first = [], None
        for label in ("Artificial voice", "My own audio"):
            b = Gtk.ToggleButton(label=label)
            if first is None:
                first = b
            else:
                b.set_group(first)
            seg.append(b)
            self.c_buttons.append(b)
        self.c_buttons[0].set_active(True)
        for i, b in enumerate(self.c_buttons):
            b.connect("toggled", self._on_cover_kind, i)
        box.append(seg)

        own = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.cover_btn = ActionButton("Choose audio…", "folder", busy_label="Reading…", done_label="Loaded")
        self.cover_btn.connect("clicked", self._choose_cover)
        own.append(self.cover_btn)
        self.cover_lbl = Gtk.Label(xalign=0, wrap=True)
        self.cover_lbl.add_css_class("pane-title")
        self.cover_lbl.set_visible(False)
        own.append(self.cover_lbl)
        self.cover_hint = Gtk.Label(xalign=0, wrap=True)
        self.cover_hint.add_css_class("sm-dim"); self.cover_hint.add_css_class("sm-small")
        own.append(self.cover_hint)
        self.cover_rev = self._plain_reveal(own)
        box.append(self.cover_rev)
        self._update_cover_hint()
        return box

    def _on_cover_kind(self, btn, index):
        if btn.get_active():
            self.cover_kind = "custom" if index == 1 else "synthetic"
            self.cover_rev.set_reveal_child(self.cover_kind == "custom")

    def _update_cover_hint(self):
        if self.cover is None:
            self.cover_hint.set_label("Any audio file you like: a song, a talk, your own voice. WAV works "
                                      "everywhere; MP3, OGG, FLAC and M4A need audio support.")
            return
        c = self.cover
        if self._pcm is None:
            self.cover_hint.set_label(f"Room for about {c['capacity']:.0f} s of recorded voice.")
            return
        need = estimate = vc.estimate_payload_len(self._pcm, self.method == "pq")
        bits, repeats = vc.choose_bits(len(c["samples"]), c["channels"], need)
        if repeats > 1:
            self.cover_hint.set_label(f"Your audio is shorter than the recording needs, so it will be "
                                      f"repeated {repeats}× to fit.")
        else:
            self.cover_hint.set_label("Your recording fits. The chosen audio stays "
                                      + ("unchanged to the ear." if bits <= 2 else "essentially unchanged."))

    def _choose_cover(self, *_):
        if self._busy:
            return
        flt = Gtk.FileFilter()
        flt.set_name("Audio files")
        flt.add_mime_type("audio/*")
        for ext in ("wav", "mp3", "ogg", "oga", "opus", "flac", "m4a", "aac", "wma", "aiff"):
            flt.add_suffix(ext)
        store = Gio.ListStore.new(Gtk.FileFilter)
        store.append(flt)
        dlg = Gtk.FileDialog()
        dlg.set_title("Choose the audio to hide the voice in")
        dlg.set_filters(store)
        dlg.open(self, None, self._on_cover_chosen)

    def _on_cover_chosen(self, dlg, res):
        try:
            f = dlg.open_finish(res)
        except GLib.Error:
            return
        self._load_cover(f)

    def _load_cover(self, f: Gio.File):
        path = f.get_path()
        name = f.get_basename()

        def work():
            if not path:
                raise ValueError("Choose a file that is stored on this computer.")
            samples = rate = None
            if path.lower().endswith(".wav"):
                try:
                    samples, rate = vc.load_cover_wav(path)
                except vc.VoiceError:
                    if audio is None:
                        raise
            if samples is None:
                if audio is None:
                    raise ValueError("MP3, OGG, FLAC and M4A need audio support. Use a WAV file or "
                                     "install audio support from the Record section.")
                samples, rate = audio.decode_audio_file(path, vc.MAX_COVER_SECONDS)
                if len(samples) < 4096:
                    raise ValueError("That audio is too short to use.")
            frames, ch = samples.shape
            return {"samples": samples, "rate": rate, "name": name, "seconds": frames / rate,
                    "channels": ch, "capacity": vc.cover_capacity_seconds(frames, ch, 3)}

        def ok(cover):
            self.cover = cover
            self.cover_btn.done()
            self.cover_lbl.set_label(f"{cover['name']}  ·  {fmt_time(cover['seconds'])}  ·  "
                                     f"{cover['rate'] / 1000:.1f} kHz {'stereo' if cover['channels'] == 2 else 'mono'}")
            self.cover_lbl.set_visible(True)
            self._update_cover_hint()
            self.toast(ToastKind.SUCCESS, f"“{cover['name']}” will be used as the disguise.")
        self._async(self.cover_btn, work, ok, "Reading the audio…")

    @staticmethod
    def _plain_reveal(child: Gtk.Widget) -> Gtk.Revealer:
        rev = Gtk.Revealer()
        rev.set_transition_type(Gtk.RevealerTransitionType.SLIDE_DOWN)
        rev.set_transition_duration(240)
        rev.set_child(child)
        return rev

    def _on_method(self, btn, index):
        if btn.get_active():
            self.method = "pq" if index == 1 else "password"
            self._apply_method()

    def _apply_method(self):
        pq = self.method == "pq"
        self.pq_rev.set_reveal_child(pq)
        self.pw_rev.set_reveal_child(not pq)
        if hasattr(self, "cover_hint"):
            self._update_cover_hint()

    def refresh_summary(self):
        """Called by the main window when the cipher changes."""
        self.cipher_lbl.set_label(f"Cipher: {self.main._selected_algorithm().value} "
                                  "(change it in the main window)")

    def refresh_keys(self):
        keep = None
        i = self.e_key_dd.get_selected()
        if 0 <= i < len(self._key_entries):
            keep = self._key_entries[i].key_id
        entries = sorted(self.main.keystore.entries(), key=lambda e: (not e.own, e.name.lower()))
        self._key_entries = entries
        labels = [f"{e.name}  ({'yours' if e.own else 'contact'})" for e in entries] or ["No keys yet"]
        self.e_key_model.splice(0, self.e_key_model.get_n_items(), labels)
        idx = next((k for k, e in enumerate(entries) if e.key_id == keep), 0)
        if entries:
            self.e_key_dd.set_selected(idx)

    def _enc_params(self) -> EncParams:
        alg = self.main._selected_algorithm()
        if self.method == "pq":
            i = self.e_key_dd.get_selected()
            if not (0 <= i < len(self._key_entries)):
                raise UserError("Create your key pair or add a contact first (press Keys).", self.e_key_dd)
            e = self._key_entries[i]
            return EncParams(alg, None, e.public, f"Post-quantum · {alg.value} · to {e.name}")
        pwd = self.e_pwd.get_text()
        if not pwd:
            raise UserError("Choose a password to encrypt with.", self.e_pwd)
        return EncParams(alg, pwd, None, f"Password · {alg.value}", weak_password=len(pwd) < MIN_PASSWORD_LENGTH)

    def _record_card(self) -> Gtk.Widget:
        pane, _hdr = make_pane("Record")
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self.wave = WaveView()
        box.append(self.wave)
        self.timer = Gtk.Label(label="00:00.0")
        self.timer.add_css_class("timer-label")
        box.append(self.timer)

        self.rec_stack = Gtk.Stack()
        self.rec_stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self.rec_stack.set_transition_duration(180)
        self.rec_stack.add_named(make_icon("mic", 26), "mic")
        self.rec_stack.add_named(make_icon("stop", 26), "stop")
        self.rec_btn = Gtk.Button()
        self.rec_btn.add_css_class("rec-btn")
        self.rec_btn.set_halign(Gtk.Align.CENTER)
        self.rec_btn.set_child(self.rec_stack)
        self.rec_btn.set_tooltip_text("Start / stop recording")
        self.rec_btn.update_property([Gtk.AccessibleProperty.LABEL], ["Record"])
        self.rec_btn.connect("clicked", self._on_record)
        self.rec_btn.set_sensitive(_AUDIO_OK)
        box.append(self.rec_btn)
        self.rec_hint = Gtk.Label(label="Tap to record")
        self.rec_hint.add_css_class("sm-dim")
        box.append(self.rec_hint)

        if not _AUDIO_OK:
            box.append(self._audio_notice())
        pane.append(padded(box))
        return pane

    def _audio_notice(self) -> Gtk.Widget:
        n = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        n.add_css_class("notice")
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        ic = make_icon("warning", 18); ic.add_css_class("notice-icon")
        row.append(ic)
        t = Gtk.Label(xalign=0, wrap=True, label="Audio support (GStreamer) isn't installed on this computer.")
        row.append(t)
        n.append(row)
        self.install_btn = ActionButton("Install audio support", "save", busy_label="Installing…",
                                        done_label="Installed")
        self.install_btn.connect("clicked", self._install_audio)
        n.append(self.install_btn)
        return n

    def _recording_card(self) -> Gtk.Widget:
        pane, hdr = make_pane("Your recording")
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self.rec_player = PlayerRow()
        box.append(self.rec_player)

        box.append(Gtk.Separator())
        box.append(_caption("Speech to text"))
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.lang = Gtk.DropDown.new_from_strings([n for n, _c in stt.LANGUAGES])
        self.lang.set_tooltip_text("Spoken language")
        row.append(self.lang)
        self.tx_btn = ActionButton("Transcribe", "wave", busy_label="Working…", done_label="Done",
                                   tooltip="Convert speech to text and put it in the message")
        self.tx_btn.set_hexpand(True)
        self.tx_btn.connect("clicked", lambda *_: self._transcribe(encrypt=False))
        row.append(self.tx_btn)
        box.append(row)
        self.status_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.status_spin = Gtk.Spinner()
        self.status_lbl = Gtk.Label(xalign=0, wrap=True)
        self.status_lbl.add_css_class("sm-dim")
        self.status_row.append(self.status_spin); self.status_row.append(self.status_lbl)
        self.status_rev = Gtk.Revealer()
        self.status_rev.set_transition_type(Gtk.RevealerTransitionType.SLIDE_DOWN)
        self.status_rev.set_child(self.status_row)
        box.append(self.status_rev)
        note = Gtk.Label(xalign=0, wrap=True,
                         label="Runs on this computer. The first use downloads a speech model (about 145 MB).")
        note.add_css_class("sm-dim"); note.add_css_class("sm-small")
        box.append(note)

        box.append(Gtk.Separator())
        box.append(self._protection_block())
        box.append(self._disguise_block())
        self.enc_btn = ActionButton("Encrypt voice", "lock", primary=True, busy_label="Encrypting…",
                                    done_label="Encrypted",
                                    tooltip="Lock the recording and hide it inside the chosen audio")
        self.enc_btn.connect("clicked", self._encrypt_voice)
        box.append(self.enc_btn)
        self.tx_enc_btn = ActionButton("Transcribe and encrypt text", "lock", busy_label="Working…",
                                       done_label="Done",
                                       tooltip="Convert speech to text, then encrypt the text with the same protection")
        self.tx_enc_btn.connect("clicked", lambda *_: self._transcribe(encrypt=True))
        box.append(self.tx_enc_btn)
        self.save_orig = ActionButton("Save original…", "save", done_label="Saved")
        self.save_orig.connect("clicked", lambda *_: self._save(
            vc.wav_bytes(self._pcm, vc.SPEECH_RATE), f"{self._stamp()}-original.wav", self.save_orig, "original voice"))
        box.append(self.save_orig)
        pane.append(padded(box))
        return pane

    def _encrypted_card(self) -> Gtk.Widget:
        pane, _hdr = make_pane("Encrypted voice")
        self.enc_pane = pane
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self.enc_info = Gtk.Label(xalign=0, wrap=True)
        box.append(self.enc_info)
        self.enc_player = PlayerRow()
        box.append(self.enc_player)
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8, homogeneous=True)
        self.save_enc = ActionButton("Save encrypted…", "save", primary=True, done_label="Saved")
        self.save_enc.connect("clicked", lambda *_: self._save(
            self._enc.wav, f"{self._stamp()}-encrypted.wav", self.save_enc, "encrypted voice"))
        self.save_both = ActionButton("Save both…", "folder", done_label="Saved")
        self.save_both.connect("clicked", self._save_both)
        row.append(self.save_enc); row.append(self.save_both)
        box.append(row)
        self.enc_note = Gtk.Label(xalign=0, wrap=True)
        self.enc_note.add_css_class("sm-dim"); self.enc_note.add_css_class("sm-small")
        box.append(self.enc_note)
        pane.append(padded(box))
        return pane

    def _open_card(self) -> Gtk.Widget:
        pane, _hdr = make_pane("Decrypt a voice")
        self.open_pane = pane
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        lbl = Gtk.Label(xalign=0, wrap=True,
                        label="1. Open an encrypted voice file (or drop it here).  "
                              "2. Enter its password or key passphrase.  3. The original recording is restored.")
        lbl.add_css_class("sm-dim")
        box.append(lbl)
        self.open_btn = ActionButton("Open encrypted file…", "folder")
        self.open_btn.connect("clicked", self._open_file)
        box.append(self.open_btn)

        # shown after a file has been chosen
        u = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        u.append(Gtk.Separator())
        self.file_lbl = Gtk.Label(xalign=0, wrap=True)
        self.file_lbl.add_css_class("pane-title")
        u.append(self.file_lbl)
        self.unlock_info = Gtk.Label(xalign=0, wrap=True)
        self.unlock_info.add_css_class("sm-dim")
        u.append(self.unlock_info)
        self.unlock_entry = Gtk.PasswordEntry(show_peek_icon=True)
        self.unlock_entry.add_css_class("sm-entry")
        self.unlock_entry.connect("activate", lambda *_: self._decrypt_loaded())
        self.unlock_entry.connect("changed", lambda *_: self.unlock_entry.remove_css_class("invalid"))
        u.append(self.unlock_entry)
        self.unlock_btn = ActionButton("Decrypt", "unlock", primary=True, busy_label="Decrypting…",
                                       done_label="Decrypted")
        self.unlock_btn.connect("clicked", lambda *_: self._decrypt_loaded())
        u.append(self.unlock_btn)
        self.unlock_rev = self._plain_reveal(u)
        box.append(self.unlock_rev)
        pane.append(padded(box))

        drop = Gtk.DropTarget.new(Gio.File, Gdk.DragAction.COPY)
        drop.connect("drop", self._on_drop)
        pane.add_controller(drop)
        return pane

    def _decrypted_card(self) -> Gtk.Widget:
        pane, _hdr = make_pane("Original voice restored")
        self.dec_pane = pane
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self.dec_player = PlayerRow()
        box.append(self.dec_player)
        self.dec_save = ActionButton("Save original…", "save", primary=True, done_label="Saved")
        self.dec_save.connect("clicked", lambda *_: self._save(
            vc.wav_bytes(self._dec[0], self._dec[1]), f"{self._stamp()}-decrypted.wav", self.dec_save,
            "original voice"))
        box.append(self.dec_save)
        self.dec_tx = ActionButton("Transcribe to message", "wave", busy_label="Working…", done_label="Done")
        self.dec_tx.connect("clicked", lambda *_: self._transcribe(encrypt=False, source="dec"))
        box.append(self.dec_tx)
        pane.append(padded(box))
        return pane

    # ------------------------------------------------------------------ helpers
    @staticmethod
    def _stamp() -> str:
        return datetime.now().strftime("voice-%Y%m%d-%H%M%S")

    def toast(self, kind: ToastKind, msg: str):
        self.toasts.show_toast(kind, msg)

    def _user_error(self, err: UserError):
        self.toast(ToastKind.ERROR, str(err))
        w = err.widget
        if w is not None:
            w.add_css_class("invalid")
            GLib.timeout_add(2200, lambda: (w.remove_css_class("invalid"), False)[1])
            w.grab_focus()

    def _status(self, text):
        if text:
            self.status_lbl.set_label(text)
            self.status_spin.start()
        else:
            self.status_spin.stop()
        self.status_rev.set_reveal_child(bool(text))
        return False

    def _flash(self, pane: Gtk.Widget):
        pane.add_css_class("flash")
        GLib.timeout_add(900, lambda: (pane.remove_css_class("flash"), False)[1])

    def _async(self, btn: ActionButton, work, on_ok, status: str | None = None, on_error=None):
        if self._busy:
            return
        self._busy = True
        btn.busy()
        if status:
            self._status(status)

        def runner():
            try:
                res, err = work(), None
            except (ValueError, OSError, RuntimeError) as exc:
                res, err = None, str(exc)
            except Exception as exc:  # pragma: no cover
                res, err = None, f"Unexpected error: {exc}"
            GLib.idle_add(finish, res, err)

        def finish(res, err):
            self._busy = False
            self._status(None)
            if err is not None:
                btn.reset()
                self.toast(ToastKind.ERROR, err)
                if on_error:
                    on_error(err)
            else:
                on_ok(res)
            return False

        threading.Thread(target=runner, daemon=True).start()

    # ------------------------------------------------------------------ recording
    def _on_record(self, *_):
        if self.recorder is None or self._busy:
            return
        if self.recorder.recording:
            self._stop_recording()
        else:
            self._start_recording()

    def _start_recording(self):
        try:
            self.recorder.start()
        except RuntimeError as exc:
            self.toast(ToastKind.ERROR, str(exc))
            return
        for r in (self.rec_player, self.enc_player, self.dec_player):
            r.stop()
        self.rec_rev.set_reveal_child(False)
        self.enc_rev.set_reveal_child(False)
        self._pcm = self._enc = None
        self.wave.run(lambda: self.recorder.level)
        self.rec_stack.set_visible_child_name("stop")
        self.rec_btn.add_css_class("on")
        self.rec_hint.set_label("Recording… tap to stop")
        self._rec_timer = GLib.timeout_add(100, self._rec_tick)
        self._pulse = GLib.timeout_add(750, self._pulse_tick)

    def _rec_tick(self):
        if not self.recorder.recording:
            self._rec_timer = 0
            return False
        s = self.recorder.seconds
        self.timer.set_label(f"{int(s // 60):02d}:{s % 60:04.1f}")
        if s >= vc.MAX_SECONDS:
            self._stop_recording()
            self.toast(ToastKind.INFO, "Reached the 15-minute limit.")
            return False
        return True

    def _pulse_tick(self):
        if not self.recorder.recording:
            self.rec_btn.remove_css_class("pulse")
            self._pulse = 0
            return False
        if self.rec_btn.has_css_class("pulse"):
            self.rec_btn.remove_css_class("pulse")
        else:
            self.rec_btn.add_css_class("pulse")
        return True

    def _stop_recording(self):
        pcm = self.recorder.stop()
        self.wave.stop()
        self.rec_stack.set_visible_child_name("mic")
        self.rec_btn.remove_css_class("on"); self.rec_btn.remove_css_class("pulse")
        seconds = len(pcm) / vc.SPEECH_RATE
        self.timer.set_label(f"{int(seconds // 60):02d}:{seconds % 60:04.1f}")
        if seconds < 0.5:
            self.rec_hint.set_label("Tap to record")
            self.toast(ToastKind.WARNING, "That was too short. Hold the recording a little longer.")
            return
        self._pcm = pcm
        self.rec_hint.set_label("Tap to record again")
        self._update_cover_hint()
        self.rec_player.set_audio(vc.wav_bytes(pcm, vc.SPEECH_RATE), seconds)
        self.rec_rev.set_reveal_child(True)

    def _on_rec_error(self, msg: str):
        self.wave.stop()
        self.rec_stack.set_visible_child_name("mic")
        self.rec_btn.remove_css_class("on")
        self.rec_hint.set_label("Tap to record")
        self.toast(ToastKind.ERROR, msg)
        return False

    def _install_audio(self, *_):
        import bootstrap
        pm, prefix = bootstrap.detect_package_manager()
        pkgs = bootstrap.AUDIO_PACKAGES.get(pm)
        if not pkgs:
            self.toast(ToastKind.ERROR, "Install GStreamer (with its “good” plugins) using your package manager.")
            return

        def work():
            if pm == "apt":
                bootstrap.run_privileged(["apt-get", "update", "-qq"])
            ok, msg = bootstrap.run_privileged(prefix + pkgs)
            if not ok:
                raise RuntimeError(msg or "Installation failed.")
            return True
        self._async(self.install_btn, work, lambda _r: (
            self.install_btn.done(3000),
            self.toast(ToastKind.SUCCESS, "Installed. Restart Secure Message to use the microphone.")),
            "Installing audio support…")

    # ------------------------------------------------------------------ speech to text
    def _transcribe(self, encrypt: bool, source: str = "rec"):
        pcm = self._pcm if source == "rec" else (self._dec[0] if self._dec else None)
        btn = {("rec", False): self.tx_btn, ("rec", True): self.tx_enc_btn}.get((source, encrypt), self.dec_tx)
        if pcm is None:
            return
        params = None
        if encrypt:                                   # fail early, before the long transcription
            try:
                params = self._enc_params()
            except UserError as err:
                self._user_error(err)
                return
        lang = stt.LANGUAGES[self.lang.get_selected()][1]

        def work():
            if not stt.package_installed():
                GLib.idle_add(self._status, "Installing speech recognition (one time)…")
                stt.install_package()
            if not stt.model_ready():
                GLib.idle_add(self._status, "Preparing the speech model (first time: about 145 MB)…")
            stt.load_model()
            GLib.idle_add(self._status, "Transcribing…")
            text = stt.transcribe(pcm, lang)
            token = None
            if params is not None:
                GLib.idle_add(self._status, "Encrypting the text…")
                token = ce.encrypt_text(text, algorithm=params.algorithm, password=params.password,
                                        recipient=params.recipient)
            return text, token

        def ok(result):
            text, token = result
            btn.done()
            if token is not None:
                self.main.show_text_result(text, token, params.label)
                self.main.present()
                self.toast(ToastKind.SUCCESS, "Transcribed and encrypted. See the main window.")
            else:
                self.main.set_message_text(text)
                self.toast(ToastKind.SUCCESS, "Transcribed. The text is in the message box.")
        self._async(btn, work, ok, "Starting…")

    # ------------------------------------------------------------------ voice encryption
    def _encrypt_voice(self, *_):
        if self._pcm is None:
            return
        try:
            p = self._enc_params()
            if self.cover_kind == "custom" and self.cover is None:
                raise UserError("Choose the audio to hide the voice in, or switch to the artificial voice.",
                                self.cover_btn)
        except UserError as err:
            self._user_error(err)
            return
        pcm = self._pcm
        cover = self.cover if self.cover_kind == "custom" else None

        def work():
            return vc.encrypt_voice(pcm, algorithm=p.algorithm, password=p.password, recipient=p.recipient,
                                    cover=(cover["samples"], cover["rate"]) if cover else None)

        def ok(ev):
            self._enc = ev
            self.enc_btn.done()
            if cover:
                where = f"inside “{cover['name']}”"
                if ev.repeats > 1:
                    where += f" (repeated {ev.repeats}× to fit)"
                self.enc_note.set_label("It plays your chosen audio. Keep it as a WAV file: converting to MP3 "
                                        "or sending it through an app that re-encodes audio destroys the "
                                        "hidden message.")
                heard = "your audio"
            else:
                where = "inside an artificial voice"
                self.enc_note.set_label("It plays as a computer-made voice. Keep it as a WAV file: converting "
                                        "to MP3 or sending it through an app that re-encodes audio destroys "
                                        "the hidden message.")
                heard = "the artificial voice"
            self.enc_info.set_label(
                f"{p.label}\n{fmt_time(ev.seconds)} · {len(ev.wav) / 1e6:.1f} MB · "
                f"{ev.original_seconds:.1f} s of speech hidden {where}")
            self.enc_player.set_audio(ev.wav, ev.seconds)
            self.enc_rev.set_reveal_child(True)
            self._flash(self.enc_pane)
            self.toast(ToastKind.SUCCESS, f"Voice encrypted. Press play to hear {heard}.")
            if p.weak_password:
                self.toast(ToastKind.WARNING, "The password is short. Use 12 or more characters.")
        self._async(self.enc_btn, work, ok,
                    "Encrypting and hiding the voice…" if cover else "Encrypting and generating the artificial voice…")

    # ------------------------------------------------------------------ decrypt voice
    def _wav_filter_store(self):
        flt = Gtk.FileFilter()
        flt.set_name("WAV audio")
        flt.add_suffix("wav")
        store = Gio.ListStore.new(Gtk.FileFilter)
        store.append(flt)
        return store

    def _open_file(self, *_):
        if self._busy:
            return
        dlg = Gtk.FileDialog()
        dlg.set_title("Open encrypted voice")
        dlg.set_filters(self._wav_filter_store())
        dlg.open(self, None, self._on_open_chosen)

    def _on_open_chosen(self, dlg, res):
        try:
            f = dlg.open_finish(res)
        except GLib.Error:
            return
        self._load_encrypted(f)

    def _on_drop(self, _target, value, _x, _y):
        if isinstance(value, Gio.File):
            self._load_encrypted(value)
            return True
        return False

    def _load_encrypted(self, f: Gio.File):
        """Step 1: read the file and see what protects it. Step 2 (password) comes next."""
        try:
            _ok, data, _etag = f.load_contents(None)
            data = bytes(data)
            info = vc.inspect_voice(data)
        except GLib.Error as exc:
            self.toast(ToastKind.ERROR, f"Couldn't open the file: {exc.message}")
            return
        except vc.VoiceError as exc:
            self.toast(ToastKind.ERROR, str(exc))
            return
        self._dec_blob, self._dec_info = data, info
        self._dec_can_open = True
        self.dec_rev.set_reveal_child(False)
        self._dec = None
        self.file_lbl.set_label(f"{f.get_basename()}  ·  {len(data) / 1e6:.1f} MB")
        self.unlock_entry.set_text("")
        if info.is_pq:
            e = self.main.keystore.find(info.key_id)
            if e is not None and e.own:
                self.unlock_info.set_label(f"Encrypted with your post-quantum key “{e.name}”. "
                                           "Enter that key’s passphrase.")
                self._set_placeholder("Key passphrase")
            elif e is not None:
                self.unlock_info.set_label(f"This was encrypted for your contact “{e.name}”, so only "
                                           f"{e.name} can open it.")
                self._dec_can_open = False
            else:
                self.unlock_info.set_label("This was encrypted for a post-quantum key that isn’t on this "
                                           "computer. Import your key pair from Keys, or ask the sender "
                                           "to use your public key.")
                self._dec_can_open = False
        else:
            self.unlock_info.set_label("Protected with a password. Enter it to decrypt.")
            self._set_placeholder("Password")
        self.unlock_entry.set_visible(self._dec_can_open)
        self.unlock_btn.set_visible(self._dec_can_open)
        self.unlock_rev.set_reveal_child(True)
        if self._dec_can_open:
            GLib.idle_add(lambda: (self.unlock_entry.grab_focus(), False)[1])
        else:
            self.toast(ToastKind.WARNING, "You don’t have the key for this voice.")

    def _set_placeholder(self, text: str):
        try:
            self.unlock_entry.set_property("placeholder-text", text)
        except TypeError:
            pass

    def _decrypt_loaded(self, *_):
        if self._busy or not getattr(self, "_dec_blob", None) or not self._dec_can_open:
            return
        secret = self.unlock_entry.get_text()
        info = self._dec_info
        if not secret:
            self._user_error(UserError("Enter the key passphrase." if info.is_pq else "Enter the password.",
                                       self.unlock_entry))
            return
        dp = DecParams(None if info.is_pq else secret, info.key_id if info.is_pq else None,
                       secret if info.is_pq else None)
        blob = self._dec_blob

        def work():
            return vc.decrypt_voice(blob, **decrypt_kwargs(self.main.keystore, dp))

        def ok(result):
            pcm, rate = result
            self._dec = (pcm, rate)
            self.unlock_btn.done()
            seconds = len(pcm) / rate
            self.dec_player.set_audio(vc.wav_bytes(pcm, rate), seconds)
            self.dec_rev.set_reveal_child(True)
            self._flash(self.dec_pane)
            self.toast(ToastKind.SUCCESS, f"Voice decrypted ({fmt_time(seconds)}).")

        def failed_hint(_=None):
            self.unlock_entry.add_css_class("invalid")
            self.unlock_entry.grab_focus()
        self._async(self.unlock_btn, work, ok, "Decrypting…", on_error=failed_hint)

    # ------------------------------------------------------------------ saving
    def _save(self, data: bytes, name: str, btn: ActionButton, label: str):
        dlg = Gtk.FileDialog()
        dlg.set_initial_name(name)
        dlg.set_filters(self._wav_filter_store())

        def done(d, res):
            try:
                f = d.save_finish(res)
            except GLib.Error:
                return
            try:
                f.replace_contents(data, None, False, Gio.FileCreateFlags.NONE, None)
            except GLib.Error as exc:
                self.toast(ToastKind.ERROR, f"Couldn't save: {exc.message}")
                return
            btn.done()
            self.toast(ToastKind.SUCCESS, f"Saved the {label}: {f.get_basename()}")
        dlg.save(self, None, done)

    def _save_both(self, *_):
        if self._pcm is None or self._enc is None:
            return
        dlg = Gtk.FileDialog()
        dlg.set_title("Choose a folder for both files")

        def done(d, res):
            try:
                folder = d.select_folder_finish(res)
            except GLib.Error:
                return
            base = self._stamp()
            try:
                for suffix, data in (("original", vc.wav_bytes(self._pcm, vc.SPEECH_RATE)),
                                     ("encrypted", self._enc.wav)):
                    folder.get_child(f"{base}-{suffix}.wav").replace_contents(
                        data, None, False, Gio.FileCreateFlags.NONE, None)
            except GLib.Error as exc:
                self.toast(ToastKind.ERROR, f"Couldn't save: {exc.message}")
                return
            self.save_both.done()
            self.toast(ToastKind.SUCCESS, f"Saved {base}-original.wav and {base}-encrypted.wav")
        dlg.select_folder(self, None, done)

    # ------------------------------------------------------------------ close
    def _on_close(self, *_):
        if self.recorder is not None and self.recorder.recording:
            self.recorder.stop()
        self.wave.stop()
        for r in (self.rec_player, self.enc_player, self.dec_player):
            r.close()
        self.main.studio = None
        return False
