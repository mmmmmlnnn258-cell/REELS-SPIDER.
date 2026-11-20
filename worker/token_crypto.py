"""Versioned AES-256-GCM encryption for Instagram Login access tokens.

Envelope/AAD are shared with dashboard/lib/token-crypto.ts. Legacy plaintext remains
readable; encrypted values fail closed on missing keys, tampering, or malformed data.
"""
from __future__ import annotations

import base64
import os
import secrets

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

_PREFIX = "enc:v1:"
_AAD = b"reels-spider:channels:instagram:access_token:v1"


def _key(value: str | None = None) -> bytes:
    raw = value if value is not None else os.environ.get("TOKEN_ENCRYPTION_KEY", "")
    try:
        key = base64.b64decode(raw, validate=True)
    except Exception as exc:
        raise RuntimeError("TOKEN_ENCRYPTION_KEY must be base64 for exactly 32 bytes") from exc
    if len(key) != 32:
        raise RuntimeError("TOKEN_ENCRYPTION_KEY must be base64 for exactly 32 bytes")
    return key


def is_encrypted(value: str | None) -> bool:
    return isinstance(value, str) and value.startswith(_PREFIX)


def encrypt_token(value: str | None, key: str | None = None) -> str | None:
    if value is None or value == "":
        return value
    if is_encrypted(value):
        decrypt_token(value, key)
        return value
    nonce = secrets.token_bytes(12)
    sealed = AESGCM(_key(key)).encrypt(nonce, value.encode("utf-8"), _AAD)
    return _PREFIX + base64.b64encode(nonce).decode("ascii") + ":" + base64.b64encode(sealed).decode("ascii")


def decrypt_token(value: str | None, key: str | None = None) -> str | None:
    if value is None or value == "" or not is_encrypted(value):
        return value
    parts = value.split(":")
    if len(parts) != 4 or parts[:2] != ["enc", "v1"]:
        raise RuntimeError("Unsupported encrypted token envelope")
    try:
        nonce = base64.b64decode(parts[2], validate=True)
        sealed = base64.b64decode(parts[3], validate=True)
    except Exception as exc:
        raise RuntimeError("Malformed encrypted token envelope") from exc
    if len(nonce) != 12 or len(sealed) < 16:
        raise RuntimeError("Malformed encrypted token envelope")
    try:
        return AESGCM(_key(key)).decrypt(nonce, sealed, _AAD).decode("utf-8")
    except Exception as exc:
        raise RuntimeError("Unable to decrypt Instagram access token") from exc
