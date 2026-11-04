"""
File Vault window — hide any file inside any other file, encrypted; and reveal it back.

    Hide     carrier (photo, video, song, zip, pdf, anything) + the file to hide
             -> protect with a password or a post-quantum key -> one output file
             that opens/plays exactly like the carrier, with the other file sealed inside.
    Reveal   open that output file -> enter the password / key passphrase
             -> the original hidden file, byte for byte.
"""

from __future__ import annotations

import threading
from pathlib import Path

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gdk, Gio, GLib, Gtk, Adw  # noqa: E402

import crypto_engine as ce
import file_vault as fv
from protection import UserError
from widgets import (ActionButton, ToastHost, ToastKind, fmt_time, make_icon, make_pane,
                     padded, spacer, traffic_lights)


def _reveal(child: Gtk.Widget) -> Gtk.Revealer:
    rev = Gtk.Revealer()
    rev.set_transition_type(Gtk.RevealerTransitionType.SLIDE_DOWN)
    rev.set_transition_duration(240)
    rev.set_child(child)
    return rev


def _caption(text: str) -> Gtk.Label:
    lbl = Gtk.Label(label=text, xalign=0)
    lbl.add_css_class("sm-caption")
    return lbl


def _human(n: int) -> str:
    size = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} GB"


def _any_file_filters() -> Gio.ListStore:
    flt = Gtk.FileFilter()
    flt.set_name("All files")
    flt.add_pattern("*")
    store = Gio.ListStore.new(Gtk.FileFilter)
    store.append(flt)
    return store


