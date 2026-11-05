#!/usr/bin/env python3
"""
Secure Message – encrypt and decrypt short messages between trusted colleagues.

GTK4 + libadwaita, styled after macOS. One screen, two columns:
    left  – your text, the shared password, the algorithm and the two actions
    right – the result, ready to copy
"""

from __future__ import annotations

import sys
import threading
import traceback
import unicodedata

# ---------------------------------------------------------------------------
# Bootstrap first (before any Gtk import)
# ---------------------------------------------------------------------------
from bootstrap import ensure_ready

if "--selftest" in sys.argv:
    # Used by the build pipeline to verify a packaged build; see selftest.py
    import selftest
    _i = sys.argv.index("--selftest")
    _report = sys.argv[_i + 1] if len(sys.argv) > _i + 1 else "selftest-report.txt"
    sys.exit(selftest.run(_report))

if not ensure_ready():
    print("Bootstrap failed. Cannot start.")
    sys.exit(1)

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Adw, Gio, GLib, Gdk  # noqa: E402

import crypto_engine as ce  # noqa: E402
from crypto_engine import Algorithm, list_algorithms  # noqa: E402
from keystore import KeyStore  # noqa: E402
from protection import MIN_PASSWORD_LENGTH, DecParams, EncParams, UserError, decrypt_kwargs  # noqa: E402
from settings import Settings  # noqa: E402
from theme import build_css  # noqa: E402
from widgets import (ActionButton, ToastHost, ToastKind, load_github_avatar,  # noqa: E402
                     make_icon, register_icons, traffic_lights)

APP_ID = "com.securemessage.app"
APP_VERSION = "2.0.0"
DEVELOPER = "Mehran Ghadirian"
DEVELOPER_URL = "https://github.com/mehranqadirian"
MAX_DETECT_CHARS = 4_000_000


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def text_direction(text: str) -> Gtk.TextDirection:
    """Direction of the first strongly-directional character (Persian/Arabic -> RTL)."""
    for ch in text[:400]:
        kind = unicodedata.bidirectional(ch)
        if kind == "L":
            return Gtk.TextDirection.LTR
        if kind in ("R", "AL"):
            return Gtk.TextDirection.RTL
    return Gtk.TextDirection.LTR


def compact(text: str) -> str:
    """Remove every whitespace character (pasted tokens are often line-wrapped)."""
    return "".join(text.split())


def looks_encrypted(text: str) -> bool:
    token = compact(text)
    if not (40 <= len(token) <= MAX_DETECT_CHARS):
        return False
    try:
        ce.inspect_token(token)
        return True
    except ValueError:
        return False


def buffer_text(buf: Gtk.TextBuffer) -> str:
    start, end = buf.get_bounds()
    return buf.get_text(start, end, False)


def count_label(n: int) -> str:
    if n == 0:
        return "No text"
    return f"{n:,} character" + ("" if n == 1 else "s")


# ---------------------------------------------------------------------------
# Main window
# ---------------------------------------------------------------------------

