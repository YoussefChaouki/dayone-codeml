"""Local encryption: every sensitive payload on the phone is AES-256-GCM encrypted.

The key is derived from the midwife's PIN with scrypt (salt stored next to the database),
so a stolen phone or copied database file reveals neither images nor field values.
Each payload has its own random nonce; the record id is bound as associated data so a
ciphertext cannot be swapped between records.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

NONCE_BYTES = 12
SALT_BYTES = 16
MAGIC = b"DO1"  # format version prefix


class WrongPin(Exception):
    """The PIN does not decrypt the store."""


def derive_key(pin: str, salt: bytes) -> bytes:
    # n=2**14 keeps unlock under ~100 ms on a low-end phone while making brute force costly.
    return Scrypt(salt=salt, length=32, n=2**14, r=8, p=1).derive(pin.encode())


@dataclass
class Cipher:
    key: bytes

    @classmethod
    def from_pin(cls, pin: str, salt: bytes) -> Cipher:
        return cls(derive_key(pin, salt))

    def encrypt(self, plaintext: bytes, aad: bytes = b"") -> bytes:
        nonce = os.urandom(NONCE_BYTES)
        return MAGIC + nonce + AESGCM(self.key).encrypt(nonce, plaintext, aad)

    def decrypt(self, blob: bytes, aad: bytes = b"") -> bytes:
        if not blob.startswith(MAGIC):
            raise ValueError("not a DayOne ciphertext")
        nonce, ct = blob[len(MAGIC):len(MAGIC) + NONCE_BYTES], blob[len(MAGIC) + NONCE_BYTES:]
        try:
            return AESGCM(self.key).decrypt(nonce, ct, aad)
        except InvalidTag as e:
            raise WrongPin("cannot decrypt: wrong PIN or tampered data") from e


def new_salt() -> bytes:
    return os.urandom(SALT_BYTES)
