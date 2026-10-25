"""Optional at-rest encryption for credentials/sessions in the sync repo (off by default).

AES-256-GCM with a random 32-byte key kept in ``<data>/vault/sync_key.json`` on each device.
The user copies the key to their other devices (Settings > Sync > Export key). Losing the key
means the encrypted files cannot be read; the local vault copies still work.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from . import store

SUFFIX = ".enc"


class KeyMissing(Exception):
    pass


def _kid(key: bytes) -> str:
    return hashlib.sha256(key).hexdigest()[:12]


def load_key() -> bytes | None:
    try:
        raw = json.loads(store.key_file().read_text(encoding="utf-8"))
        key = base64.b64decode(raw["key"])
        return key if len(key) == 32 else None
    except (OSError, ValueError, KeyError):
        return None


def ensure_key() -> bytes:
    key = load_key()
    if key is None:
        key = AESGCM.generate_key(bit_length=256)
        store.write_private(store.key_file(), {"key": base64.b64encode(key).decode(), "kid": _kid(key)})
    return key


def export_key() -> str:
    key = load_key()
    if key is None:
        raise KeyMissing("No sync key on this device yet.")
    return "aqk1." + base64.urlsafe_b64encode(key).decode().rstrip("=")


def import_key(text: str) -> str:
    text = (text or "").strip()
    if not text.startswith("aqk1."):
        raise ValueError("Not an ApplyQueue sync key.")
    b = text[5:]
    key = base64.urlsafe_b64decode(b + "=" * (-len(b) % 4))
    if len(key) != 32:
        raise ValueError("Not an ApplyQueue sync key.")
    store.write_private(store.key_file(), {"key": base64.b64encode(key).decode(), "kid": _kid(key)})
    return _kid(key)


def encrypt(plaintext: str) -> str:
    key = ensure_key()
    nonce = os.urandom(12)
    ct = AESGCM(key).encrypt(nonce, plaintext.encode("utf-8"), b"applyqueue-sync-v1")
    return json.dumps({"v": 1, "alg": "A256GCM", "kid": _kid(key),
                       "nonce": base64.b64encode(nonce).decode(), "ct": base64.b64encode(ct).decode()}) + "\n"


def decrypt(blob: str) -> str:
    key = load_key()
    data = json.loads(blob)
    if key is None or data.get("kid") != _kid(key):
        raise KeyMissing("This device does not have the sync key these files were encrypted with.")
    pt = AESGCM(key).decrypt(base64.b64decode(data["nonce"]), base64.b64decode(data["ct"]), b"applyqueue-sync-v1")
    return pt.decode("utf-8")
