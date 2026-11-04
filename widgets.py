"""Reusable GTK widgets for SecureMessage."""

from __future__ import annotations

from enum import Enum
from pathlib import Path

import threading
import time
import urllib.request

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Adw, Gio, GLib, Gdk  # noqa: E402

ICONS_DIR = Path(__file__).resolve().parent / "icons"

# ---------------------------------------------------------------------------
# Icons
# ---------------------------------------------------------------------------
# Every icon is a *symbolic* SVG shipped in icons/hicolor/scalable/actions.
# GTK recolours symbolic icons with the widget's CSS `color`, so they are
# correct on light and dark themes alike. If one is missing for any reason the
# matching Adwaita icon is used instead.

_FALLBACK = {
    "lock": "changes-prevent-symbolic",
    "unlock": "changes-allow-symbolic",
    "copy": "edit-copy-symbolic",
    "paste": "edit-paste-symbolic",
    "trash": "user-trash-symbolic",
    "check": "object-select-symbolic",
    "sun": "weather-clear-symbolic",
    "moon": "weather-clear-night-symbolic",
    "info": "dialog-information-symbolic",
    "success": "emblem-ok-symbolic",
    "warning": "dialog-warning-symbolic",
    "error": "dialog-error-symbolic",
    "close": "window-close-symbolic",
    "help": "help-about-symbolic",
    "shield": "security-high-symbolic",
    "minus": "list-remove-symbolic",
    "plus": "list-add-symbolic",
    "mic": "audio-input-microphone-symbolic",
    "stop": "media-playback-stop-symbolic",
    "play": "media-playback-start-symbolic",
    "pause": "media-playback-pause-symbolic",
    "save": "document-save-symbolic",
    "folder": "folder-symbolic",
    "key": "dialog-password-symbolic",
    "wave": "audio-x-generic-symbolic",
}


def register_icons(display: Gdk.Display | None = None) -> None:
    display = display or Gdk.Display.get_default()
    theme = Gtk.IconTheme.get_for_display(display)
    theme.add_search_path(str(ICONS_DIR))
    Gtk.Window.set_default_icon_name("com.securemessage.app")


def make_icon(name: str, size: int = 16) -> Gtk.Image:
    """
    Symbolic icon by short name ("lock", "copy", ...).

    The bundled sm-<name>-symbolic icon is used when it can be found. GTK
    resolves a *list* of names theme-by-theme, so a fallback inside the list
    would win over our icons (they live in hicolor); we therefore pick one
    name up front instead.
    """
    icon_name = f"sm-{name}-symbolic"
    display = Gdk.Display.get_default()
    if display is not None and not Gtk.IconTheme.get_for_display(display).has_icon(icon_name):
        icon_name = _FALLBACK.get(name, "image-missing")
    img = Gtk.Image.new_from_icon_name(icon_name)
    img.set_pixel_size(size)
    return img


def _row(icon: Gtk.Widget, text: str, spacing: int = 7) -> Gtk.Box:
    box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=spacing)
    box.set_halign(Gtk.Align.CENTER)
    box.append(icon)
    lbl = Gtk.Label(label=text)
    box.append(lbl)
    return box


# ---------------------------------------------------------------------------
# Feedback button
# ---------------------------------------------------------------------------

class ActionButton(Gtk.Button):
    """
    Button with three crossfading faces:
        idle  – icon + label
        busy  – spinner + busy label (while work is running)
        done  – check mark + done label (for a moment after success)

    All faces share one size, so the button never jumps when the face changes.
    """

    def __init__(self, label: str, icon: str, *, style: str = "sm-btn",
                 primary: bool = False, busy_label: str | None = None,
                 done_label: str | None = None, tooltip: str | None = None):
        super().__init__()
        self.add_css_class(style)
        if primary:
            self.add_css_class("primary")
        if tooltip:
            self.set_tooltip_text(tooltip)

        self._timer = 0
        self._spinner = Gtk.Spinner()
        self._stack = Gtk.Stack()
        self._stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self._stack.set_transition_duration(200)
        self._stack.set_hhomogeneous(True)
        self._stack.set_vhomogeneous(True)
        self._stack.add_named(_row(make_icon(icon), label), "idle")
        self._stack.add_named(_row(make_icon("check"), done_label or label), "done")
        if busy_label:
            self._stack.add_named(_row(self._spinner, busy_label), "busy")
        self._has_busy = bool(busy_label)
        self.set_child(self._stack)

    # -- states ------------------------------------------------------------
    def _cancel_timer(self):
        if self._timer:
            GLib.source_remove(self._timer)
            self._timer = 0

    def reset(self):
        self._cancel_timer()
        self._spinner.stop()
        self.remove_css_class("done")
        self._stack.set_visible_child_name("idle")

    def busy(self):
        self._cancel_timer()
        self.remove_css_class("done")
        if self._has_busy:
            self._spinner.start()
            self._stack.set_visible_child_name("busy")

    def done(self, ms: int = 1500):
        self._cancel_timer()
        self._spinner.stop()
        self.add_css_class("done")
        self._stack.set_visible_child_name("done")
        self._timer = GLib.timeout_add(ms, self._on_done_timeout)

    def _on_done_timeout(self):
        self._timer = 0
        self.reset()
        return False


