#!/usr/bin/env python3
"""
Secure Message Encryption Engine
Supports multiple authenticated encryption schemes with strong password-based key derivation.
Uses Argon2id for KDF and AES-256-GCM / ChaCha20-Poly1305 for encryption.
"""

from __future__ import annotations

import base64
import json
import os
import secrets
from dataclasses import dataclass
from enum import Enum
from typing import Optional, Tuple

from argon2.low_level import Type, hash_secret_raw
from cryptography.hazmat.primitives.ciphers.aead import AESGCM, ChaCha20Poly1305


class Algorithm(Enum):
    AES_256_GCM = "AES-256-GCM"
    CHACHA20_POLY1305 = "ChaCha20-Poly1305"


# Argon2id parameters tuned for interactive use (secure but not too slow)
ARGON2_TIME_COST = 3
ARGON2_MEMORY_COST = 64 * 1024  # 64 MiB
ARGON2_PARALLELISM = 2
ARGON2_HASH_LEN = 32
SALT_LEN = 16
NONCE_LEN = 12  # for both AES-GCM and ChaCha20


@dataclass
class EncryptedPayload:
    algorithm: str
    salt: bytes
    nonce: bytes
    ciphertext: bytes
    version: int = 1

    def to_b64(self) -> str:
        data = {
            "v": self.version,
            "alg": self.algorithm,
            "salt": base64.b64encode(self.salt).decode("ascii"),
            "nonce": base64.b64encode(self.nonce).decode("ascii"),
            "ct": base64.b64encode(self.ciphertext).decode("ascii"),
        }
        raw = json.dumps(data, separators=(",", ":")).encode("utf-8")
        return base64.urlsafe_b64encode(raw).decode("ascii")

    @classmethod
    def from_b64(cls, token: str) -> "EncryptedPayload":
        try:
            raw = base64.urlsafe_b64decode(token.encode("ascii"))
            data = json.loads(raw.decode("utf-8"))
            return cls(
                version=data.get("v", 1),
                algorithm=data["alg"],
                salt=base64.b64decode(data["salt"]),
                nonce=base64.b64decode(data["nonce"]),
                ciphertext=base64.b64decode(data["ct"]),
            )
        except Exception as exc:
            raise ValueError("Invalid or corrupted encrypted message format") from exc


def _derive_key(password: str, salt: bytes) -> bytes:
    """Derive a 32-byte key using Argon2id."""
    if not password:
        raise ValueError("Password cannot be empty")
    return hash_secret_raw(
        secret=password.encode("utf-8"),
        salt=salt,
        time_cost=ARGON2_TIME_COST,
        memory_cost=ARGON2_MEMORY_COST,
        parallelism=ARGON2_PARALLELISM,
        hash_len=ARGON2_HASH_LEN,
        type=Type.ID,
    )


def encrypt(plaintext: str, password: str, algorithm: Algorithm = Algorithm.AES_256_GCM) -> str:
    """
    Encrypt plaintext with the given password and algorithm.
    Returns a compact base64url-encoded payload that contains all necessary metadata.
    """
    if not plaintext:
        raise ValueError("Message cannot be empty")
    salt = secrets.token_bytes(SALT_LEN)
    key = _derive_key(password, salt)
    nonce = secrets.token_bytes(NONCE_LEN)
    data = plaintext.encode("utf-8")

    if algorithm == Algorithm.AES_256_GCM:
        aead = AESGCM(key)
        ciphertext = aead.encrypt(nonce, data, None)
    elif algorithm == Algorithm.CHACHA20_POLY1305:
        aead = ChaCha20Poly1305(key)
        ciphertext = aead.encrypt(nonce, data, None)
    else:
        raise ValueError(f"Unsupported algorithm: {algorithm}")

    payload = EncryptedPayload(
        algorithm=algorithm.value,
        salt=salt,
        nonce=nonce,
        ciphertext=ciphertext,
    )
    return payload.to_b64()


