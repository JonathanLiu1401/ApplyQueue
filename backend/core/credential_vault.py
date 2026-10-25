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
        """A malformed file (truncated write from another tool, hand edit, wrong encoding, wrong
        shape) is moved aside as ``site_credentials.json.corrupt-<time>`` (never deleted, never
        overwritten) and an empty vault is used, so applying keeps working; the private sync
        repo still holds the entries and the next sync restores them."""
        if not self.path.exists():
            return {"version": 1, "sites": {}}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8-sig"))
            if not isinstance(data, dict):
                raise TypeError(f"expected an object, got {type(data).__name__}")
            data.setdefault("sites", {})
            if not isinstance(data["sites"], dict):
                raise TypeError("'sites' is not an object")
            bad = [h for h, e in data["sites"].items() if not isinstance(e, dict) or not isinstance(e.get("password"), str)]
            for h in bad:  # one damaged entry must not cost every other site its password
                data["sites"].pop(h)
            if not isinstance(data.get(LEGACY_KEY, {}), dict):
                data.pop(LEGACY_KEY)
            return data
        except (ValueError, TypeError, UnicodeDecodeError) as e:
            self._quarantine(e)
            return {"version": 1, "sites": {}}

    def _quarantine(self, err: Exception) -> None:
        import logging
        import time as _t
        dest = self.path.with_name(f"{self.path.name}.corrupt-{_t.strftime('%Y%m%d-%H%M%S')}")
        try:
            self.path.replace(dest)
            logging.getLogger("applyqueue.vault").error(
                "credential vault was unreadable (%s); moved it to %s", type(err).__name__, dest.name)
        except OSError as move_err:
            self._stuck_corrupt = True  # never overwrite a file we could not move aside
            logging.getLogger("applyqueue.vault").error(
                "credential vault was unreadable (%s) and could not be moved aside: %s", type(err).__name__, move_err)

    def _write(self, data: dict) -> None:
        if getattr(self, "_stuck_corrupt", False):
            raise OSError(f"{self.path.name} is unreadable and could not be moved aside; not overwriting it")
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

    def all_entries(self) -> dict[str, dict]:
        """Every per-site entry, passwords included (sync engine only)."""
        with self._lock:
            return {h: dict(e) for h, e in self._read()["sites"].items()}

    def upsert_synced(self, entries: dict[str, dict]) -> int:
        """Merge entries from the sync repo: per host, the newer ``updated_at`` wins."""
        changed = 0
        with self._lock:
            data = self._read()
            for host, e in entries.items():
                if not host or not e.get("password"):
                    continue
                h = normalize_host(host)
                cur = data["sites"].get(h)
                if cur and (cur.get("updated_at") or cur.get("created_at") or "") >= (e.get("updated_at") or ""):
                    continue
                data["sites"][h] = {"username": e.get("username", ""), "password": e["password"],
                                    "created_at": (cur or {}).get("created_at") or e.get("updated_at") or _now(),
                                    "updated_at": e.get("updated_at") or _now(),
                                    "source": "sync", **({"login_url": e["login_url"]} if e.get("login_url") else {}),
                                    **({"sign_in_with": e["sign_in_with"]} if e.get("sign_in_with") else {})}
                changed += 1
            if changed:
                self._write(data)
        return changed

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
