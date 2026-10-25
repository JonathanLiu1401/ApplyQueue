"""Per-device files for sync: config, the GitHub token, the device id, the optional encryption key.

None of these are ever written into the sync repo (token_guard lists their names).
"""
from __future__ import annotations

import json
import os
import secrets
import sys
from pathlib import Path

try:
    from core.config import get_data_dir
except ImportError:  # pragma: no cover
    from backend.core.config import get_data_dir

DEFAULT_CONFIG = {
    "enabled": False,
    "kind": "github",          # "github" (visibility verified via API) or "git" (any remote; no credentials)
    "owner": "",
    "repo": "",
    "remote_url": "",          # derived for github; tests and "git" kind set it directly
    "client_id": "",           # the user's own GitHub OAuth App / GitHub App (device flow enabled)
    "sync_credentials": True,  # Art's ruling: portal logins + sessions live in the private repo
    "sync_sessions": True,
    "encrypt_credentials": False,  # optional (PLAN §6.3), off by default
    "device_name": "",
    "lock_ttl_s": 2 * 3600,
    "auto_minutes": 5,
}


def sync_dir() -> Path:
    d = get_data_dir() / "sync"
    d.mkdir(parents=True, exist_ok=True)
    return d


def vault_dir() -> Path:
    d = get_data_dir() / "vault"
    d.mkdir(parents=True, exist_ok=True)
    if sys.platform != "win32":
        os.chmod(d, 0o700)
    return d


def repo_dir() -> Path:
    return sync_dir() / "repo"


def _read(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def write_private(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)
    os.replace(tmp, path)
    if sys.platform != "win32":
        os.chmod(path, 0o600)


def load_config() -> dict:
    cfg = dict(DEFAULT_CONFIG)
    raw = _read(sync_dir() / "config.json", {})
    if isinstance(raw, dict):
        cfg.update({k: v for k, v in raw.items() if k in DEFAULT_CONFIG})
    return cfg


def save_config(cfg: dict) -> dict:
    clean = {k: cfg.get(k, v) for k, v in DEFAULT_CONFIG.items()}
    write_private(sync_dir() / "config.json", clean)
    return clean


def token_file() -> Path:
    return vault_dir() / "github_token.json"


def load_token() -> dict:
    t = _read(token_file(), {})
    return t if isinstance(t, dict) else {}


def save_token(tok: dict) -> None:
    write_private(token_file(), tok)


def clear_token() -> None:
    try:
        token_file().unlink()
    except FileNotFoundError:
        pass


def device_id() -> str:
    p = sync_dir() / "device.json"
    d = _read(p, {})
    if not isinstance(d, dict) or not d.get("id"):
        d = {"id": "device-" + secrets.token_hex(4)}
        write_private(p, d)
    return d["id"]


def device_label() -> str:
    return (load_config().get("device_name") or "").strip()[:40] or device_id()


def key_file() -> Path:
    return vault_dir() / "sync_key.json"
