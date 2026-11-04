"""
Key store for post-quantum identities and contacts.

~/.local/share/securemessage/keys.json (mode 0600)
  identities : your key pairs; the secret half is encrypted with your passphrase
               (Argon2id + AEAD) and never stored in the clear.
  contacts   : other people's public keys.
"""

from __future__ import annotations

import base64
import json
import os
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from gi.repository import GLib

import crypto_engine as ce


@dataclass
class KeyEntry:
    name: str
    public: bytes
    own: bool

    @property
    def key_id(self) -> bytes:
        return ce.key_id(self.public)

    @property
    def short_id(self) -> str:
        h = self.key_id.hex()
        return f"{h[:4]}-{h[4:8]}-{h[8:12]}-{h[12:16]}"


class KeyStore:
    def __init__(self, path: Optional[Path] = None):
        self.path = path or Path(GLib.get_user_data_dir()) / "securemessage" / "keys.json"
        self._data = {"identities": [], "contacts": []}
        self.load()

    # -- persistence -----------------------------------------------------------
    def load(self):
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                self._data["identities"] = list(data.get("identities", []))
                self._data["contacts"] = list(data.get("contacts", []))
        except (OSError, ValueError):
            pass

    def _save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=".keys-")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(self._data, fh)
            os.chmod(tmp, 0o600)
            os.replace(tmp, self.path)
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    # -- queries -----------------------------------------------------------------
    def entries(self) -> list[KeyEntry]:
        out = [KeyEntry(i["name"], _unb(i["public"]), True) for i in self._data["identities"]]
        out += [KeyEntry(c["name"], _unb(c["public"]), False) for c in self._data["contacts"]]
        return out

    def find(self, key_id: bytes) -> Optional[KeyEntry]:
        for e in self.entries():
            if e.key_id == key_id:
                return e
        return None

    # -- changes -----------------------------------------------------------------
    def generate(self, name: str, passphrase: str) -> KeyEntry:
        name = name.strip()
        if not name:
            raise ValueError("Give the key a name.")
        if len(passphrase) < 8:
            raise ValueError("Use a passphrase of at least 8 characters.")
        public, secret = ce.generate_identity()
        sealed = ce.seal(secret, password=passphrase)
        self._data["identities"].append({
            "name": name, "public": _b(public), "secret": _b(sealed),
            "created": int(time.time()),
        })
        self._save()
        return KeyEntry(name, public, True)

    def add_contact(self, name: str, public_text: str) -> KeyEntry:
        name = name.strip()
        if not name:
            raise ValueError("Give the contact a name.")
        public = ce.public_from_text(public_text)
        if self.find(ce.key_id(public)):
            raise ValueError("This key is already in your list.")
        self._data["contacts"].append({"name": name, "public": _b(public),
                                       "created": int(time.time())})
        self._save()
        return KeyEntry(name, public, False)

    def remove(self, key_id: bytes):
        for group in ("identities", "contacts"):
            self._data[group] = [e for e in self._data[group]
                                 if ce.key_id(_unb(e["public"])) != key_id]
        self._save()

    def unlock(self, key_id: bytes, passphrase: str) -> bytes:
        for i in self._data["identities"]:
            if ce.key_id(_unb(i["public"])) == key_id:
                try:
                    return ce.open_sealed(_unb(i["secret"]), password=passphrase)
                except ValueError as exc:
                    raise ValueError("Wrong key passphrase.") from exc
        raise ValueError("That key isn't one of yours, so it can't decrypt.")


def _b(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


def _unb(text: str) -> bytes:
    return base64.b64decode(text)
