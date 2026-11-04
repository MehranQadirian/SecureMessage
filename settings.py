"""Tiny JSON settings store (~/.config/securemessage/settings.json)."""

from __future__ import annotations

import json
from pathlib import Path

from gi.repository import GLib

_DEFAULTS = {"theme": "system", "algorithm": 0, "method": "password", "key": ""}


class Settings:
    def __init__(self):
        self._path = Path(GLib.get_user_config_dir()) / "securemessage" / "settings.json"
        self._data = dict(_DEFAULTS)
        try:
            loaded = json.loads(self._path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                self._data.update({k: loaded[k] for k in _DEFAULTS if k in loaded})
        except (OSError, ValueError):
            pass

    def get(self, key: str):
        return self._data.get(key, _DEFAULTS.get(key))

    def set(self, key: str, value) -> None:
        if self._data.get(key) == value:
            return
        self._data[key] = value
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(json.dumps(self._data), encoding="utf-8")
        except OSError:
            pass  # settings are a convenience; never block the user
