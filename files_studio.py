"""
Files — encrypt (or decrypt) any file: pictures, video, audio, archives, PDFs, anything.

  Choose a file -> protect it with a password or a post-quantum key -> optionally hide
  the result inside a picture or an audio file you choose, or save it as a plain file.

  Decrypting: open the file first (the app works out what protects it), then enter the
  password or key passphrase, and the original file is restored under its original name.
"""

from __future__ import annotations

import threading
from pathlib import Path

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Adw, Gdk, Gio, GLib  # noqa: E402

import crypto_engine as ce
import stego_image as si
import vault
import voice_codec as vc
from protection import MIN_PASSWORD_LENGTH, DecParams, EncParams, UserError, decrypt_kwargs
from widgets import (ActionButton, ToastHost, ToastKind, make_icon, make_pane, padded,
                     traffic_lights)

try:
    import audio
    _AUDIO_OK = audio.gst_available()
except Exception:
    audio, _AUDIO_OK = None, False


def _caption(text: str) -> Gtk.Label:
    lbl = Gtk.Label(label=text, xalign=0)
    lbl.add_css_class("sm-caption")
    return lbl


def _reveal(child: Gtk.Widget) -> Gtk.Revealer:
    rev = Gtk.Revealer()
    rev.set_transition_type(Gtk.RevealerTransitionType.SLIDE_DOWN)
    rev.set_transition_duration(260)
    child.set_margin_bottom(16)
    rev.set_child(child)
    return rev


def fmt_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} GB"


ANY_AUDIO_SUFFIXES = ("wav", "mp3", "ogg", "oga", "opus", "flac", "m4a", "aac", "wma", "aiff")
ANY_IMAGE_SUFFIXES = ("png", "jpg", "jpeg", "bmp", "tiff", "webp")