def decrypt(token: str, password: str) -> str:
    """
    Decrypt a previously encrypted token.
    Raises ValueError on wrong password, tampering, or invalid format.
    """
    payload = EncryptedPayload.from_b64(token)
    key = _derive_key(password, payload.salt)

    try:
        if payload.algorithm == Algorithm.AES_256_GCM.value:
            aead = AESGCM(key)
            plaintext = aead.decrypt(payload.nonce, payload.ciphertext, None)
        elif payload.algorithm == Algorithm.CHACHA20_POLY1305.value:
            aead = ChaCha20Poly1305(key)
            plaintext = aead.decrypt(payload.nonce, payload.ciphertext, None)
        else:
            raise ValueError(f"Unsupported algorithm in payload: {payload.algorithm}")
    except Exception as exc:
        # Generic error - do not leak whether password was wrong or data was tampered
        raise ValueError("Decryption failed. Wrong password or corrupted message.") from exc

    return plaintext.decode("utf-8")


def list_algorithms() -> list[str]:
    return [a.value for a in Algorithm]


if __name__ == "__main__":
    # Quick self-test
    msg = "Hello colleague, this is a confidential message."
    pwd = "MyS3cretP@ssw0rd!"
    for alg in Algorithm:
        token = encrypt(msg, pwd, alg)
        print(f"{alg.value}: {token[:60]}...")
        recovered = decrypt(token, pwd)
        assert recovered == msg
        try:
            decrypt(token, "wrong")
            assert False, "Should have failed"
        except ValueError:
            pass
    print("All self-tests passed.")


# ===========================================================================
# v2: binary sealed format, password mode + hybrid post-quantum mode
# ===========================================================================
#
# Password mode : Argon2id -> AES-256-GCM / ChaCha20-Poly1305.
#                 Symmetric 256-bit ciphers keep >=128-bit security against
#                 quantum attackers (Grover); the strength is the password's.
# Post-quantum  : hybrid key encapsulation to the recipient's public key.
#                 ML-KEM-768 (NIST FIPS 203) + X25519, combined with HKDF-SHA256,
#                 then the same AEADs.  An attacker must break BOTH ML-KEM and
#                 X25519, so it stays safe if either one is ever weakened.

import hashlib  # noqa: E402

from cryptography.hazmat.primitives import hashes  # noqa: E402
from cryptography.hazmat.primitives.asymmetric.x25519 import (  # noqa: E402
    X25519PrivateKey, X25519PublicKey)
from cryptography.hazmat.primitives.kdf.hkdf import HKDF  # noqa: E402

try:  # PQClean C implementation (preferred)
    from pqcrypto.kem import ml_kem_768 as _pq

    def _kem_keygen():
        return _pq.keygen()

    def _kem_encaps(public: bytes):
        ct, ss = _pq.encaps(public)
        return ct, ss

    def _kem_decaps(secret: bytes, ct: bytes) -> bytes:
        return _pq.decaps(secret, ct)

    KEM_BACKEND = "PQClean"
except ImportError:  # pure-Python fallback
    try:
        from kyber_py.ml_kem import ML_KEM_768 as _pq

        def _kem_keygen():
            return _pq.keygen()

        def _kem_encaps(public: bytes):
            ss, ct = _pq.encaps(public)
            return ct, ss

        def _kem_decaps(secret: bytes, ct: bytes) -> bytes:
            return _pq.decaps(secret, ct)

        KEM_BACKEND = "kyber-py"
    except ImportError:
        _pq = None
        KEM_BACKEND = None

MAGIC = b"SMX2"
TOKEN_PREFIX = "SM2:"
MODE_PASSWORD, MODE_PQ = 1, 2
_ALG_ID = {Algorithm.AES_256_GCM: 1, Algorithm.CHACHA20_POLY1305: 2}
_ID_ALG = {v: k for k, v in _ALG_ID.items()}

KEM_PK_LEN, KEM_SK_LEN, KEM_CT_LEN = 1184, 2400, 1088
X_LEN = 32
PUBLIC_KEY_LEN = KEM_PK_LEN + X_LEN          # 1216
SECRET_KEY_LEN = KEM_SK_LEN + X_LEN          # 2432
KEY_ID_LEN = 8
PUBLIC_PREFIX = "smpk1:"


def pq_available() -> bool:
    return _pq is not None


def key_id(public: bytes) -> bytes:
    return hashlib.sha256(public).digest()[:KEY_ID_LEN]