# ---------------------------------------------------------------------------
# Toasts
# ---------------------------------------------------------------------------

class ToastKind(str, Enum):
    INFO = "info"
    SUCCESS = "success"
    WARNING = "warning"
    ERROR = "error"


_DURATION_MS = {
    ToastKind.INFO: 4000,
    ToastKind.SUCCESS: 3000,
    ToastKind.WARNING: 5500,
    ToastKind.ERROR: 6500,
}


class _Toast:
    def __init__(self, host: "ToastHost", kind: ToastKind, message: str):
        self.host = host
        self.kind = kind
        self.message = message
        self.key = (kind, message)
        self._timer = 0
        self._bump_timer = 0
        self._gone = False

        self.revealer = Gtk.Revealer()
        self.revealer.set_transition_type(Gtk.RevealerTransitionType.SLIDE_UP)
        self.revealer.set_transition_duration(260)
        self.revealer.set_halign(Gtk.Align.FILL)   # stacked toasts share one width

        wrap = Gtk.Box()
        wrap.set_hexpand(True)
        wrap.set_margin_top(4); wrap.set_margin_bottom(10)
        wrap.set_margin_start(14); wrap.set_margin_end(14)

        try:
            box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10,
                          accessible_role=Gtk.AccessibleRole.ALERT)
        except Exception:
            box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        box.add_css_class("toast")
        box.set_hexpand(True)
        box.add_css_class(f"kind-{kind.value}")
        self.box = box

        icon = make_icon(kind.value, 20)
        icon.add_css_class("toast-icon")
        icon.set_valign(Gtk.Align.START)
        icon.set_margin_top(1)
        box.append(icon)

        label = Gtk.Label(label=message)
        label.add_css_class("toast-text")
        label.set_wrap(True)
        label.set_xalign(0)
        label.set_max_width_chars(46)
        label.set_hexpand(True)
        label.set_valign(Gtk.Align.CENTER)
        box.append(label)

        close = Gtk.Button()
        close.add_css_class("toast-close")
        close.set_child(make_icon("close", 12))
        close.set_valign(Gtk.Align.START)
        close.set_tooltip_text("Dismiss")
        close.connect("clicked", lambda *_: self.dismiss())
        box.append(close)

        # Pause the countdown while the pointer is over the toast.
        motion = Gtk.EventControllerMotion()
        motion.connect("enter", lambda *a: self._cancel_timer())
        motion.connect("leave", lambda *a: self._start_timer(1800))
        box.add_controller(motion)

        wrap.append(box)
        self.revealer.set_child(wrap)
        self.revealer.connect("notify::child-revealed", self._on_revealed)

    # -- life cycle ----------------------------------------------------------
    def present(self):
        self._start_timer()
        GLib.idle_add(self._reveal)

    def _reveal(self):
        if not self._gone:
            self.revealer.set_reveal_child(True)
        return False

    def _start_timer(self, ms: int | None = None):
        self._cancel_timer()
        self._timer = GLib.timeout_add(ms or _DURATION_MS[self.kind], self._expire)

    def _cancel_timer(self):
        if self._timer:
            GLib.source_remove(self._timer)
            self._timer = 0

    def _expire(self):
        self._timer = 0
        self.dismiss()
        return False

    def bump(self):
        """The same message arrived again: keep a single toast, refresh it."""
        self._start_timer()
        self.box.add_css_class("bump")
        if self._bump_timer:
            GLib.source_remove(self._bump_timer)
        self._bump_timer = GLib.timeout_add(170, self._unbump)

    def _unbump(self):
        self._bump_timer = 0
        self.box.remove_css_class("bump")
        return False

    def dismiss(self, immediate: bool = False):
        if self._gone:
            return
        self._cancel_timer()
        if immediate:
            self._remove()
            return
        self.revealer.set_reveal_child(False)
        GLib.timeout_add(500, self._remove_cb)  # safety net

    def _remove_cb(self):
        self._remove()
        return False

    def _on_revealed(self, revealer, _pspec):
        if not revealer.get_child_revealed() and not revealer.get_reveal_child():
            self._remove()

    def _remove(self):
        if self._gone:
            return
        self._gone = True
        self._cancel_timer()
        if self._bump_timer:
            GLib.source_remove(self._bump_timer)
            self._bump_timer = 0
        self.host._forget(self)


