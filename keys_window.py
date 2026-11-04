"""Post-quantum key manager: create your key pair, copy public keys, add contacts."""

from __future__ import annotations

import threading
from typing import Callable

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Adw, GLib  # noqa: E402

import crypto_engine as ce
from keystore import KeyEntry, KeyStore
from widgets import (ActionButton, ToastHost, ToastKind, confirm, make_icon, make_pane,
                     padded, spacer, traffic_lights)


class KeysWindow(Adw.Window):
    def __init__(self, parent: Gtk.Window, store: KeyStore, on_changed: Callable[[], None]):
        super().__init__(transient_for=parent, modal=False)
        self.store, self.on_changed = store, on_changed
        self.set_title("Keys")
        self.set_default_size(560, 720)
        self.set_size_request(360, 480)

        view = Adw.ToolbarView()
        header = Adw.HeaderBar()
        header.set_show_start_title_buttons(False)
        header.set_show_end_title_buttons(False)
        header.pack_start(traffic_lights(self, only_close=True))
        header.set_title_widget(Adw.WindowTitle(title="Keys", subtitle="Post-quantum"))
        view.add_top_bar(header)

        self.toasts = ToastHost()
        overlay = Gtk.Overlay()
        col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        for side in ("top", "bottom", "start", "end"):
            getattr(col, f"set_margin_{side}")(16)
        col.append(self._intro())
        col.append(self._list_card())
        col.append(self._create_card())
        col.append(self._contact_card())
        clamp = Adw.Clamp(maximum_size=620)
        clamp.set_child(col)
        sw = Gtk.ScrolledWindow()
        sw.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        sw.set_child(clamp)
        overlay.set_child(sw)
        overlay.add_overlay(self.toasts)
        view.set_content(overlay)
        self.set_content(view)
        self.refresh()

    # -- sections --------------------------------------------------------------
    def _intro(self) -> Gtk.Widget:
        lbl = Gtk.Label(xalign=0)
        lbl.set_wrap(True)
        lbl.add_css_class("sm-dim")
        lbl.set_label(
            "Post-quantum keys let people write to you without sharing a password. "
            "Messages are protected with ML-KEM-768 (NIST FIPS 203) combined with X25519, "
            "so an attacker would have to break both – even with a quantum computer.")
        return lbl

    def _list_card(self) -> Gtk.Widget:
        pane, _hdr = make_pane("Your keys and contacts")
        self.listbox = Gtk.ListBox()
        self.listbox.set_selection_mode(Gtk.SelectionMode.NONE)
        self.listbox.add_css_class("key-list")
        pane.append(self.listbox)
        self.empty = Gtk.Label(label="No keys yet. Create your key pair below.")
        self.empty.add_css_class("sm-dim")
        self.empty.set_margin_top(18); self.empty.set_margin_bottom(18)
        pane.append(self.empty)
        return pane

    def _entry(self, placeholder: str) -> Gtk.Entry:
        e = Gtk.Entry(placeholder_text=placeholder)
        e.add_css_class("sm-entry")
        return e

    def _create_card(self) -> Gtk.Widget:
        pane, _hdr = make_pane("Create your key pair")
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.new_name = self._entry("Name, e.g. My laptop")
        box.append(self.new_name)
        self.new_pass = Gtk.PasswordEntry(show_peek_icon=True)
        self.new_pass.add_css_class("sm-entry")
        try:
            self.new_pass.set_property("placeholder-text", "Passphrase (8+ characters)")
        except TypeError:
            pass
        box.append(self.new_pass)
        note = Gtk.Label(xalign=0, wrap=True,
                         label="Your secret key never leaves this computer and is locked with this "
                               "passphrase. You'll need it to decrypt.")
        note.add_css_class("sm-dim"); note.add_css_class("sm-small")
        box.append(note)
        self.create_btn = ActionButton("Create key pair", "key", primary=True,
                                       busy_label="Generating…", done_label="Created")
        self.create_btn.set_margin_top(4)
        self.create_btn.connect("clicked", self._on_create)
        box.append(self.create_btn)
        pane.append(padded(box))
        return pane

    def _contact_card(self) -> Gtk.Widget:
        pane, _hdr = make_pane("Add a contact")
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.c_name = self._entry("Contact name")
        box.append(self.c_name)
        self.c_key = Gtk.TextView()
        self.c_key.set_wrap_mode(Gtk.WrapMode.CHAR)
        self.c_key.add_css_class("mono"); self.c_key.add_css_class("keybox")
        for side in ("top", "bottom", "left", "right"):
            getattr(self.c_key, f"set_{side}_margin")(8)
        sw = Gtk.ScrolledWindow()
        sw.set_min_content_height(84)
        sw.add_css_class("keybox-frame")
        sw.set_child(self.c_key)
        box.append(sw)
        hint = Gtk.Label(xalign=0, wrap=True, label="Paste the public key your contact sent you (starts with smpk1:).")
        hint.add_css_class("sm-dim"); hint.add_css_class("sm-small")
        box.append(hint)
        self.add_btn = ActionButton("Add contact", "plus", done_label="Added")
        self.add_btn.connect("clicked", self._on_add_contact)
        box.append(self.add_btn)
        pane.append(padded(box))
        backend = ce.KEM_BACKEND or "unavailable"
        foot = Gtk.Label(xalign=0, label=f"ML-KEM-768 + X25519 · AES-256-GCM / ChaCha20-Poly1305 · {backend}")
        foot.add_css_class("sm-dim"); foot.add_css_class("sm-small")
        foot.set_margin_start(14); foot.set_margin_bottom(10)
        pane.append(foot)
        return pane

    # -- list ------------------------------------------------------------------
    def refresh(self):
        child = self.listbox.get_first_child()
        while child:
            nxt = child.get_next_sibling()
            self.listbox.remove(child)
            child = nxt
        entries = self.store.entries()
        self.empty.set_visible(not entries)
        self.listbox.set_visible(bool(entries))
        for e in entries:
            self.listbox.append(self._row(e))

    def _row(self, e: KeyEntry) -> Gtk.Widget:
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        row.add_css_class("key-row")
        row.append(make_icon("key", 18))
        col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        col.set_hexpand(True)
        name = Gtk.Label(label=e.name, xalign=0)
        name.add_css_class("pane-title")
        name.set_ellipsize(3)
        col.append(name)
        sub = Gtk.Label(xalign=0, label=f"{'Yours' if e.own else 'Contact'} · ID {e.short_id}")
        sub.add_css_class("sm-dim"); sub.add_css_class("sm-small")
        col.append(sub)
        row.append(col)
        copy = ActionButton("Copy public key", "copy", style="sm-tool", done_label="Copied",
                            tooltip="Copy this public key to send to others" if e.own else "Copy this public key")
        copy.connect("clicked", lambda *_: self._copy(e, copy))
        row.append(copy)
        rm = Gtk.Button()
        rm.add_css_class("sm-tool")
        rm.set_child(make_icon("trash", 14))
        rm.set_tooltip_text("Delete")
        rm.update_property([Gtk.AccessibleProperty.LABEL], [f"Delete {e.name}"])
        rm.connect("clicked", lambda *_: self._delete(e))
        row.append(rm)
        return row

    def _copy(self, e: KeyEntry, btn: ActionButton):
        self.get_clipboard().set(ce.public_to_text(e.public))
        btn.done(1400)
        self.toasts.show_toast(ToastKind.SUCCESS, "Public key copied. Safe to share.")

    def _delete(self, e: KeyEntry):
        body = ("This permanently removes your secret key. Messages encrypted to it can no longer be decrypted."
                if e.own else "This contact will be removed from your list.")
        confirm(self, f"Delete “{e.name}”?", body, "Delete",
                lambda: self._do_delete(e))

    def _do_delete(self, e: KeyEntry):
        self.store.remove(e.key_id)
        self.refresh()
        self.on_changed()
        self.toasts.show_toast(ToastKind.INFO, f"Deleted “{e.name}”")

    # -- actions ---------------------------------------------------------------
    def _on_create(self, *_):
        name, phrase = self.new_name.get_text(), self.new_pass.get_text()
        if not name.strip():
            self.new_name.add_css_class("invalid"); self.new_name.grab_focus()
            self.toasts.show_toast(ToastKind.ERROR, "Give the key a name.")
            return
        if len(phrase) < 8:
            self.new_pass.add_css_class("invalid"); self.new_pass.grab_focus()
            self.toasts.show_toast(ToastKind.ERROR, "Use a passphrase of at least 8 characters.")
            return
        self.new_name.remove_css_class("invalid"); self.new_pass.remove_css_class("invalid")
        self.create_btn.busy()

        def work():
            try:
                entry, err = self.store.generate(name, phrase), None
            except (ValueError, OSError) as exc:
                entry, err = None, str(exc)
            GLib.idle_add(self._created, entry, err)
        threading.Thread(target=work, daemon=True).start()

    def _created(self, entry, err):
        if err:
            self.create_btn.reset()
            self.toasts.show_toast(ToastKind.ERROR, err)
            return False
        self.create_btn.done()
        self.new_name.set_text(""); self.new_pass.set_text("")
        self.refresh(); self.on_changed(entry)
        self.toasts.show_toast(ToastKind.SUCCESS, f"Key pair “{entry.name}” created. Share its public key.")
        return False

    def _on_add_contact(self, *_):
        buf = self.c_key.get_buffer()
        text = buf.get_text(*buf.get_bounds(), False)
        try:
            entry = self.store.add_contact(self.c_name.get_text(), text)
        except ValueError as exc:
            self.toasts.show_toast(ToastKind.ERROR, str(exc))
            return
        except OSError:
            self.toasts.show_toast(ToastKind.ERROR, "Couldn't save the key file.")
            return
        self.add_btn.done()
        self.c_name.set_text(""); buf.set_text("")
        self.refresh(); self.on_changed(entry)
        self.toasts.show_toast(ToastKind.SUCCESS, f"Added “{entry.name}”")