def key_id_hex(public_or_id: bytes) -> str:
    kid = public_or_id if len(public_or_id) == KEY_ID_LEN else key_id(public_or_id)
    return kid.hex()


def generate_identity() -> tuple[bytes, bytes]:
    """Return (public_key, secret_key) for a new hybrid ML-KEM-768 + X25519 identity."""
    if _pq is None:
        raise ValueError("Post-quantum library is missing (install pqcrypto).")
    kem_pk, kem_sk = _kem_keygen()
    x_priv = X25519PrivateKey.generate()
    x_pub = x_priv.public_key().public_bytes_raw()
    x_sec = x_priv.private_bytes_raw()
    return bytes(kem_pk) + x_pub, bytes(kem_sk) + x_sec


def public_to_text(public: bytes) -> str:
    return PUBLIC_PREFIX + base64.urlsafe_b64encode(public).decode("ascii").rstrip("=")


def public_from_text(text: str) -> bytes:
    t = "".join(text.split())
    if not t.startswith(PUBLIC_PREFIX):
        raise ValueError("That isn't a Secure Message public key.")
    body = t[len(PUBLIC_PREFIX):]
    try:
        raw = base64.urlsafe_b64decode(body + "=" * (-len(body) % 4))
    except Exception as exc:
        raise ValueError("The public key is damaged.") from exc
    if len(raw) != PUBLIC_KEY_LEN:
        raise ValueError("The public key has the wrong length.")
    return raw


def _hybrid_key(ss_kem: bytes, ss_x: bytes, kem_ct: bytes, eph_pub: bytes, recip_pub: bytes) -> bytes:
    return HKDF(
        algorithm=hashes.SHA256(), length=32, salt=None,
        info=b"SecureMessage/hybrid-mlkem768-x25519/v2" + kem_ct + eph_pub + recip_pub,
    ).derive(ss_kem + ss_x)


def _aead_for(algorithm: Algorithm, key: bytes):
    return AESGCM(key) if algorithm == Algorithm.AES_256_GCM else ChaCha20Poly1305(key)


@dataclass
class BlobInfo:
    mode: int
    algorithm: Algorithm
    key_id: Optional[bytes]
    header_len: int

    @property
    def is_pq(self) -> bool:
        return self.mode == MODE_PQ


def inspect_blob(blob: bytes) -> BlobInfo:
    if len(blob) < 6 or blob[:4] != MAGIC:
        raise ValueError("Not an encrypted Secure Message.")
    mode, alg_id = blob[4], blob[5]
    if alg_id not in _ID_ALG or mode not in (MODE_PASSWORD, MODE_PQ):
        raise ValueError("Unsupported encryption format.")
    if mode == MODE_PASSWORD:
        hlen = 6 + SALT_LEN + NONCE_LEN
        kid = None
    else:
        hlen = 6 + KEY_ID_LEN + KEM_CT_LEN + X_LEN + NONCE_LEN
        kid = blob[6:6 + KEY_ID_LEN]
    if len(blob) < hlen + 16:
        raise ValueError("The encrypted data is incomplete.")
    return BlobInfo(mode, _ID_ALG[alg_id], kid, hlen)


def seal(data: bytes, *, algorithm: Algorithm = Algorithm.AES_256_GCM,
         password: Optional[str] = None, recipient: Optional[bytes] = None) -> bytes:
    """Encrypt bytes with a password OR to a recipient's public key."""
    nonce = secrets.token_bytes(NONCE_LEN)
    if recipient is not None:
        if len(recipient) != PUBLIC_KEY_LEN:
            raise ValueError("The recipient's public key is invalid.")
        kem_pk, x_pk = recipient[:KEM_PK_LEN], recipient[KEM_PK_LEN:]
        kem_ct, ss_kem = _kem_encaps(kem_pk)
        eph = X25519PrivateKey.generate()
        eph_pub = eph.public_key().public_bytes_raw()
        ss_x = eph.exchange(X25519PublicKey.from_public_bytes(x_pk))
        key = _hybrid_key(bytes(ss_kem), ss_x, bytes(kem_ct), eph_pub, recipient)
        header = (MAGIC + bytes([MODE_PQ, _ALG_ID[algorithm]]) + key_id(recipient)
                  + bytes(kem_ct) + eph_pub)
    elif password:
        salt = secrets.token_bytes(SALT_LEN)
        key = _derive_key(password, salt)
        header = MAGIC + bytes([MODE_PASSWORD, _ALG_ID[algorithm]]) + salt
    else:
        raise ValueError("A password or a recipient key is required.")
    aad = header + nonce
    return aad + _aead_for(algorithm, key).encrypt(nonce, data, aad)