class ToastHost(Gtk.Box):
    """
    Overlay widget that stacks toasts at the bottom centre of the window.

    * Four kinds: info, success, warning, error (colour + distinct icon shape).
    * A toast with the same kind and text as one already on screen is never
      added twice – the existing one is refreshed instead.
    """

    MAX_VISIBLE = 3

    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        # Bottom-right on wide windows (keeps the action buttons and the Copy
        # button uncovered); main.py centres it on narrow windows.
        self.set_halign(Gtk.Align.END)
        self.set_valign(Gtk.Align.END)
        self.set_margin_bottom(6)
        self.set_margin_start(6)
        self.set_margin_end(6)
        self._toasts: list[_Toast] = []

    def show_toast(self, kind: ToastKind, message: str):
        key = (kind, message)
        for t in self._toasts:
            if t.key == key and not t._gone:
                t.bump()
                return
        toast = _Toast(self, kind, message)
        self._toasts.append(toast)
        self.append(toast.revealer)
        while len([t for t in self._toasts if not t._gone]) > self.MAX_VISIBLE:
            oldest = next(t for t in self._toasts if not t._gone)
            oldest.dismiss(immediate=True)
        toast.present()

    def _forget(self, toast: _Toast):
        if toast in self._toasts:
            self._toasts.remove(toast)
        if toast.revealer.get_parent() is self:
            self.remove(toast.revealer)


# ---------------------------------------------------------------------------
# Developer avatar (GitHub)
# ---------------------------------------------------------------------------

AVATAR_URL = "https://avatars.githubusercontent.com/u/158446870?s=160&v=4"
_AVATAR_MAX_AGE = 7 * 24 * 3600


def load_github_avatar(avatar: Adw.Avatar) -> None:
    """
    Show the developer's GitHub picture in `avatar`.

    The picture is downloaded once (in the background, only when the help
    popover is first opened) and cached; offline, the initials remain.
    """
    cache = Path(GLib.get_user_cache_dir()) / "securemessage" / "avatar.img"

    def apply(data: bytes):
        try:
            avatar.set_custom_image(Gdk.Texture.new_from_bytes(GLib.Bytes.new(data)))
        except GLib.Error:
            pass
        return False

    fresh = False
    try:
        apply(cache.read_bytes())
        fresh = time.time() - cache.stat().st_mtime < _AVATAR_MAX_AGE
    except OSError:
        pass
    if fresh:
        return

    def fetch():
        try:
            req = urllib.request.Request(AVATAR_URL, headers={"User-Agent": "SecureMessage"})
            with urllib.request.urlopen(req, timeout=8) as resp:
                data = resp.read(2_000_000)
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_bytes(data)
            GLib.idle_add(apply, data)
        except Exception:
            pass  # offline: keep the initials

    threading.Thread(target=fetch, daemon=True).start()


# ---------------------------------------------------------------------------
# Shared building blocks
# ---------------------------------------------------------------------------

def spacer() -> Gtk.Widget:
    s = Gtk.Box()
    s.set_hexpand(True)
    return s


def make_pane(title: str):
    """A macOS-style card with a header row. Returns (card, header_box)."""
    pane = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
    pane.add_css_class("pane")
    header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
    header.add_css_class("pane-header")
    lbl = Gtk.Label(label=title, xalign=0)
    lbl.add_css_class("pane-title")
    header.append(lbl)
    pane.append(header)
    return pane, header


def padded(child: Gtk.Widget, pad: int = 14) -> Gtk.Box:
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
    for side in ("top", "bottom", "start", "end"):
        getattr(box, f"set_margin_{side}")(pad)
    box.append(child)
    return box


