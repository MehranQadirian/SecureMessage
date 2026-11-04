"""Parameters the UI collects before encrypting / decrypting (shared by text and voice)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import crypto_engine as ce

MIN_PASSWORD_LENGTH = 12   # below this, the UI warns "use 12 or more characters"


class UserError(Exception):
    """A mistake the user can fix (missing password, wrong key…). `widget` is what to highlight."""

    def __init__(self, message: str, widget=None):
        super().__init__(message)
        self.widget = widget


@dataclass
class EncParams:
    algorithm: ce.Algorithm
    password: Optional[str]
    recipient: Optional[bytes]
    label: str
    weak_password: bool = False

    @property
    def is_pq(self) -> bool:
        return self.recipient is not None


@dataclass
class DecParams:
    password: Optional[str]
    key_id: Optional[bytes]
    passphrase: Optional[str]


def decrypt_kwargs(store, dp: DecParams) -> dict:
    """Run in a worker thread: unlocking a secret key uses Argon2 and takes a moment."""
    if dp.key_id is not None:
        return {"secret": store.unlock(dp.key_id, dp.passphrase or "")}
    return {"password": dp.password}
