"""Seal the answer key so it can ride inside the public quiz comment.

Fernet (AES-128-CBC + HMAC) keyed from the ``PR2TEST_SECRET`` repo secret.
Anyone can see the blob in the comment source; only workflows holding the
secret can read it.
"""

from __future__ import annotations

import base64
import hashlib
import os

from cryptography.fernet import Fernet, InvalidToken


class MissingSecret(RuntimeError):
    pass


def _fernet(secret: str | None = None) -> Fernet:
    secret = secret if secret is not None else os.environ.get("PR2TEST_SECRET", "")
    if not secret.strip():
        raise MissingSecret(
            "PR2TEST_SECRET is not set. Create it with: "
            "gh secret set PR2TEST_SECRET --body \"$(openssl rand -hex 32)\""
        )
    key = base64.urlsafe_b64encode(hashlib.sha256(secret.encode()).digest())
    return Fernet(key)


def seal(plaintext: str, secret: str | None = None) -> str:
    return _fernet(secret).encrypt(plaintext.encode()).decode()


def unseal(blob: str, secret: str | None = None) -> str:
    try:
        return _fernet(secret).decrypt(blob.encode()).decode()
    except InvalidToken as e:
        raise ValueError("answer key blob did not decrypt; wrong PR2TEST_SECRET?") from e
