"""The update dialog: checking -> available -> downloading -> restart."""

from __future__ import annotations

import threading
import time

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk  # noqa: E402

import updater


class UpdateWindow(Adw.Window):
    """
    silent=True  : started automatically; stays hidden unless a new version exists.
    silent=False : started by the person; always shows the outcome.
    """

    def __init__(self, parent: Gtk.Window, current_version: str, silent: bool = False):
        super().__init__(transient_for=parent, modal=True, title="Software update")
        self.set_default_size(440, -1)
        self.set_resizable(False)
        self._parent, self._current, self._silent = parent, current_version, silent
        self._update: updater.Update | None = None
        self._cancel = False
        self._busy = False
        self._build()
        self.connect("close-request", self._on_close)
        if not silent:
            self.present()
        self._start_check()

    # ------------------------------------------------------------------ UI
    def _build(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        for side in ("top", "bottom", "start", "end"):
            getattr(box, f"set_margin_{side}")(22)
        self.title_lbl = Gtk.Label(xalign=0, wrap=True)
        self.title_lbl.add_css_class("title-3")
        self.body_lbl = Gtk.Label(xalign=0, wrap=True, selectable=False)
        self.body_lbl.add_css_class("sm-dim")
        self.spinner = Gtk.Spinner(spinning=True)
        self.spinner.set_halign(Gtk.Align.START)
        self.bar = Gtk.ProgressBar(show_text=True, visible=False)

        self.notes = Gtk.Label(xalign=0, yalign=0, wrap=True, selectable=True)
        self.notes.add_css_class("sm-small")
        self.notes_scroll = Gtk.ScrolledWindow(min_content_height=120, max_content_height=220,
                                               propagate_natural_height=True, visible=False)
        self.notes_scroll.set_child(self.notes)

        self.btn_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.btn_row.set_halign(Gtk.Align.END)
        self.later = Gtk.Button(label="Later")
        self.later.connect("clicked", lambda *_: self.close())
        self.action = Gtk.Button(label="Update now")
        self.action.add_css_class("suggested-action")
        self.action.connect("clicked", self._on_action)
        self.btn_row.append(self.later)
        self.btn_row.append(self.action)

        for w in (self.title_lbl, self.spinner, self.body_lbl, self.notes_scroll, self.bar, self.btn_row):
            box.append(w)
        self.set_content(box)
        self._show_checking()

    def _state(self, title, body="", spinner=False, notes=None, action=None, later="Close", bar=False):
        self.title_lbl.set_label(title)
        self.body_lbl.set_label(body)
        self.body_lbl.set_visible(bool(body))
        self.spinner.set_visible(spinner)
        self.spinner.set_spinning(spinner)
        self.notes_scroll.set_visible(bool(notes))
        self.notes.set_label(notes or "")
        self.bar.set_visible(bar)
        self.action.set_visible(action is not None)
        if action:
            self.action.set_label(action)
        self.action.set_sensitive(True)
        if later:
            self.later.set_label(later)
        self.later.set_visible(bool(later))

    def _show_checking(self):
        self._state("Looking for updates…", spinner=True, later="Cancel")

    # --------------------------------------------------------------- check
    def _start_check(self):
        def work():
            try:
                result, err = updater.check(self._current), None
            except updater.UpdateError as exc:
                result, err = None, str(exc)
            except Exception as exc:                              # never crash the app over this
                result, err = None, f"Unexpected error: {exc}"
            GLib.idle_add(self._checked, result, err)
        threading.Thread(target=work, daemon=True).start()

    def _checked(self, update, err):
        if err:
            if self._silent:
                self.destroy()
            else:
                self._state("Couldn't check for updates", err)
            return False
        if update is None:
            if self._silent:
                self.destroy()
            else:
                self._state("You're up to date", f"Secure Message {self._current} is the newest version.")
            return False
        self._update = update
        notes = update.release.notes.strip()
        if len(notes) > 1200:
            notes = notes[:1200].rsplit("\n", 1)[0] + "\n…"
        if update.can_install:
            body = f"Version {update.release.version} is available. You have {self._current}."
            self._state("A new version is ready", body, notes=notes, action="Update now", later="Later")
        else:
            body = (f"Version {update.release.version} is available, but this copy "
                    "(running from source, or an unsupported system) can't update itself.")
            self._state("A new version is available", body, notes=notes, action="Open download page", later="Close")
        if self._silent:
            self.present()
        return False

    # ------------------------------------------------------------- install
    def _on_action(self, *_):
        u = self._update
        if not u:
            return
        if not u.can_install:
            try:
                Gio.AppInfo.launch_default_for_uri(u.release.page, None)
            except GLib.Error:
                pass
            self.close()
            return
        self._busy, self._cancel = True, False
        self._state("Downloading Secure Message " + u.release.version, "Starting…", bar=True, later="Cancel")
        self.action.set_visible(False)

        def progress(frac, text):
            GLib.idle_add(self._progress, frac, text)

        def work():
            try:
                path = updater.download(u, progress, lambda: self._cancel)
                GLib.idle_add(self._installing)
                updater.install(u, path)
                GLib.idle_add(self._done)
            except updater.UpdateError as exc:
                GLib.idle_add(self._failed, str(exc))
            except Exception as exc:
                GLib.idle_add(self._failed, f"Unexpected error: {exc}")
        threading.Thread(target=work, daemon=True).start()

    def _progress(self, frac, text):
        self.bar.set_fraction(max(0.0, min(1.0, frac)))
        self.bar.set_text(text)
        self.body_lbl.set_label("")
        self.body_lbl.set_visible(False)
        return False

    def _installing(self):
        self.bar.set_visible(False)
        self._state("Installing…", "Confirm the permission prompt if one appears. "
                    "Secure Message will restart by itself.", spinner=True, later=None)
        return False

    def _done(self):
        self._busy = False
        self._state("Restarting…", spinner=True, later=None)
        app = self._parent.get_application()
        GLib.timeout_add(600, lambda: (app.quit(), False)[1])
        return False

    def _failed(self, message):
        self._busy = False
        if self._cancel:
            self.destroy()
        else:
            self._state("The update didn't finish", message, action="Try again", later="Close")
        return False

    def _on_close(self, *_):
        if self._busy:
            self._cancel = True            # stops the download; the window closes when it notices
            return True
        return False


def should_auto_check(settings) -> bool:
    return (bool(settings.get("auto_update"))
            and time.time() - float(settings.get("last_update_check") or 0) > 12 * 3600
            and updater.detect_flavour() != "source")