class FileVaultWindow(Adw.Window):
    def __init__(self, main):
        super().__init__(transient_for=main)
        self.main = main
        self.set_title("File Vault")
        self.set_default_size(620, 860)
        self.set_size_request(380, 540)
        self._busy = False

        self.carrier_path: Path | None = None
        self.secret_path: Path | None = None
        self._hidden_result = None          # fv.HideResult, pending save

        self.open_path: Path | None = None
        self.open_info: fv.VaultInfo | None = None
        self._can_open = False
        self._revealed_tmp: Path | None = None
        self._revealed_meta: fv.VaultMeta | None = None

        view = Adw.ToolbarView()
        header = Adw.HeaderBar()
        header.set_show_start_title_buttons(False)
        header.set_show_end_title_buttons(False)
        header.pack_start(traffic_lights(self, only_close=True))
        header.set_title_widget(Adw.WindowTitle(title="File Vault", subtitle="Hide any file inside any file"))
        view.add_top_bar(header)

        col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        for side in ("top", "bottom", "start", "end"):
            getattr(col, f"set_margin_{side}")(16)

        hide_card = self._hide_card()
        hide_card.set_margin_bottom(16)
        col.append(hide_card)
        self.progress_rev = _reveal(self._progress_card())
        col.append(self.progress_rev)
        self.result_rev = _reveal(self._result_card())
        col.append(self.result_rev)

        open_card = self._open_card()
        open_card.set_margin_bottom(16)
        col.append(open_card)
        self.revealed_rev = _reveal(self._revealed_card())
        col.append(self.revealed_rev)

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
        self.refresh_keys()

    # ------------------------------------------------------------------ hide
    def _hide_card(self) -> Gtk.Widget:
        pane, _hdr = make_pane("Hide a file")
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        lbl = Gtk.Label(xalign=0, wrap=True, label=(
            "Pick any carrier file (a photo, a video, a song, a ZIP, a PDF — anything) and any "
            "file to hide inside it. The output opens or plays exactly like the carrier."))
        lbl.add_css_class("sm-dim")
        box.append(lbl)

        self.carrier_btn = ActionButton("Choose carrier file…", "folder", busy_label="Reading…",
                                        done_label="Chosen")
        self.carrier_btn.connect("clicked", lambda *_: self._choose(is_carrier=True))
        box.append(self.carrier_btn)
        self.carrier_lbl = Gtk.Label(xalign=0, wrap=True)
        self.carrier_lbl.add_css_class("pane-title")
        self.carrier_lbl.set_visible(False)
        box.append(self.carrier_lbl)
        self.carrier_warn = Gtk.Label(xalign=0, wrap=True)
        self.carrier_warn.add_css_class("sm-small")
        self.carrier_warn.set_visible(False)
        box.append(self.carrier_warn)

        self.secret_btn = ActionButton("Choose file to hide…", "folder", busy_label="Reading…",
                                       done_label="Chosen")
        self.secret_btn.connect("clicked", lambda *_: self._choose(is_carrier=False))
        box.append(self.secret_btn)
        self.secret_lbl = Gtk.Label(xalign=0, wrap=True)
        self.secret_lbl.add_css_class("pane-title")
        self.secret_lbl.set_visible(False)
        box.append(self.secret_lbl)

        box.append(Gtk.Separator())
        box.append(self._protection_block())

        self.hide_btn = ActionButton("Hide file", "lock", primary=True, busy_label="Encrypting…",
                                     done_label="Hidden")
        self.hide_btn.connect("clicked", self._on_hide)
        box.append(self.hide_btn)
        pane.append(padded(box))

        drop = Gtk.DropTarget.new(Gio.File, Gdk.DragAction.COPY)
        drop.connect("drop", lambda t, v, x, y: self._load_carrier(v) if isinstance(v, Gio.File) else False)
        pane.add_controller(drop)
        return pane

    def _protection_block(self) -> Gtk.Widget:
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
        self.pq_rev = self._plain_reveal(pq)
        box.append(self.pq_rev)

        alg_lbl = Gtk.Label(xalign=0, wrap=True,
                            label=f"Cipher: {self.main._selected_algorithm().value} (change it in the main window)")
        alg_lbl.add_css_class("sm-dim"); alg_lbl.add_css_class("sm-small")
        box.append(alg_lbl)
        self._key_entries = []
        self._apply_method()
        return box

    @staticmethod
    def _plain_reveal(child: Gtk.Widget) -> Gtk.Revealer:
        rev = Gtk.Revealer()
        rev.set_transition_type(Gtk.RevealerTransitionType.SLIDE_DOWN)
        rev.set_transition_duration(220)
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

    def refresh_keys(self):
        if not hasattr(self, "e_key_dd"):
            return
        entries = sorted(self.main.keystore.entries(), key=lambda e: (not e.own, e.name.lower()))
        self._key_entries = entries
        labels = [f"{e.name}  ({'yours' if e.own else 'contact'})" for e in entries] or ["No keys yet"]
        self.e_key_model.splice(0, self.e_key_model.get_n_items(), labels)
        if entries:
            self.e_key_dd.set_selected(0)

    # ------------------------------------------------------------ choosing files
    def _choose(self, *, is_carrier: bool):
        if self._busy:
            return
        dlg = Gtk.FileDialog()
        dlg.set_title("Choose the carrier file" if is_carrier else "Choose the file to hide")
        dlg.set_filters(_any_file_filters())
        dlg.open(self, None, lambda d, r: self._on_chosen(d, r, is_carrier))

    def _on_chosen(self, dlg, res, is_carrier: bool):
        try:
            f = dlg.open_finish(res)
        except GLib.Error:
            return
        if is_carrier:
            self._load_carrier(f)
        else:
            self._load_secret(f)

    def _load_carrier(self, f: Gio.File):
        path = f.get_path()
        if not path:
            self.toast(ToastKind.ERROR, "Choose a file that is stored on this computer.")
            return
        p = Path(path)

        def work():
            already = fv.inspect_carrier(p)
            return p, p.stat().st_size, fv.carrier_warning(p), already is not None

        def ok(res):
            path, size, warning, already = res
            if already:
                self.toast(ToastKind.WARNING, "This file already hides something — choose a clean copy.")
                return
            self.carrier_path = path
            self.carrier_btn.done()
            self.carrier_lbl.set_label(f"{path.name}  ·  {_human(size)}")
            self.carrier_lbl.set_visible(True)
            if warning:
                self.carrier_warn.set_label(f"⚠ {warning}")
                self.carrier_warn.set_visible(True)
            else:
                self.carrier_warn.set_visible(False)
        self._async(self.carrier_btn, work, ok, "Checking the carrier…")

    def _load_secret(self, f: Gio.File):
        path = f.get_path()
        if not path:
            self.toast(ToastKind.ERROR, "Choose a file that is stored on this computer.")
            return
        p = Path(path)
        self.secret_path = p
        self.secret_btn.done()
        self.secret_lbl.set_label(f"{p.name}  ·  {_human(p.stat().st_size)}")
        self.secret_lbl.set_visible(True)

    # ------------------------------------------------------------ progress / result
    def _progress_card(self) -> Gtk.Widget:
        pane, _hdr = make_pane("Working…")
        self.progress_pane = pane
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.progress_bar = Gtk.ProgressBar()
        self.progress_bar.set_show_text(True)
        box.append(self.progress_bar)
        pane.append(padded(box))
        return pane

    def _result_card(self) -> Gtk.Widget:
        pane, _hdr = make_pane("File hidden")
        self.result_pane = pane
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self.result_lbl = Gtk.Label(xalign=0, wrap=True)
        self.result_lbl.add_css_class("sm-dim")
        box.append(self.result_lbl)
        self.save_btn = ActionButton("Save as…", "save", primary=True, done_label="Saved")
        self.save_btn.connect("clicked", self._on_save_result)
        box.append(self.save_btn)
        pane.append(padded(box))
        return pane

    # ------------------------------------------------------------ encrypt (hide)
    def _enc_kwargs(self) -> dict:
        alg = self.main._selected_algorithm()
        if self.method == "pq":
            i = self.e_key_dd.get_selected()
            if not (0 <= i < len(self._key_entries)):
                raise UserError("Create your key pair or add a contact first (press Keys).", self.e_key_dd)
            return {"algorithm": alg, "recipient": self._key_entries[i].public}
        pwd = self.e_pwd.get_text()
        if not pwd:
            raise UserError("Choose a password to encrypt with.", self.e_pwd)
        return {"algorithm": alg, "password": pwd}

    def _on_hide(self, *_):
        if self._busy:
            return
        if self.carrier_path is None:
            self.toast(ToastKind.ERROR, "Choose the carrier file first.")
            return
        if self.secret_path is None:
            self.toast(ToastKind.ERROR, "Choose the file you want to hide.")
            return
        try:
            kwargs = self._enc_kwargs()
        except UserError as exc:
            self._user_error(exc)
            return
        carrier, secret = self.carrier_path, self.secret_path
        tmp_out = carrier.with_name(f".{carrier.name}.vault-tmp{carrier.suffix}")

        def on_progress(done, total):
            GLib.idle_add(self._set_progress, done, total)

        def work():
            return fv.hide(carrier, secret, tmp_out, progress=on_progress, **kwargs)

        def ok(result: fv.HideResult):
            self._hidden_result = result
            self.progress_rev.set_reveal_child(False)
            kind = "ZIP archive" if result.mode == "zip" else carrier.suffix.upper().lstrip(".") or "file"
            self.result_lbl.set_label(
                f"“{result.hidden_name}” ({_human(result.hidden_size)}) is now hidden inside "
                f"{carrier.name} ({kind}, {_human(result.out_size)} total). Save it, then share or store it "
                "like any normal file.")
            self.result_rev.set_reveal_child(True)
            self._flash(self.result_pane)
            self.toast(ToastKind.SUCCESS, "File hidden.")

        def on_error(_msg):
            self.progress_rev.set_reveal_child(False)
            tmp_out.unlink(missing_ok=True)

        self.progress_rev.set_reveal_child(True)
        self._set_progress(0, 1)
        self._async(self.hide_btn, work, ok, "Encrypting and hiding…", on_error=on_error)

    def _set_progress(self, done: int, total: int):
        frac = 0.0 if total <= 0 else min(1.0, done / total)
        self.progress_bar.set_fraction(frac)
        self.progress_bar.set_text(f"{_human(done)} / {_human(total)}")
        return False

    def _on_save_result(self, *_):
        result = self._hidden_result
        if result is None:
            return
        dlg = Gtk.FileDialog()
        dlg.set_initial_name(self.carrier_path.name)
        dlg.set_filters(_any_file_filters())

        def done(d, res):
            try:
                f = d.save_finish(res)
            except GLib.Error:
                return
            try:
                data = Path(result.out_path).read_bytes()
                f.replace_contents(data, None, False, Gio.FileCreateFlags.NONE, None)
            except (GLib.Error, OSError) as exc:
                self.toast(ToastKind.ERROR, f"Couldn't save: {exc}")
                return
            self.save_btn.done()
            self.toast(ToastKind.SUCCESS, f"Saved: {f.get_basename()}")
            Path(result.out_path).unlink(missing_ok=True)
            self._hidden_result = None
        dlg.save(self, None, done)

    # ------------------------------------------------------------------ reveal
    def _open_card(self) -> Gtk.Widget:
        pane, _hdr = make_pane("Reveal a hidden file")
        self.open_pane = pane
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        lbl = Gtk.Label(xalign=0, wrap=True,
                        label="1. Open the file that hides something (or drop it here).  "
                              "2. Enter its password or key passphrase.  3. Save the original file.")
        lbl.add_css_class("sm-dim")
        box.append(lbl)
        self.open_btn = ActionButton("Open file…", "folder")
        self.open_btn.connect("clicked", self._open_file)
        box.append(self.open_btn)

        u = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        u.append(Gtk.Separator())
        self.open_file_lbl = Gtk.Label(xalign=0, wrap=True)
        self.open_file_lbl.add_css_class("pane-title")
        u.append(self.open_file_lbl)
        self.open_info_lbl = Gtk.Label(xalign=0, wrap=True)
        self.open_info_lbl.add_css_class("sm-dim")
        u.append(self.open_info_lbl)
        self.open_entry = Gtk.PasswordEntry(show_peek_icon=True)
        self.open_entry.add_css_class("sm-entry")
        self.open_entry.connect("activate", lambda *_: self._on_reveal())
        self.open_entry.connect("changed", lambda *_: self.open_entry.remove_css_class("invalid"))
        u.append(self.open_entry)
        self.reveal_btn = ActionButton("Reveal", "unlock", primary=True, busy_label="Decrypting…",
                                       done_label="Revealed")
        self.reveal_btn.connect("clicked", self._on_reveal)
        u.append(self.reveal_btn)
        self.open_unlock_rev = self._plain_reveal(u)
        box.append(self.open_unlock_rev)
        pane.append(padded(box))

        drop = Gtk.DropTarget.new(Gio.File, Gdk.DragAction.COPY)
        drop.connect("drop", lambda t, v, x, y: self._load_open(v) if isinstance(v, Gio.File) else False)
        pane.add_controller(drop)
        return pane

    def _open_file(self, *_):
        if self._busy:
            return
        dlg = Gtk.FileDialog()
        dlg.set_title("Open a file that hides something")
        dlg.set_filters(_any_file_filters())
        dlg.open(self, None, self._on_open_chosen)

    def _on_open_chosen(self, dlg, res):
        try:
            f = dlg.open_finish(res)
        except GLib.Error:
            return
        self._load_open(f)

    def _load_open(self, f: Gio.File):
        path = f.get_path()
        if not path:
            self.toast(ToastKind.ERROR, "Choose a file that is stored on this computer.")
            return
        p = Path(path)
        info = fv.inspect_carrier(p)
        if info is None:
            self.toast(ToastKind.ERROR, "Nothing is hidden in that file.")
            return
        self.open_path, self.open_info = p, info
        self.revealed_rev.set_reveal_child(False)
        self._revealed_tmp = None
        self.open_file_lbl.set_label(f"{p.name}  ·  {_human(p.stat().st_size)}")
        self.open_entry.set_text("")
        self._can_open = True
        if info.is_pq:
            e = self.main.keystore.find(info.key_id)
            if e is not None and e.own:
                self.open_info_lbl.set_label(f"Hidden for your post-quantum key “{e.name}”. "
                                             "Enter that key’s passphrase.")
                self._placeholder("Key passphrase")
            elif e is not None:
                self.open_info_lbl.set_label(f"This was hidden for your contact “{e.name}”, so only "
                                             f"{e.name} can reveal it.")
                self._can_open = False
            else:
                self.open_info_lbl.set_label("This was hidden for a post-quantum key that isn’t on this "
                                             "computer. Import the key pair from Keys, or ask the sender "
                                             "to use your public key.")
                self._can_open = False
        else:
            self.open_info_lbl.set_label("Protected with a password. Enter it to reveal the hidden file.")
            self._placeholder("Password")
        self.open_entry.set_visible(self._can_open)
        self.reveal_btn.set_visible(self._can_open)
        self.open_unlock_rev.set_reveal_child(True)
        if self._can_open:
            GLib.idle_add(lambda: (self.open_entry.grab_focus(), False)[1])
        else:
            self.toast(ToastKind.WARNING, "You don’t have the key for this file.")

    def _placeholder(self, text: str):
        try:
            self.open_entry.set_property("placeholder-text", text)
        except TypeError:
            pass

    def _on_reveal(self, *_):
        if self._busy or self.open_path is None or not self._can_open:
            return
        secret_text = self.open_entry.get_text()
        info = self.open_info
        if not secret_text:
            self._user_error(UserError(
                "Enter the key passphrase." if info.is_pq else "Enter the password.", self.open_entry))
            return
        path = self.open_path
        kwargs = {}
        if info.is_pq:
            try:
                kwargs["secret"] = self.main.keystore.unlock(info.key_id, secret_text)
            except ValueError as exc:
                self._async_fail(str(exc))
                return
        else:
            kwargs["password"] = secret_text
        tmp_out = path.with_name(f".{path.name}.revealed-tmp")

        def on_progress(done, total):
            GLib.idle_add(self._set_progress, done, total)

        def work():
            meta = fv.extract(path, tmp_out, progress=on_progress, **kwargs)
            return meta, tmp_out

        def ok(res):
            meta, out = res
            self._revealed_tmp, self._revealed_meta = out, meta
            self.reveal_btn.done()
            self.revealed_name_lbl.set_label(f"{meta.name}  ·  {_human(meta.size)}")
            self.revealed_rev.set_reveal_child(True)
            self._flash(self.open_pane)
            self.toast(ToastKind.SUCCESS, f"Revealed “{meta.name}”.")

        def failed_hint(_msg):
            self.open_entry.add_css_class("invalid")
            self.open_entry.grab_focus()
            tmp_out.unlink(missing_ok=True)
        self._async(self.reveal_btn, work, ok, "Decrypting…", on_error=failed_hint)

    def _async_fail(self, msg: str):
        self.toast(ToastKind.ERROR, msg)
        self.open_entry.add_css_class("invalid")

    def _revealed_card(self) -> Gtk.Widget:
        pane, _hdr = make_pane("Original file restored")
        self.revealed_pane = pane
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self.revealed_name_lbl = Gtk.Label(xalign=0, wrap=True)
        self.revealed_name_lbl.add_css_class("sm-dim")
        box.append(self.revealed_name_lbl)
        self.revealed_save = ActionButton("Save…", "save", primary=True, done_label="Saved")
        self.revealed_save.connect("clicked", self._on_save_revealed)
        box.append(self.revealed_save)
        pane.append(padded(box))
        return pane

    def _on_save_revealed(self, *_):
        if self._revealed_tmp is None or self._revealed_meta is None:
            return
        dlg = Gtk.FileDialog()
        dlg.set_initial_name(self._revealed_meta.name)
        dlg.set_filters(_any_file_filters())

        def done(d, res):
            try:
                f = d.save_finish(res)
            except GLib.Error:
                return
            try:
                data = self._revealed_tmp.read_bytes()
                f.replace_contents(data, None, False, Gio.FileCreateFlags.NONE, None)
            except (GLib.Error, OSError) as exc:
                self.toast(ToastKind.ERROR, f"Couldn't save: {exc}")
                return
            self.revealed_save.done()
            self.toast(ToastKind.SUCCESS, f"Saved: {f.get_basename()}")
        dlg.save(self, None, done)

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
            except (fv.VaultError, ValueError, OSError, RuntimeError) as exc:
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

    def _on_close(self, *_):
        self.main.vault = None
        return False