class MainWindow(Adw.ApplicationWindow):
    def __init__(self, app: "SecureMessageApp"):
        super().__init__(application=app)
        self.app = app
        self.settings = app.settings
        self.set_title("Secure Message")
        self.set_default_size(1080, 700)
        self.set_size_request(360, 480)

        self._busy = False
        self._detect_timer = 0
        self._flash_timer = 0
        self._invalid_timers: dict[Gtk.Widget, int] = {}
        self._result_kind: str | None = None
        self.keystore = KeyStore()
        self.studio = None
        self.files_win = None
        self.keys_win = None
        self.method = self.settings.get("method") if self.settings.get("method") in ("password", "pq") else "password"
        if self.method == "pq" and not ce.pq_available():
            self.method = "password"

        self._build_actions()
        self._build_ui()
        self._build_breakpoint()
        self.refresh_keys()
        self._apply_method(initial=True)
        self._refresh_state()
        self.msg_view.grab_focus()

    # ------------------------------------------------------------------ UI
    def _build_ui(self):
        view = Adw.ToolbarView()
        view.add_top_bar(self._build_header())
        view.add_bottom_bar(self._build_action_bar())

        self.toasts = ToastHost()
        overlay = Gtk.Overlay()
        overlay.set_child(self._build_content())
        overlay.add_overlay(self.toasts)
        view.set_content(overlay)
        self.set_content(view)

    def _build_action_bar(self) -> Gtk.Widget:
        bar = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        bar.add_css_class("action-bar")
        clamp = Adw.Clamp(maximum_size=1280, tightening_threshold=960)
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        row.set_margin_start(16); row.set_margin_end(16)
        self.summary_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.summary_box.set_hexpand(True)
        shield = make_icon("shield", 14)
        shield.add_css_class("sm-dim")
        self.summary_box.append(shield)
        self.summary_lbl = Gtk.Label(xalign=0)
        self.summary_lbl.add_css_class("sm-dim"); self.summary_lbl.add_css_class("sm-small")
        self.summary_lbl.set_ellipsize(3)
        self.summary_lbl.set_hexpand(True)
        self.summary_box.append(self.summary_lbl)
        row.append(self.summary_box)
        self.dec_btn = ActionButton("Decrypt", "unlock", busy_label="Decrypting…",
                                    done_label="Decrypted", tooltip="Decrypt an encrypted message (Ctrl+D)")
        self.dec_btn.connect("clicked", lambda *_: self.run("decrypt"))
        self.enc_btn = ActionButton("Encrypt", "lock", primary=True, busy_label="Encrypting…",
                                    done_label="Encrypted", tooltip="Encrypt the text (Ctrl+E)")
        self.enc_btn.connect("clicked", lambda *_: self.run("encrypt"))
        for b in (self.dec_btn, self.enc_btn):
            b.set_size_request(132, -1)
            row.append(b)
        clamp.set_child(row)
        bar.append(clamp)
        return bar

    def _build_header(self) -> Adw.HeaderBar:
        header = Adw.HeaderBar()
        header.set_show_start_title_buttons(False)   # we draw our own traffic lights
        header.set_show_end_title_buttons(False)
        header.pack_start(traffic_lights(self))
        header.set_title_widget(Adw.WindowTitle(title="Secure Message",
                                                subtitle="Encrypt and decrypt text"))

        # Help / about popover
        help_btn = Gtk.MenuButton()
        help_btn.add_css_class("sm-header-btn")
        help_btn.set_child(make_icon("help", 16))
        help_btn.set_tooltip_text("Help")
        help_btn.update_property([Gtk.AccessibleProperty.LABEL], ["Help"])
        help_btn.set_popover(self._build_help_popover())
        header.pack_end(help_btn)

        # Light / dark toggle: sun and moon crossfade inside one button
        self.theme_stack = Gtk.Stack()
        self.theme_stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self.theme_stack.set_transition_duration(260)
        self.theme_stack.add_named(make_icon("moon", 16), "moon")
        self.theme_stack.add_named(make_icon("sun", 16), "sun")
        self.theme_btn = Gtk.Button()
        self.theme_btn.add_css_class("sm-header-btn")
        self.theme_btn.set_child(self.theme_stack)
        self.theme_btn.connect("clicked", lambda *_: self.app.toggle_theme())
        header.pack_end(self.theme_btn)
        self.update_theme_button(self.app.is_dark())
        return header

    def _build_help_popover(self) -> Gtk.Popover:
        pop = Gtk.Popover()
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        box.set_margin_top(14); box.set_margin_bottom(12)
        box.set_margin_start(16); box.set_margin_end(16)

        title = Gtk.Label(label="How it works", xalign=0)
        title.add_css_class("pane-title")
        box.append(title)

        steps = [
            "Type or paste your text on the left.",
            "Enter the shared password. Send it separately, never with the message.",
            "Press Encrypt for plain text, or Decrypt for an encrypted message.",
        ]
        for i, text in enumerate(steps, 1):
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
            num = Gtk.Label(label=str(i))
            num.add_css_class("help-step-num")
            num.set_valign(Gtk.Align.START)
            row.append(num)
            lbl = Gtk.Label(label=text, xalign=0)
            lbl.set_wrap(True); lbl.set_max_width_chars(34)
            row.append(lbl)
            box.append(row)

        box.append(Gtk.Separator())
        for keys, what in (("Ctrl+E", "Encrypt"), ("Ctrl+D", "Decrypt"),
                           ("Ctrl+Shift+C", "Copy result")):
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            what_lbl = Gtk.Label(label=what, xalign=0)
            what_lbl.set_hexpand(True)
            what_lbl.add_css_class("sm-dim")
            row.append(what_lbl)
            kbd = Gtk.Label(label=keys)
            kbd.add_css_class("kbd")
            row.append(kbd)
            box.append(row)

        box.append(Gtk.Separator())
        dev = Gtk.Button()
        dev.add_css_class("flat"); dev.add_css_class("dev-card")
        dev.set_tooltip_text(DEVELOPER_URL)
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        avatar = Adw.Avatar(size=40, text=DEVELOPER, show_initials=True)
        row.append(avatar)
        col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        col.set_valign(Gtk.Align.CENTER)
        who = Gtk.Label(label=DEVELOPER, xalign=0)
        who.add_css_class("pane-title")
        col.append(who)
        link = Gtk.Label(label="github.com/mehranqadirian", xalign=0)
        link.add_css_class("sm-dim"); link.add_css_class("sm-small")
        col.append(link)
        row.append(col)
        dev.set_child(row)
        dev.connect("clicked", lambda *_: self._open_url(DEVELOPER_URL))
        box.append(dev)
        loaded = []
        pop.connect("show", lambda *_: loaded or (loaded.append(1), load_github_avatar(avatar)))

        about = Gtk.Button(label="About Secure Message")
        about.add_css_class("flat")
        about.connect("clicked", lambda *_: (pop.popdown(), self.show_about()))
        box.append(about)
        pop.set_child(box)
        return pop

    # -- content ------------------------------------------------------------
    def _build_content(self) -> Gtk.Widget:
        self.columns = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=16)
        self.columns.set_homogeneous(True)
        self.columns.set_vexpand(True)

        left = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        left.append(self._build_message_pane())
        left.append(self._build_controls_pane())
        self.columns.append(left)
        self.columns.append(self._build_result_pane())

        clamp = Adw.Clamp()
        clamp.set_maximum_size(1280)
        clamp.set_tightening_threshold(960)
        clamp.set_vexpand(True)
        clamp.set_child(self.columns)
        for side in ("top", "bottom", "start", "end"):
            getattr(self.columns, f"set_margin_{side}")(16)

        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroller.set_child(clamp)
        return scroller

    def _build_breakpoint(self):
        bp = Adw.Breakpoint.new(Adw.BreakpointCondition.parse("max-width: 760sp"))
        bp.add_setter(self.columns, "orientation", Gtk.Orientation.VERTICAL)
        bp.add_setter(self.columns, "homogeneous", False)
        bp.add_setter(self.toasts, "halign", Gtk.Align.CENTER)
        bp.add_setter(self.summary_box, "visible", False)
        bp.add_setter(self.dec_btn, "hexpand", True)
        bp.add_setter(self.enc_btn, "hexpand", True)
        self.add_breakpoint(bp)

    # -- panes ----------------------------------------------------------------
    @staticmethod
    def _pane(title: str):
        pane = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        pane.add_css_class("pane")
        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        header.add_css_class("pane-header")
        lbl = Gtk.Label(label=title, xalign=0)
        lbl.add_css_class("pane-title")
        header.append(lbl)
        pane.append(header)
        return pane, header

    @staticmethod
    def _spacer() -> Gtk.Widget:
        s = Gtk.Box()
        s.set_hexpand(True)
        return s

    @staticmethod
    def _footer(initial: str):
        footer = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        footer.add_css_class("pane-footer")
        lbl = Gtk.Label(label=initial, xalign=0)
        lbl.add_css_class("sm-dim"); lbl.add_css_class("sm-small")
        lbl.set_ellipsize(3)  # Pango.EllipsizeMode.END
        footer.append(lbl)
        return footer, lbl

    @staticmethod
    def _text_area(view: Gtk.TextView, overlay_child: Gtk.Widget) -> Gtk.Widget:
        sw = Gtk.ScrolledWindow()
        sw.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        sw.set_min_content_height(190)
        sw.set_vexpand(True)
        sw.set_child(view)
        ov = Gtk.Overlay()
        ov.set_vexpand(True)
        ov.set_child(sw)
        overlay_child.set_can_target(False)
        ov.add_overlay(overlay_child)
        return ov

    @staticmethod
    def _make_view(editable: bool) -> Gtk.TextView:
        tv = Gtk.TextView()
        tv.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        tv.set_editable(editable)
        tv.set_cursor_visible(editable)
        tv.set_accepts_tab(False)
        tv.set_top_margin(12); tv.set_bottom_margin(12)
        tv.set_left_margin(14); tv.set_right_margin(14)
        return tv

    def _build_message_pane(self) -> Gtk.Widget:
        self.msg_pane, header = self._pane("Message")
        self.msg_pane.set_vexpand(True)

        # "Looks encrypted" hint appears next to the title
        self.detect_chip = Gtk.Label(label="Encrypted text detected")
        self.detect_chip.add_css_class("chip"); self.detect_chip.add_css_class("info")
        self.detect_rev = Gtk.Revealer()
        self.detect_rev.set_transition_type(Gtk.RevealerTransitionType.CROSSFADE)
        self.detect_rev.set_transition_duration(220)
        self.detect_rev.set_child(self.detect_chip)
        header.append(self.detect_rev)
        header.append(self._spacer())

        self.voice_btn = ActionButton("Voice", "mic", style="sm-tool",
                                      tooltip="Record, transcribe and encrypt voice")
        self.voice_btn.connect("clicked", self.open_studio)
        header.append(self.voice_btn)
        self.files_btn = ActionButton("Files", "folder", style="sm-tool",
                                      tooltip="Encrypt or hide any file: images, video, audio, archives, PDFs…")
        self.files_btn.connect("clicked", self.open_files)
        header.append(self.files_btn)
        self.paste_btn = ActionButton("Paste", "paste", style="sm-tool", done_label="Pasted",
                                      tooltip="Replace the message with the clipboard")
        self.paste_btn.connect("clicked", self.on_paste)
        header.append(self.paste_btn)
        self.clear_btn = ActionButton("Clear", "trash", style="sm-tool",
                                      tooltip="Clear the message and the result")
        self.clear_btn.connect("clicked", self.on_clear)
        header.append(self.clear_btn)

        self.msg_view = self._make_view(True)
        self.msg_buf = self.msg_view.get_buffer()
        self.msg_buf.connect("changed", self._on_message_changed)

        self.msg_hint = Gtk.Label(
            label="Type or paste your text here.\nPlain text can be encrypted; "
                  "an encrypted message can be decrypted.")
        self.msg_hint.add_css_class("sm-dim")
        self.msg_hint.set_wrap(True); self.msg_hint.set_xalign(0); self.msg_hint.set_yalign(0)
        self.msg_hint.set_halign(Gtk.Align.START); self.msg_hint.set_valign(Gtk.Align.START)
        self.msg_hint.set_margin_top(12); self.msg_hint.set_margin_start(14)
        self.msg_hint.set_margin_end(14)
        self.msg_pane.append(self._text_area(self.msg_view, self.msg_hint))

        footer, self.msg_count = self._footer("No text")
        self.msg_pane.append(footer)
        return self.msg_pane

    def _segmented(self, labels, active: int, on_toggle) -> tuple[Gtk.Box, list]:
        seg = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=2)
        seg.add_css_class("segmented")
        seg.set_homogeneous(True)
        buttons, first = [], None
        for name in labels:
            b = Gtk.ToggleButton(label=name)
            if first is None:
                first = b
            else:
                b.set_group(first)
            seg.append(b)
            buttons.append(b)
        buttons[active if 0 <= active < len(buttons) else 0].set_active(True)
        for i, b in enumerate(buttons):                  # connect after the initial state is set
            b.connect("toggled", on_toggle, i)
        return seg, buttons

    def _build_controls_pane(self) -> Gtk.Widget:
        pane = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        pane.add_css_class("pane")
        inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        for side in ("top", "bottom", "start", "end"):
            getattr(inner, f"set_margin_{side}")(14)
        pane.append(inner)

        # Protection method
        cap0 = Gtk.Label(label="Protection", xalign=0)
        cap0.add_css_class("sm-caption")
        inner.append(cap0)
        seg0, self.method_buttons = self._segmented(
            ["Password", "Post-quantum key"], 1 if self.method == "pq" else 0, self._on_method_toggled)
        if not ce.pq_available():
            self.method_buttons[1].set_sensitive(False)
            self.method_buttons[1].set_tooltip_text("Needs the pqcrypto package")
        inner.append(seg0)

        # Key picker (post-quantum only)
        kbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        kbox.set_margin_top(6)
        kcap = Gtk.Label(label="Key", xalign=0)
        kcap.add_css_class("sm-caption")
        kbox.append(kcap)
        krow = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.key_model = Gtk.StringList()
        self.key_dd = Gtk.DropDown(model=self.key_model)
        self.key_dd.set_hexpand(True)
        self.key_dd.add_css_class("sm-dropdown")
        self.key_dd.connect("notify::selected", self._on_key_selected)
        krow.append(self.key_dd)
        keys_btn = ActionButton("Keys", "key", tooltip="Create your key pair or add contacts")
        keys_btn.connect("clicked", self.open_keys)
        krow.append(keys_btn)
        kbox.append(krow)
        self.key_note = Gtk.Label(xalign=0, wrap=True)
        self.key_note.add_css_class("sm-dim"); self.key_note.add_css_class("sm-small")
        kbox.append(self.key_note)
        self.pq_rev = Gtk.Revealer()
        self.pq_rev.set_transition_type(Gtk.RevealerTransitionType.SLIDE_DOWN)
        self.pq_rev.set_transition_duration(260)
        self.pq_rev.set_child(kbox)
        inner.append(self.pq_rev)

        # Password / passphrase
        self.pwd_caption = Gtk.Label(label="Password", xalign=0)
        self.pwd_caption.add_css_class("sm-caption")
        self.pwd_caption.set_margin_top(8)
        inner.append(self.pwd_caption)
        self.pwd = Gtk.PasswordEntry()
        self.pwd.set_show_peek_icon(True)
        self.pwd.add_css_class("sm-entry")
        try:
            self.pwd.set_property("placeholder-text", "Shared password")
        except TypeError:
            pass
        self.pwd.connect("activate", self.on_password_activate)
        self.pwd.connect("changed", lambda *_: self._clear_invalid(self.pwd))
        inner.append(self.pwd)
        self.pwd_note = Gtk.Label(xalign=0, wrap=True)
        self.pwd_note.add_css_class("sm-dim"); self.pwd_note.add_css_class("sm-small")
        inner.append(self.pwd_note)

        # Algorithm (segmented control)
        cap2 = Gtk.Label(label="Cipher", xalign=0)
        cap2.add_css_class("sm-caption")
        cap2.set_margin_top(8)
        inner.append(cap2)
        saved = int(self.settings.get("algorithm") or 0)
        seg, self.alg_buttons = self._segmented(list_algorithms(), saved, self._on_algorithm_toggled)
        inner.append(seg)

        return pane

    def _build_result_pane(self) -> Gtk.Widget:
        self.res_pane, header = self._pane("Result")
        self.res_pane.set_vexpand(True)

        self.res_chip = Gtk.Label()
        self.res_chip.add_css_class("chip")
        self.res_chip_rev = Gtk.Revealer()
        self.res_chip_rev.set_transition_type(Gtk.RevealerTransitionType.CROSSFADE)
        self.res_chip_rev.set_transition_duration(220)
        self.res_chip_rev.set_child(self.res_chip)
        header.append(self.res_chip_rev)
        header.append(self._spacer())

        self.copy_btn = ActionButton("Copy", "copy", style="sm-tool", done_label="Copied",
                                     tooltip="Copy the result (Ctrl+Shift+C)")
        self.copy_btn.connect("clicked", lambda *_: self.on_copy())
        header.append(self.copy_btn)

        self.res_view = self._make_view(False)
        self.res_buf = self.res_view.get_buffer()

        empty = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        empty.set_halign(Gtk.Align.CENTER); empty.set_valign(Gtk.Align.CENTER)
        ic = make_icon("shield", 40)
        ic.add_css_class("empty-icon")
        empty.append(ic)
        t = Gtk.Label(label="No result yet")
        t.add_css_class("empty-title")
        empty.append(t)
        s = Gtk.Label(label="Enter text and a password, then press Encrypt or Decrypt.")
        s.add_css_class("sm-dim"); s.set_wrap(True); s.set_justify(Gtk.Justification.CENTER)
        s.set_max_width_chars(30)
        empty.append(s)
        self.res_empty = empty
        self.res_pane.append(self._text_area(self.res_view, empty))

        footer, self.res_info = self._footer("The result appears here")
        self.res_pane.append(footer)
        return self.res_pane

    # ------------------------------------------------------------- actions
    def _build_actions(self):
        for name, cb, accels in (
            ("encrypt", lambda *_: self.run("encrypt"), ["<Primary>e"]),
            ("decrypt", lambda *_: self.run("decrypt"), ["<Primary>d"]),
            ("copy-result", lambda *_: self.on_copy(), ["<Primary><Shift>c"]),
        ):
            act = Gio.SimpleAction.new(name, None)
            act.connect("activate", cb)
            self.add_action(act)
            self.app.set_accels_for_action(f"win.{name}", accels)

    # ------------------------------------------------------------ feedback
    def toast(self, kind: ToastKind, message: str):
        self.toasts.show_toast(kind, message)

    def update_theme_button(self, dark: bool):
        self.theme_stack.set_visible_child_name("sun" if dark else "moon")
        tip = "Switch to light appearance" if dark else "Switch to dark appearance"
        self.theme_btn.set_tooltip_text(tip)
        self.theme_btn.update_property([Gtk.AccessibleProperty.LABEL], [tip])

    def _mark_invalid(self, widget: Gtk.Widget, focus: Gtk.Widget | None = None):
        widget.add_css_class("invalid")
        old = self._invalid_timers.pop(widget, 0)
        if old:
            GLib.source_remove(old)
        self._invalid_timers[widget] = GLib.timeout_add(2200, self._invalid_timeout, widget)
        (focus or widget).grab_focus()

    def _invalid_timeout(self, widget):
        self._invalid_timers.pop(widget, None)
        widget.remove_css_class("invalid")
        return False

    def _clear_invalid(self, widget: Gtk.Widget):
        widget.remove_css_class("invalid")

    def _flash_result(self):
        self.res_pane.add_css_class("flash")
        if self._flash_timer:
            GLib.source_remove(self._flash_timer)
        self._flash_timer = GLib.timeout_add(900, self._unflash)

    def _unflash(self):
        self._flash_timer = 0
        self.res_pane.remove_css_class("flash")
        return False

    # ------------------------------------------------------------- events
    def _on_algorithm_toggled(self, btn: Gtk.ToggleButton, index: int):
        if btn.get_active():
            self.settings.set("algorithm", index)
            self._summary_changed()

    def _selected_algorithm(self) -> Algorithm:
        for i, b in enumerate(self.alg_buttons):
            if b.get_active():
                return Algorithm(list_algorithms()[i])
        return Algorithm.AES_256_GCM

    def _on_message_changed(self, buf: Gtk.TextBuffer):
        text = buffer_text(buf)
        self.msg_hint.set_visible(buf.get_char_count() == 0)
        self.msg_count.set_text(count_label(buf.get_char_count()))
        self.msg_view.set_direction(text_direction(text))
        self._clear_invalid(self.msg_pane)
        self._refresh_state()
        if self._detect_timer:
            GLib.source_remove(self._detect_timer)
        self._detect_timer = GLib.timeout_add(180, self._detect_cb)

    def _detect_cb(self):
        self._detect_timer = 0
        text = buffer_text(self.msg_buf)
        found = looks_encrypted(text)
        self.detect_rev.set_reveal_child(found)
        if found:
            self.msg_view.add_css_class("mono")
        else:
            self.msg_view.remove_css_class("mono")
        return False

    def _refresh_state(self):
        has_msg = self.msg_buf.get_char_count() > 0
        has_res = self.res_buf.get_char_count() > 0
        self.clear_btn.set_sensitive(has_msg or has_res)
        self.copy_btn.set_sensitive(has_res)
        self.res_empty.set_visible(not has_res)

    def on_password_activate(self, *_):
        """Enter in the password field does what the text calls for."""
        text = buffer_text(self.msg_buf)
        self.run("decrypt" if looks_encrypted(text) else "encrypt")

    def on_clear(self, *_):
        self.msg_buf.set_text("")
        self._set_result_text("", None, "")
        self.msg_view.grab_focus()

    def on_paste(self, *_):
        self.get_clipboard().read_text_async(None, self._on_paste_ready)

    def _on_paste_ready(self, clipboard, result):
        try:
            text = clipboard.read_text_finish(result)
        except GLib.Error:
            text = None
        if not text:
            self.toast(ToastKind.WARNING, "The clipboard has no text to paste.")
            return
        self.msg_buf.set_text(text)
        self.paste_btn.done(1200)
        if looks_encrypted(text):
            self.toast(ToastKind.INFO, "Encrypted message pasted. Enter the password, then press Decrypt.")

    def on_copy(self, *_):
        text = buffer_text(self.res_buf)
        if not text:
            self.toast(ToastKind.WARNING, "There is no result to copy yet.")
            return
        self.get_clipboard().set(text)
        self.copy_btn.done(1500)
        self.toast(ToastKind.SUCCESS, "Copied to clipboard")

    # ------------------------------------------------------ crypto actions
    def run(self, mode: str):
        if self._busy:
            return
        text = buffer_text(self.msg_buf)
        if not text.strip():
            self._mark_invalid(self.msg_pane, self.msg_view)
            self.toast(ToastKind.ERROR, "Type or paste some text first.")
            return
        try:
            if mode == "decrypt":
                token = compact(text)
                try:
                    info = ce.inspect_token(token)
                except ValueError:
                    self._mark_invalid(self.msg_pane, self.msg_view)
                    self.toast(ToastKind.ERROR,
                               "That isn’t an encrypted message. Use Encrypt for plain text.")
                    return
                dp = self.decrypt_params(info.is_pq, info.key_id)
                work = lambda: ce.decrypt_text(token, **decrypt_kwargs(self.keystore, dp))  # noqa: E731
                meta = {"pq": info.is_pq}
            else:
                if looks_encrypted(text):
                    self._mark_invalid(self.msg_pane, self.msg_view)
                    self.toast(ToastKind.WARNING,
                               "This text is already encrypted. Press Decrypt to read it.")
                    return
                ep = self.encrypt_params()
                plain = text.strip()
                work = lambda: ce.encrypt_text(plain, algorithm=ep.algorithm,  # noqa: E731
                                               password=ep.password, recipient=ep.recipient)
                meta = {"pq": ep.is_pq, "weak": ep.weak_password, "label": ep.label}
        except UserError as err:
            self._mark_invalid(err.widget) if err.widget is not None else None
            self.toast(ToastKind.ERROR, str(err))
            return

        self._set_busy(True, mode)
        threading.Thread(target=self._worker, args=(mode, work, meta), daemon=True).start()

    def _worker(self, mode: str, work, meta: dict):
        try:
            result, error = work(), None
        except ValueError as exc:
            result, error = None, str(exc)
        except Exception as exc:  # pragma: no cover - defensive
            result, error = None, f"Unexpected error: {exc}"
        GLib.idle_add(self._finish, mode, result, error, meta)

    # -- protection parameters (shared with the voice studio) ------------------
    def _selected_key(self):
        i = self.key_dd.get_selected()
        return self._key_entries[i] if 0 <= i < len(self._key_entries) else None

    def encrypt_params(self) -> EncParams:
        alg = self._selected_algorithm()
        if self.method == "pq":
            entry = self._selected_key()
            if entry is None:
                raise UserError("Create your key pair or add a contact first (press Keys).", self.key_dd)
            return EncParams(alg, None, entry.public, f"Post-quantum · {alg.value} · to {entry.name}")
        pwd = self.pwd.get_text()
        if not pwd:
            raise UserError("Enter the password to continue.", self.pwd)
        return EncParams(alg, pwd, None, f"Password · {alg.value}", weak_password=len(pwd) < MIN_PASSWORD_LENGTH)

    def decrypt_params(self, is_pq: bool, key_id) -> DecParams:
        """What this encrypted item needs; switches the method to match it."""
        if is_pq:
            if self.method != "pq":
                self.set_method("pq")
            entry = self.keystore.find(key_id) if key_id else None
            if entry is None or not entry.own:
                raise UserError("This was encrypted for a key you don’t have. Ask the sender to use your "
                                "public key.", self.key_dd)
            self._select_key(entry)
            phrase = self.pwd.get_text()
            if not phrase:
                raise UserError("Enter your key passphrase to decrypt.", self.pwd)
            return DecParams(None, key_id, phrase)
        if self.method != "password":
            self.set_method("password")
        pwd = self.pwd.get_text()
        if not pwd:
            raise UserError("Enter the password to continue.", self.pwd)
        return DecParams(pwd, None, None)

    def protection_summary(self) -> str:
        alg = self._selected_algorithm().value
        if self.method == "pq":
            e = self._selected_key()
            who = f"to {e.name}" if e else "no key selected"
            return f"Post-quantum · ML-KEM-768 + X25519 · {alg} · {who}"
        return f"Password · Argon2id · {alg}"

    def set_message_text(self, text: str):
        self.msg_buf.set_text(text)
        self.msg_view.grab_focus()

    def show_text_result(self, plain: str, token: str, label: str):
        """Used by the voice studio after 'transcribe and encrypt'."""
        self.msg_buf.set_text(plain)
        self._set_result_text(token, "encrypted", f"{label} · {count_label(len(token))}")
        self._flash_result()

    # -- method / keys -----------------------------------------------------------
    def _on_method_toggled(self, btn: Gtk.ToggleButton, index: int):
        if btn.get_active():
            self.method = "pq" if index == 1 else "password"
            self.settings.set("method", self.method)
            self._apply_method()

    def set_method(self, method: str):
        self.method_buttons[1 if method == "pq" else 0].set_active(True)

    def _apply_method(self, initial: bool = False):
        pq = self.method == "pq"
        self.pq_rev.set_reveal_child(pq)
        self.pwd_caption.set_label("Key passphrase" if pq else "Password")
        self.pwd_note.set_label("Only needed to decrypt. Encrypting uses the selected public key."
                                if pq else "Share it separately. Never send it with the message.")
        try:
            self.pwd.set_property("placeholder-text", "Unlocks your secret key" if pq else "Shared password")
        except TypeError:
            pass
        self._summary_changed()

    def _summary_changed(self):
        if hasattr(self, "summary_lbl"):
            self.summary_lbl.set_label(self.protection_summary())
        if self.studio is not None:
            self.studio.refresh_summary()
        if self.files_win is not None:
            self.files_win.refresh_summary()

    def _on_key_selected(self, *_):
        e = self._selected_key()
        if e is not None:
            self.settings.set("key", e.key_id.hex())
            self.key_note.set_label(("Your key" if e.own else "Contact") + f" · ID {e.short_id}")
        self._summary_changed()

    def _select_key(self, entry):
        for i, e in enumerate(self._key_entries):
            if e.key_id == entry.key_id:
                self.key_dd.set_selected(i)
                return

    def refresh_keys(self, select=None):
        entries = sorted(self.keystore.entries(), key=lambda e: (not e.own, e.name.lower()))
        self._key_entries = entries
        labels = [f"{e.name}  ({'yours' if e.own else 'contact'})" for e in entries] or ["No keys yet"]
        self.key_model.splice(0, self.key_model.get_n_items(), labels)
        want = select.key_id.hex() if select is not None else self.settings.get("key")
        idx = next((i for i, e in enumerate(entries) if e.key_id.hex() == want), 0)
        if entries:
            self.key_dd.set_selected(idx)
            self._on_key_selected()
        else:
            self.key_note.set_label("Press Keys to create your key pair or add a contact.")
        if self.studio is not None:
            self.studio.refresh_keys()
        if self.files_win is not None:
            self.files_win.refresh_keys()

    def open_keys(self, *_):
        from keys_window import KeysWindow
        if self.keys_win is None:
            self.keys_win = KeysWindow(self, self.keystore, self.refresh_keys)
            self.keys_win.connect("close-request", lambda *_: (setattr(self, "keys_win", None), False)[1])
        self.keys_win.present()

    def open_studio(self, *_):
        if self.studio is not None:
            self.studio.present()
            return
        try:
            from voice_studio import VoiceStudio
            self.studio = VoiceStudio(self)
        except Exception as exc:
            traceback.print_exc()
            self.toast(ToastKind.ERROR, f"Voice isn’t available: {exc}")
            return
        self.studio.present()

    def open_files(self, *_):
        if self.files_win is not None:
            self.files_win.present()
            return
        try:
            from files_studio import FilesStudio
            self.files_win = FilesStudio(self)
            self.files_win.connect("close-request", lambda *_: (setattr(self, "files_win", None), False)[1])
        except Exception as exc:
            traceback.print_exc()
            self.toast(ToastKind.ERROR, f"Files isn’t available: {exc}")
            return
        self.files_win.present()

    def _set_busy(self, busy: bool, mode: str | None = None):
        self._busy = busy
        for b in (self.enc_btn, self.dec_btn):
            b.set_can_target(not busy)
        if busy:
            (self.enc_btn if mode == "encrypt" else self.dec_btn).busy()
        else:
            self.enc_btn.reset()
            self.dec_btn.reset()

    def _finish(self, mode: str, result: str | None, error: str | None, meta: dict):
        self._busy = False
        for b in (self.enc_btn, self.dec_btn):
            b.set_can_target(True)
        btn = self.enc_btn if mode == "encrypt" else self.dec_btn

        if error is not None or result is None:
            btn.reset()
            if mode == "decrypt":
                self._set_result_text("", None, "")
                if error and error.startswith("Wrong key passphrase"):
                    self._mark_invalid(self.pwd)
                    self.toast(ToastKind.ERROR, "Couldn’t decrypt. The key passphrase is wrong.")
                elif meta.get("pq"):
                    self.toast(ToastKind.ERROR,
                               "Couldn’t decrypt. This wasn’t made for your key, or it was changed.")
                else:
                    self._mark_invalid(self.pwd)
                    self.toast(ToastKind.ERROR,
                               "Couldn’t decrypt. The password is wrong, or the message was changed.")
            else:
                self.toast(ToastKind.ERROR, f"Couldn’t encrypt. {error}")
            return False

        if mode == "encrypt":
            self._set_result_text(result, "encrypted", f"{meta['label']} · {count_label(len(result))}")
            btn.done()
            if meta.get("weak"):
                self.toast(ToastKind.WARNING,
                           "Encrypted, but the password is short. Use 12 or more characters.")
            else:
                self.toast(ToastKind.SUCCESS, "Message encrypted" + (" (post-quantum)" if meta.get("pq") else ""))
        else:
            self._set_result_text(result, "decrypted", count_label(len(result)))
            btn.done()
            self.toast(ToastKind.SUCCESS, "Message decrypted")
        self._flash_result()
        return False

    def _set_result_text(self, text: str, kind: str | None, info: str):
        self.res_buf.set_text(text)
        self._result_kind = kind
        self.res_view.set_direction(text_direction(text))
        if kind == "encrypted":
            self.res_view.add_css_class("mono")
        else:
            self.res_view.remove_css_class("mono")
        if kind:
            self.res_chip.set_text("Encrypted" if kind == "encrypted" else "Decrypted")
            if kind == "decrypted":
                self.res_chip.add_css_class("ok")
            else:
                self.res_chip.remove_css_class("ok")
            self.res_info.set_text(info)
        else:
            self.res_info.set_text("The result appears here")
        self.res_chip_rev.set_reveal_child(bool(kind))
        self._refresh_state()

    # --------------------------------------------------------------- about
    @staticmethod
    def _open_url(url: str):
        try:
            Gio.AppInfo.launch_default_for_uri(url, None)
        except GLib.Error:
            pass

    def show_about(self):
        kwargs = dict(
            application_name="Secure Message",
            application_icon=APP_ID,
            developer_name=DEVELOPER,
            developers=[f"{DEVELOPER} {DEVELOPER_URL}"],
            website=DEVELOPER_URL,
            version=APP_VERSION,
            comments="Encrypt and decrypt short messages with a shared password.\n"
                     "Argon2id + AES-256-GCM / ChaCha20-Poly1305, or post-quantum ML-KEM-768 + X25519.",
            copyright=f"© 2025 {DEVELOPER}",
            license_type=Gtk.License.MIT_X11,
        )
        if hasattr(Adw, "AboutDialog"):
            Adw.AboutDialog(**kwargs).present(self)
        else:  # libadwaita < 1.5
            Adw.AboutWindow(transient_for=self, **kwargs).present()