class FilesStudio(Adw.Window):
    def __init__(self, main):
        super().__init__(transient_for=main)
        self.main = main
        self.set_title("Files")
        self.set_default_size(620, 900)
        self.set_size_request(360, 520)
        self._busy = False

        self._path: str | None = None
        self._packed: bytes | None = None
        self.disguise = "plain"           # "plain" | "image" | "audio"
        self.cover = None                 # {"samples": arr, "rate": int, "channels": int, "name": str} (audio)
        self.img_cover = None             # {"array": arr, "name": str} (image)
        self._out: bytes | None = None
        self._out_ext = ".smx"

        self._open_data: bytes | None = None
        self._open_info = None
        self._open_sealed: bytes | None = None
        self._can_open = False
        self._result: vault.UnpackedFile | None = None

        view = Adw.ToolbarView()
        header = Adw.HeaderBar()
        header.set_show_start_title_buttons(False)
        header.set_show_end_title_buttons(False)
        header.pack_start(traffic_lights(self, only_close=True))
        header.set_title_widget(Adw.WindowTitle(title="Files", subtitle="Encrypt or hide any file"))
        view.add_top_bar(header)

        col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        for side in ("top", "bottom", "start", "end"):
            getattr(col, f"set_margin_{side}")(16)
        enc = self._encrypt_card()
        enc.set_margin_bottom(16)
        col.append(enc)
        self.enc_rev = _reveal(self._encrypted_card())
        col.append(self.enc_rev)
        openc = self._open_card()
        openc.set_margin_bottom(16)
        col.append(openc)
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

    # ------------------------------------------------------------------ encrypt
    def _encrypt_card(self) -> Gtk.Widget:
        pane, _hdr = make_pane("Choose a file")
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)

        self.pick_btn = ActionButton("Choose a file…", "folder", busy_label="Reading…", done_label="Loaded",
                                     tooltip="Any file: images, video, audio, archives, PDFs…")
        self.pick_btn.connect("clicked", self._choose_file)
        box.append(self.pick_btn)
        self.file_lbl = Gtk.Label(xalign=0, wrap=True)
        self.file_lbl.add_css_class("pane-title")
        self.file_lbl.set_visible(False)
        box.append(self.file_lbl)
        self.file_sub = Gtk.Label(xalign=0, wrap=True)
        self.file_sub.add_css_class("sm-dim"); self.file_sub.add_css_class("sm-small")
        box.append(self.file_sub)

        box.append(Gtk.Separator())
        box.append(_caption("Protect with"))
        box.append(self._method_segmented())
        box.append(self.pw_rev)
        box.append(self.pq_rev)
        self.cipher_lbl = Gtk.Label(xalign=0, wrap=True)
        self.cipher_lbl.add_css_class("sm-dim"); self.cipher_lbl.add_css_class("sm-small")
        box.append(self.cipher_lbl)

        box.append(Gtk.Separator())
        box.append(_caption("The encrypted file should be"))
        box.append(self._disguise_segmented())
        box.append(self.img_rev)
        box.append(self.aud_rev)
        self.disguise_hint = Gtk.Label(xalign=0, wrap=True)
        self.disguise_hint.add_css_class("sm-dim"); self.disguise_hint.add_css_class("sm-small")
        box.append(self.disguise_hint)

        self.enc_btn = ActionButton("Encrypt", "lock", primary=True, busy_label="Encrypting…",
                                    done_label="Encrypted")
        self.enc_btn.set_margin_top(4)
        self.enc_btn.set_sensitive(False)
        self.enc_btn.connect("clicked", self._encrypt)
        box.append(self.enc_btn)
        pane.append(padded(box))
        return pane

    def _method_segmented(self) -> Gtk.Widget:
        seg = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=2)
        seg.add_css_class("segmented"); seg.set_homogeneous(True)
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
        self.pw_rev = _reveal(pw)

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
                       label="Encrypt to someone's public key. Only the owner of the matching secret "
                             "key can open it, even with a quantum computer.")
        n2.add_css_class("sm-dim"); n2.add_css_class("sm-small")
        pq.append(n2)
        self.pq_rev = _reveal(pq)
        self._key_entries = []
        self.refresh_keys()
        self._apply_method()
        return seg

    def _on_method(self, btn, index):
        if btn.get_active():
            self.method = "pq" if index == 1 else "password"
            self._apply_method()

    def _apply_method(self):
        pq = self.method == "pq"
        self.pq_rev.set_reveal_child(pq)
        self.pw_rev.set_reveal_child(not pq)
        self._update_hints()
        self._invalidate_output()

    def refresh_summary(self):
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

    def _disguise_segmented(self) -> Gtk.Widget:
        seg = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=2)
        seg.add_css_class("segmented"); seg.set_homogeneous(True)
        self.d_buttons, first = [], None
        for label in ("A plain file", "Hidden in a picture", "Hidden in audio"):
            b = Gtk.ToggleButton(label=label)
            if first is None:
                first = b
            else:
                b.set_group(first)
            seg.append(b)
            self.d_buttons.append(b)
        self.d_buttons[0].set_active(True)
        for i, b in enumerate(self.d_buttons):
            b.connect("toggled", self._on_disguise, i)

        img = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.img_btn = ActionButton("Choose a picture…", "folder", busy_label="Reading…", done_label="Loaded")
        self.img_btn.connect("clicked", self._choose_image_cover)
        img.append(self.img_btn)
        self.img_lbl = Gtk.Label(xalign=0, wrap=True)
        self.img_lbl.add_css_class("pane-title")
        self.img_lbl.set_visible(False)
        img.append(self.img_lbl)
        self.img_rev = _reveal(img)

        aud = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.aud_btn = ActionButton("Choose audio…", "folder", busy_label="Reading…", done_label="Loaded")
        self.aud_btn.connect("clicked", self._choose_audio_cover)
        aud.append(self.aud_btn)
        self.aud_lbl = Gtk.Label(xalign=0, wrap=True)
        self.aud_lbl.add_css_class("pane-title")
        self.aud_lbl.set_visible(False)
        aud.append(self.aud_lbl)
        self.aud_rev = _reveal(aud)
        return seg

    def _on_disguise(self, btn, index):
        if btn.get_active():
            self.disguise = ("plain", "image", "audio")[index]
            self.img_rev.set_reveal_child(self.disguise == "image")
            self.aud_rev.set_reveal_child(self.disguise == "audio")
            self._update_hints()
            self._invalidate_output()

    def _update_hints(self):
        if not hasattr(self, "disguise_hint"):
            return
        if self._packed is None:
            self.disguise_hint.set_label("")
            return
        est = vault.sealed_len_estimate(len(self._packed), self.method == "pq")
        if self.disguise == "plain":
            self.disguise_hint.set_label(f"Saved as a plain encrypted file (about {fmt_size(est)}).")
        elif self.disguise == "image":
            if self.img_cover is None:
                self.disguise_hint.set_label("Choose a picture to hide the file inside.")
            else:
                cap = si.capacity_bytes(self.img_cover["array"], 3)
                if est <= cap:
                    self.disguise_hint.set_label("This picture is large enough to hold the file.")
                else:
                    self.disguise_hint.set_label(
                        f"This picture can only hold about {fmt_size(cap)}, but the encrypted file is "
                        f"about {fmt_size(est)}. Choose a bigger picture or a smaller file.")
        else:
            if self.cover is None:
                self.disguise_hint.set_label("Choose an audio file to hide the file inside.")
            else:
                frames, ch = self.cover["samples"].shape
                cap = (frames * ch * 3) // 8
                if est <= cap:
                    self.disguise_hint.set_label("This audio is long enough to hold the file.")
                else:
                    self.disguise_hint.set_label(
                        f"This audio can hold about {fmt_size(cap)}; the file ({fmt_size(est)}) will make "
                        "it repeat to fit.")

    # -- choosing files ----------------------------------------------------------
    def _choose_file(self, *_):
        if self._busy:
            return
        dlg = Gtk.FileDialog()
        dlg.set_title("Choose a file to encrypt")
        dlg.open(self, None, self._on_file_chosen)

    def _on_file_chosen(self, dlg, res):
        try:
            f = dlg.open_finish(res)
        except GLib.Error:
            return
        path = f.get_path()
        if not path:
            self.toasts.show_toast(ToastKind.ERROR, "Choose a file that is stored on this computer.")
            return

        def work():
            return vault.pack_file(path)

        def ok(packed):
            self._path, self._packed = path, packed
            self.pick_btn.done(1200)
            size = Path(path).stat().st_size
            self.file_lbl.set_label(Path(path).name)
            self.file_lbl.set_visible(True)
            self.file_sub.set_label(f"{fmt_size(size)}")
            self.enc_btn.set_sensitive(True)
            self._update_hints()
            self._invalidate_output()
        self._async(self.pick_btn, work, ok, "Reading the file…")

    def _choose_image_cover(self, *_):
        dlg = Gtk.FileDialog()
        dlg.set_title("Choose a picture")
        dlg.set_filters(self._filter_store("Pictures", ANY_IMAGE_SUFFIXES))
        dlg.open(self, None, self._on_image_cover_chosen)

    def _on_image_cover_chosen(self, dlg, res):
        try:
            f = dlg.open_finish(res)
            _ok, data, _etag = f.load_contents(None)
        except GLib.Error:
            return

        def work():
            return si.load_cover_image(bytes(data))

        def ok(arr):
            self.img_cover = {"array": arr, "name": f.get_basename()}
            self.img_btn.done(1200)
            self.img_lbl.set_label(f"{f.get_basename()}  ·  {arr.shape[1]}×{arr.shape[0]}")
            self.img_lbl.set_visible(True)
            self._update_hints()
            self._invalidate_output()
        self._async(self.img_btn, work, ok, "Reading the picture…")

    def _choose_audio_cover(self, *_):
        dlg = Gtk.FileDialog()
        dlg.set_title("Choose audio")
        dlg.set_filters(self._filter_store("Audio", ANY_AUDIO_SUFFIXES))
        dlg.open(self, None, self._on_audio_cover_chosen)

    def _on_audio_cover_chosen(self, dlg, res):
        try:
            f = dlg.open_finish(res)
        except GLib.Error:
            return
        path = f.get_path()

        def work():
            if path and path.lower().endswith(".wav"):
                try:
                    samples, rate = vc.load_cover_wav(path)
                    return samples, rate
                except vc.VoiceError:
                    if audio is None:
                        raise
            if audio is None:
                raise RuntimeError("MP3, OGG, FLAC and M4A need audio support. Use a WAV file.")
            samples, rate = audio.decode_audio_file(path, vc.MAX_COVER_SECONDS)
            return samples, rate

        def ok(result):
            samples, rate = result
            self.cover = {"samples": samples, "rate": rate, "channels": samples.shape[1],
                          "name": f.get_basename()}
            self.aud_btn.done(1200)
            self.aud_lbl.set_label(f"{f.get_basename()}  ·  {len(samples) / rate:.0f} s")
            self.aud_lbl.set_visible(True)
            self._update_hints()
            self._invalidate_output()
        self._async(self.aud_btn, work, ok, "Reading the audio…")

    @staticmethod
    def _filter_store(name: str, suffixes) -> Gio.ListStore:
        flt = Gtk.FileFilter()
        flt.set_name(name)
        for s in suffixes:
            flt.add_suffix(s)
        store = Gio.ListStore.new(Gtk.FileFilter)
        store.append(flt)
        return store

    # -- encrypt -----------------------------------------------------------------
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

    def _encrypt(self, *_):
        if self._busy or self._packed is None:
            return
        try:
            p = self._enc_params()
            if self.disguise == "image" and self.img_cover is None:
                raise UserError("Choose a picture to hide the file inside.", self.img_btn)
            if self.disguise == "audio" and self.cover is None:
                raise UserError("Choose an audio file to hide the file inside.", self.aud_btn)
        except UserError as err:
            self._user_error(err)
            return
        packed, disguise, src_name = self._packed, self.disguise, Path(self._path).name
        img_cover, aud_cover = self.img_cover, self.cover

        def work():
            sealed = vault.seal_packed(packed, algorithm=p.algorithm, password=p.password, recipient=p.recipient)
            if disguise == "plain":
                return sealed, ".smx", None
            if disguise == "image":
                return vault.hide_in_image(sealed, img_cover["array"]), ".png", None
            data, repeats = vault.hide_in_audio(sealed, aud_cover["samples"], aud_cover["rate"])
            return data, ".wav", repeats

        def ok(result):
            data, ext, repeats = result
            self._out, self._out_ext = data, ext
            self.enc_btn.done()
            self._show_encrypted(p, src_name, disguise, len(data), repeats,
                                 img_cover["name"] if disguise == "image" else
                                 aud_cover["name"] if disguise == "audio" else None)
            self.toast(ToastKind.SUCCESS, "File encrypted.")
            if p.weak_password:
                self.toast(ToastKind.WARNING, "The password is short. Use 12 or more characters.")
        self._async(self.enc_btn, work, ok, "Encrypting…")

    def _invalidate_output(self):
        """Something that would change the result changed, so hide the stale 'Encrypted file'
        card rather than leave it showing a file or disguise that no longer matches the
        current settings — it would otherwise still display the previous Encrypt result."""
        self._out = None
        if hasattr(self, "enc_rev"):
            self.enc_rev.set_reveal_child(False)

    def _encrypted_card(self) -> Gtk.Widget:
        pane, _hdr = make_pane("Encrypted file")
        self.enc_pane = pane
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self.enc_info = Gtk.Label(xalign=0, wrap=True)
        box.append(self.enc_info)
        self.save_enc = ActionButton("Save…", "save", primary=True, done_label="Saved")
        self.save_enc.connect("clicked", self._save_encrypted)
        box.append(self.save_enc)
        self.enc_note = Gtk.Label(xalign=0, wrap=True)
        self.enc_note.add_css_class("sm-dim"); self.enc_note.add_css_class("sm-small")
        box.append(self.enc_note)
        pane.append(padded(box))
        return pane

    def _show_encrypted(self, p: EncParams, src_name: str, disguise: str, out_len: int,
                        repeats, cover_name: str | None):
        if disguise == "plain":
            desc = "Saved as a plain encrypted file."
            note = "Anyone can see it's an encrypted Secure Message file (but not open it without the key)."
        elif disguise == "image":
            desc = f"Hidden inside “{cover_name}”. It looks like an ordinary picture."
            note = ("Keep it as PNG. Re-saving it as JPEG or another lossy format, or sending it "
                    "through an app that recompresses images, destroys the hidden file.")
        else:
            desc = f"Hidden inside “{cover_name}”."
            if repeats and repeats > 1:
                desc += f" (repeated {repeats}× to fit)"
            note = ("Keep it as WAV. Converting it to MP3/OGG, or sending it through an app that "
                    "re-encodes audio, destroys the hidden file.")
        self.enc_info.set_label(f"{src_name}  →  {p.label}\n{desc}\n{fmt_size(out_len)}")
        self.enc_note.set_label(note)
        self.enc_rev.set_reveal_child(True)
        self._flash(self.enc_pane)

    def _save_encrypted(self, *_):
        if self._out is None:
            return
        base = Path(self._path).stem if self._path else "file"
        dlg = Gtk.FileDialog()
        dlg.set_initial_name(f"{base}-encrypted{self._out_ext}")
        dlg.save(self, None, self._on_save_encrypted)

    def _on_save_encrypted(self, dlg, res):
        try:
            f = dlg.save_finish(res)
        except GLib.Error:
            return
        try:
            f.replace_contents(self._out, None, False, Gio.FileCreateFlags.NONE, None)
        except GLib.Error as exc:
            self.toast(ToastKind.ERROR, f"Couldn't save: {exc.message}")
            return
        self.save_enc.done()
        self.toast(ToastKind.SUCCESS, f"Saved {f.get_basename()}")

    # ------------------------------------------------------------------ decrypt
    def _open_card(self) -> Gtk.Widget:
        pane, _hdr = make_pane("Decrypt a file")
        self.open_pane = pane
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        lbl = Gtk.Label(xalign=0, wrap=True,
                        label="1. Open the encrypted file (plain, or hidden in a picture or audio).  "
                              "2. Enter its password or key passphrase.  3. The original file is restored.")
        lbl.add_css_class("sm-dim")
        box.append(lbl)
        self.open_btn = ActionButton("Open a file…", "folder")
        self.open_btn.connect("clicked", self._open_file)
        box.append(self.open_btn)

        u = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        u.append(Gtk.Separator())
        self.open_lbl = Gtk.Label(xalign=0, wrap=True)
        self.open_lbl.add_css_class("pane-title")
        u.append(self.open_lbl)
        self.open_info = Gtk.Label(xalign=0, wrap=True)
        self.open_info.add_css_class("sm-dim")
        u.append(self.open_info)
        self.unlock_entry = Gtk.PasswordEntry(show_peek_icon=True)
        self.unlock_entry.add_css_class("sm-entry")
        self.unlock_entry.connect("activate", lambda *_: self._decrypt_loaded())
        self.unlock_entry.connect("changed", lambda *_: self.unlock_entry.remove_css_class("invalid"))
        u.append(self.unlock_entry)
        self.unlock_btn = ActionButton("Decrypt", "unlock", primary=True, busy_label="Decrypting…",
                                       done_label="Decrypted")
        self.unlock_btn.connect("clicked", lambda *_: self._decrypt_loaded())
        u.append(self.unlock_btn)
        self.unlock_rev = _reveal(u)
        box.append(self.unlock_rev)
        pane.append(padded(box))

        drop = Gtk.DropTarget.new(Gio.File, Gdk.DragAction.COPY)
        drop.connect("drop", lambda _t, value, _x, _y: (self._load_file(value), True)[1]
                    if isinstance(value, Gio.File) else False)
        pane.add_controller(drop)
        return pane

    def _open_file(self, *_):
        if self._busy:
            return
        dlg = Gtk.FileDialog()
        dlg.set_title("Open an encrypted file")
        dlg.open(self, None, lambda d, r: self._on_open_chosen(d, r))

    def _on_open_chosen(self, dlg, res):
        try:
            f = dlg.open_finish(res)
        except GLib.Error:
            return
        self._load_file(f)

    def _load_file(self, f: Gio.File):
        try:
            _ok, data, _etag = f.load_contents(None)
            data = bytes(data)
            sealed, info = vault.inspect(data)
        except GLib.Error as exc:
            self.toast(ToastKind.ERROR, f"Couldn't open the file: {exc.message}")
            return
        except vault.VaultError as exc:
            self.toast(ToastKind.ERROR, str(exc))
            return
        self._open_sealed, self._open_info = sealed, info
        self._can_open = True
        self.dec_rev.set_reveal_child(False)
        self._result = None
        self.open_lbl.set_label(f.get_basename())
        self.unlock_entry.set_text("")
        if info.is_pq:
            e = self.main.keystore.find(info.key_id)
            if e is not None and e.own:
                self.open_info.set_label(f"Encrypted with your post-quantum key “{e.name}”. "
                                         "Enter that key's passphrase.")
                self._set_placeholder("Key passphrase")
            elif e is not None:
                self.open_info.set_label(f"This was encrypted for your contact “{e.name}”, so only "
                                         f"{e.name} can open it.")
                self._can_open = False
            else:
                self.open_info.set_label("This was encrypted for a post-quantum key that isn't on this "
                                         "computer. Import your key pair from Keys, or ask the sender "
                                         "to use your public key.")
                self._can_open = False
        else:
            self.open_info.set_label("Protected with a password. Enter it to decrypt.")
            self._set_placeholder("Password")
        self.unlock_entry.set_visible(self._can_open)
        self.unlock_btn.set_visible(self._can_open)
        self.unlock_rev.set_reveal_child(True)
        if self._can_open:
            GLib.idle_add(lambda: (self.unlock_entry.grab_focus(), False)[1])
        else:
            self.toast(ToastKind.WARNING, "You don't have the key for this file.")

    def _set_placeholder(self, text: str):
        try:
            self.unlock_entry.set_property("placeholder-text", text)
        except TypeError:
            pass

    def _decrypt_loaded(self, *_):
        if self._busy or self._open_sealed is None or not self._can_open:
            return
        secret_text = self.unlock_entry.get_text()
        info = self._open_info
        if not secret_text:
            self._user_error(UserError(
                "Enter the key passphrase." if info.is_pq else "Enter the password.", self.unlock_entry))
            return
        dp = DecParams(None if info.is_pq else secret_text, info.key_id if info.is_pq else None,
                       secret_text if info.is_pq else None)
        sealed = self._open_sealed

        def work():
            return vault.decrypt_sealed(sealed, **decrypt_kwargs(self.main.keystore, dp))

        def ok(result: vault.UnpackedFile):
            self._result = result
            self.unlock_btn.done()
            self.result_lbl.set_label(result.name)
            self.result_sub.set_label(f"{fmt_size(len(result.data))} · {result.mime}")
            self.dec_rev.set_reveal_child(True)
            self._flash(self.dec_pane)
            self.toast(ToastKind.SUCCESS, f"Decrypted “{result.name}”.")

        def failed(_=None):
            self.unlock_entry.add_css_class("invalid")
            self.unlock_entry.grab_focus()
        self._async(self.unlock_btn, work, ok, "Decrypting…", on_error=failed)

    def _decrypted_card(self) -> Gtk.Widget:
        pane, _hdr = make_pane("Original file restored")
        self.dec_pane = pane
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self.result_lbl = Gtk.Label(xalign=0, wrap=True)
        self.result_lbl.add_css_class("pane-title")
        box.append(self.result_lbl)
        self.result_sub = Gtk.Label(xalign=0, wrap=True)
        self.result_sub.add_css_class("sm-dim")
        box.append(self.result_sub)
        self.dec_save = ActionButton("Save as…", "save", primary=True, done_label="Saved")
        self.dec_save.connect("clicked", self._save_decrypted)
        box.append(self.dec_save)
        pane.append(padded(box))
        return pane

    def _save_decrypted(self, *_):
        if self._result is None:
            return
        dlg = Gtk.FileDialog()
        dlg.set_initial_name(self._result.name)
        dlg.save(self, None, self._on_save_decrypted)

    def _on_save_decrypted(self, dlg, res):
        try:
            f = dlg.save_finish(res)
        except GLib.Error:
            return
        try:
            f.replace_contents(self._result.data, None, False, Gio.FileCreateFlags.NONE, None)
        except GLib.Error as exc:
            self.toast(ToastKind.ERROR, f"Couldn't save: {exc.message}")
            return
        self.dec_save.done()
        self.toast(ToastKind.SUCCESS, f"Saved {f.get_basename()}")

    # ------------------------------------------------------------------ helpers
    def toast(self, kind: ToastKind, msg: str):
        self.toasts.show_toast(kind, msg)

    def _user_error(self, err: UserError):
        self.toast(ToastKind.ERROR, str(err))
        w = err.widget
        if w is not None:
            w.add_css_class("invalid")
            GLib.timeout_add(2200, lambda: (w.remove_css_class("invalid"), False)[1])
            w.grab_focus()

    def _flash(self, pane: Gtk.Widget):
        pane.add_css_class("flash")
        GLib.timeout_add(900, lambda: (pane.remove_css_class("flash"), False)[1])

    def _async(self, btn: ActionButton, work, on_ok, status: str | None = None, on_error=None):
        if self._busy:
            return
        self._busy = True
        btn.busy()

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
            if err is not None:
                btn.reset()
                self.toast(ToastKind.ERROR, err)
                if on_error:
                    on_error(err)
            else:
                on_ok(res)
            return False

        threading.Thread(target=runner, daemon=True).start()