def open_sealed(blob: bytes, *, password: Optional[str] = None,
                secret: Optional[bytes] = None) -> bytes:
    info = inspect_blob(blob)
    aad = blob[:info.header_len]
    nonce = aad[-NONCE_LEN:]
    body = blob[info.header_len:]
    try:
        if info.mode == MODE_PASSWORD:
            if not password:
                raise ValueError("A password is required.")
            key = _derive_key(password, aad[6:6 + SALT_LEN])
        else:
            if secret is None or len(secret) != SECRET_KEY_LEN:
                raise ValueError("A matching secret key is required.")
            off = 6 + KEY_ID_LEN
            kem_ct = aad[off:off + KEM_CT_LEN]
            eph_pub = aad[off + KEM_CT_LEN:off + KEM_CT_LEN + X_LEN]
            kem_sk, x_sk = secret[:KEM_SK_LEN], secret[KEM_SK_LEN:]
            x_priv = X25519PrivateKey.from_private_bytes(x_sk)
            recip_pub = _public_from_secret(secret)
            ss_kem = _kem_decaps(kem_sk, kem_ct)
            ss_x = x_priv.exchange(X25519PublicKey.from_public_bytes(eph_pub))
            key = _hybrid_key(bytes(ss_kem), ss_x, kem_ct, eph_pub, recip_pub)
        return _aead_for(info.algorithm, key).decrypt(nonce, body, aad)
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError("Decryption failed. Wrong password or key, or the data was changed.") from exc


def _public_from_secret(secret: bytes) -> bytes:
    """The ML-KEM-768 secret key embeds its public key (FIPS 203 dk layout)."""
    kem_sk = secret[:KEM_SK_LEN]
    kem_pk = kem_sk[1152:1152 + KEM_PK_LEN]          # dk = dkPKE(1152) || ek(1184) || H(ek) || z
    x_pub = X25519PrivateKey.from_private_bytes(secret[KEM_SK_LEN:]).public_key().public_bytes_raw()
    return kem_pk + x_pub


# ---- text tokens -----------------------------------------------------------

@dataclass
class TokenInfo:
    is_pq: bool
    algorithm: str
    key_id: Optional[bytes]


def _b64u(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _unb64u(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def inspect_token(token: str) -> TokenInfo:
    t = "".join(token.split())
    if t.startswith(TOKEN_PREFIX):
        try:
            info = inspect_blob(_unb64u(t[len(TOKEN_PREFIX):]))
        except ValueError:
            raise
        except Exception as exc:
            raise ValueError("Invalid or corrupted encrypted message format") from exc
        return TokenInfo(info.is_pq, info.algorithm.value, info.key_id)
    payload = EncryptedPayload.from_b64(t)            # legacy v1 (password only)
    return TokenInfo(False, payload.algorithm, None)


def encrypt_text(text: str, *, algorithm: Algorithm = Algorithm.AES_256_GCM,
                 password: Optional[str] = None, recipient: Optional[bytes] = None) -> str:
    if not text:
        raise ValueError("Message cannot be empty")
    blob = seal(text.encode("utf-8"), algorithm=algorithm, password=password, recipient=recipient)
    return TOKEN_PREFIX + _b64u(blob)


def decrypt_text(token: str, *, password: Optional[str] = None,
                 secret: Optional[bytes] = None) -> str:
    t = "".join(token.split())
    if not t.startswith(TOKEN_PREFIX):
        return decrypt(t, password or "")            # legacy v1
    try:
        blob = _unb64u(t[len(TOKEN_PREFIX):])
    except Exception as exc:
        raise ValueError("Invalid or corrupted encrypted message format") from exc
    return open_sealed(blob, password=password, secret=secret).decode("utf-8")