# ---------------------------------------------------------------------------
# Application
# ---------------------------------------------------------------------------

class SecureMessageApp(Adw.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.FLAGS_NONE)
        self.settings = Settings()
        self.css = Gtk.CssProvider()
        self.win: MainWindow | None = None
        self._css_installed = False
        self.connect("activate", self.on_activate)

    def on_activate(self, *_):
        if self.win:
            self.win.present()
            return
        register_icons()
        self._apply_pref(self.settings.get("theme"))
        Adw.StyleManager.get_default().connect("notify::dark", self._on_dark_changed)
        self.set_accels_for_action("app.quit", ["<Primary>q"])
        quit_action = Gio.SimpleAction.new("quit", None)
        quit_action.connect("activate", lambda *_: self.quit())
        self.add_action(quit_action)
        self.win = MainWindow(self)
        self.win.present()

    # -- theme ----------------------------------------------------------------
    def is_dark(self) -> bool:
        return Adw.StyleManager.get_default().get_dark()

    def _apply_pref(self, pref: str):
        scheme = {
            "light": Adw.ColorScheme.FORCE_LIGHT,
            "dark": Adw.ColorScheme.FORCE_DARK,
        }.get(pref, Adw.ColorScheme.DEFAULT)
        Adw.StyleManager.get_default().set_color_scheme(scheme)
        self._reload_css()

    def toggle_theme(self):
        new = "light" if self.is_dark() else "dark"
        self.settings.set("theme", new)
        self._apply_pref(new)

    def _on_dark_changed(self, *_):
        self._reload_css()
        if self.win:
            self.win.update_theme_button(self.is_dark())

    def _reload_css(self):
        css = build_css(self.is_dark())
        if hasattr(self.css, "load_from_string"):
            self.css.load_from_string(css)
        else:
            self.css.load_from_data(css.encode("utf-8"))
        if not self._css_installed:
            Gtk.StyleContext.add_provider_for_display(
                Gdk.Display.get_default(), self.css, Gtk.STYLE_PROVIDER_PRIORITY_USER - 1)
            self._css_installed = True


def main():
    GLib.set_application_name("Secure Message")
    Adw.init()
    app = SecureMessageApp()
    sys.exit(app.run(sys.argv))


if __name__ == "__main__":
    main()