def traffic_lights(window: Gtk.Window, only_close: bool = False) -> Gtk.Widget:
    """macOS-style window buttons: fixed round dots that don't depend on libadwaita's version."""
    box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
    box.add_css_class("traffic")
    box.set_valign(Gtk.Align.CENTER)
    spec = [("close", "close", "Close", lambda *_: window.close())]
    if not only_close:
        spec += [
            ("minimize", "minus", "Minimize", lambda *_: window.minimize()),
            ("maximize", "plus", "Maximize",
             lambda *_: window.unmaximize() if window.is_maximized() else window.maximize()),
        ]
    for kind, icon, tip, cb in spec:
        b = Gtk.Button()
        b.add_css_class("tl"); b.add_css_class(kind)
        b.set_halign(Gtk.Align.CENTER); b.set_valign(Gtk.Align.CENTER)
        b.set_focus_on_click(False)
        b.set_child(make_icon(icon, 8))
        b.set_tooltip_text(tip)
        b.update_property([Gtk.AccessibleProperty.LABEL], [tip])
        b.connect("clicked", cb)
        box.append(b)
    return box


def confirm(parent: Gtk.Window, heading: str, body: str, ok_label: str, on_ok, destructive=True):
    cls = getattr(Adw, "AlertDialog", None)
    if cls is not None:
        dlg = cls(heading=heading, body=body)
        dlg.add_response("cancel", "Cancel")
        dlg.add_response("ok", ok_label)
        dlg.set_close_response("cancel")
        if destructive:
            dlg.set_response_appearance("ok", Adw.ResponseAppearance.DESTRUCTIVE)
        dlg.connect("response", lambda _d, r: on_ok() if r == "ok" else None)
        dlg.present(parent)
    else:
        dlg = Adw.MessageDialog(transient_for=parent, modal=True, heading=heading, body=body)
        dlg.add_response("cancel", "Cancel")
        dlg.add_response("ok", ok_label)
        dlg.set_close_response("cancel")
        if destructive:
            dlg.set_response_appearance("ok", Adw.ResponseAppearance.DESTRUCTIVE)
        dlg.connect("response", lambda _d, r: on_ok() if r == "ok" else None)
        dlg.present()


def fmt_time(seconds: float) -> str:
    seconds = max(0.0, seconds)
    return f"{int(seconds // 60)}:{int(seconds % 60):02d}"


# ---------------------------------------------------------------------------
# Live waveform
# ---------------------------------------------------------------------------

class WaveView(Gtk.DrawingArea):
    """Scrolling level meter: new bars appear on the right while recording."""

    def __init__(self):
        super().__init__()
        from collections import deque
        self.levels = deque([0.0] * 200, maxlen=200)
        self.set_content_height(104)
        self.set_hexpand(True)
        self.add_css_class("wave")
        self.set_draw_func(self._draw)
        self._source = None
        self._level_fn = None
        self._rng = __import__("random").Random()

    def run(self, level_fn):
        """Animate from level_fn() (0..1) until stop() and the bars have fallen silent."""
        self._level_fn = level_fn
        self.add_css_class("live")
        if not self._source:
            self._source = GLib.timeout_add(33, self._tick)

    def stop(self):
        self._level_fn = None
        self.remove_css_class("live")

    def clear(self):
        self.levels.extend([0.0] * self.levels.maxlen)
        self.queue_draw()

    def _tick(self):
        if self._level_fn:
            v = max(0.0, min(1.0, self._level_fn() * (0.9 + 0.2 * self._rng.random())))
        else:
            v = 0.0
        self.levels.append(v)
        self.queue_draw()
        if self._level_fn is None and not any(self.levels):
            self._source = None
            return False
        return True

    def _draw(self, _area, cr, w, h):
        import math
        rgba = self.get_color()
        bar, gap = 4.0, 3.0
        n = max(8, int(w // (bar + gap)))
        levels = list(self.levels)[-n:]
        x = w - len(levels) * (bar + gap) + gap / 2
        mid = h / 2
        for i, v in enumerate(levels):
            bh = max(bar, v * (h - 10))
            alpha = 0.18 + 0.82 * (i + 1) / len(levels)
            if v == 0.0:
                alpha *= 0.55
            cr.set_source_rgba(rgba.red, rgba.green, rgba.blue, alpha)
            r = bar / 2
            y = mid - bh / 2
            cr.new_sub_path()
            cr.arc(x + r, y + r, r, math.pi, 1.5 * math.pi)
            cr.arc(x + r, y + bh - r, r, 0, 0.5 * math.pi)
            cr.arc(x + r, y + bh - r, r, 0.5 * math.pi, math.pi)
            cr.arc(x + r, y + r, r, math.pi, 1.5 * math.pi)
            cr.close_path()
            cr.fill()
            x += bar + gap
