"""Local per-site credential vault (replaces LangHire's hard-coded shared password).

Upstream told the agent to create every ATS account with the literal password
from its prompt, and stored one shared password in settings.json for all sites.
ApplyQueue generates a unique strong password per site (hostname) and keeps it
here, outside the repo and outside the LLM context:

- File: ``<data dir>/vault/site_credentials.json`` (dir 0700, file 0600 on POSIX),
  written atomically (tmp + fsync + replace) BEFORE the password is typed, so a
  crash can never leave an account whose password we lost.
- The agent never sees a password: the ``fill_site_password`` browser action
  types it straight into the page (``is_sensitive=True``).
- At-rest encryption / OS keychain and the private sync repo arrive in M5.
"""
from __future__ import annotations

import json
import os
import secrets
import string
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from filelock import FileLock

SYMBOLS = "!@#$%^&*-_=+?"
_ALPHABET = string.ascii_letters + string.digits + SYMBOLS
LEGACY_KEY = "_legacy_shared"
DEFAULT_LENGTH = 20


def generate_password(length: int = DEFAULT_LENGTH) -> str:
    """Cryptographically random password with upper, lower, digit and symbol."""
    if length < 12:
        raise ValueError("password length must be >= 12")
    while True:
        pw = "".join(secrets.choice(_ALPHABET) for _ in range(length))
        if (any(c.islower() for c in pw) and any(c.isupper() for c in pw)
                and any(c.isdigit() for c in pw) and any(c in SYMBOLS for c in pw)):
            return pw


def normalize_host(url_or_host: str) -> str:
    """'https://WWW.Boards.Example.com/x' -> 'boards.example.com'."""
    s = (url_or_host or "").strip()
    host = urlparse(s).hostname if "://" in s else s.split("/")[0].split(":")[0]
    host = (host or "").lower().rstrip(".")
    if host.startswith("www."):
        host = host[4:]
    if not host:
        raise ValueError("empty host")
    return host


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class CredentialVault:
    def __init__(self, path: Path):
        self.path = Path(path)
        self._lock = FileLock(str(self.path) + ".lock")

    @classmethod
    def default(cls) -> "CredentialVault":
        try:
            from core.config import get_data_dir
        except ImportError:  # pragma: no cover
            from backend.core.config import get_data_dir
        return cls(get_data_dir() / "vault" / "site_credentials.json")

    # ── storage ──
    def _read(self) -> dict:
        if not self.path.exists():
            return {"version": 1, "sites": {}}
        data = json.loads(self.path.read_text(encoding="utf-8"))
        data.setdefault("sites", {})
        return data

    def _write(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if sys.platform != "win32":
            os.chmod(self.path.parent, 0o700)
        tmp = self.path.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, self.path)
        if sys.platform != "win32":
            os.chmod(self.path, 0o600)

    # ── API ──
    def get(self, host: str) -> dict | None:
        with self._lock:
            return self._read()["sites"].get(normalize_host(host))

    def get_or_create(self, host: str, username: str) -> tuple[dict, bool]:
        """Return (entry, created). A new password is persisted before it is returned."""
        h = normalize_host(host)
        with self._lock:
            data = self._read()
            entry = data["sites"].get(h)
            if entry:
                return entry, False
            entry = {"username": username, "password": generate_password(), "created_at": _now(),
                     "updated_at": _now(), "source": "generated"}
            data["sites"][h] = entry
            self._write(data)
            return entry, True

    def password_for_sign_in(self, host: str) -> str | None:
        """Per-site password, else the migrated legacy shared password (sign-in only)."""
        h = normalize_host(host)
        with self._lock:
            data = self._read()
            entry = data["sites"].get(h)
            if entry:
                return entry["password"]
            legacy = data.get(LEGACY_KEY) or {}
            return legacy.get("password") or None

    def import_legacy_shared(self, password: str, username: str = "") -> bool:
        """Keep LangHire's old shared password only so existing accounts can still sign in."""
        if not password:
            return False
        with self._lock:
            data = self._read()
            if data.get(LEGACY_KEY, {}).get("password") == password:
                return False
            data[LEGACY_KEY] = {"username": username, "password": password, "imported_at": _now()}
            self._write(data)
            return True

    def list_sites(self) -> list[dict]:
        """Hosts with usernames and dates - never passwords."""
        with self._lock:
            data = self._read()
        return [
            {"host": h, "username": e.get("username", ""), "created_at": e.get("created_at"),
             "source": e.get("source", "")}
            for h, e in sorted(data["sites"].items())
        ]

    def has_legacy_shared(self) -> bool:
        with self._lock:
            return bool((self._read().get(LEGACY_KEY) or {}).get("password"))


def migrate_settings_password(settings: dict, vault: CredentialVault) -> bool:
    """Move a shared password out of settings.json into the vault. Returns True if settings changed."""
    sd = settings.get("sensitive_data") or {}
    pw = sd.get("password")
    if pw is None:
        return False
    if pw:
        vault.import_legacy_shared(pw, sd.get("email", ""))
    sd.pop("password", None)
    settings["sensitive_data"] = sd
    return True
