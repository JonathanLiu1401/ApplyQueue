"""Guard against subscription/OAuth tokens leaking into the sync repo (PLAN §3.2).

ApplyQueue never reads, stores or forwards a vendor CLI's login token (Codex
"Sign in with ChatGPT", Claude Code, Cursor, Grok). Those tokens stay in the
vendor CLI's own store on the device. The cross-device sync repo (M5) must
never receive one by accident either, so every sync write goes through
:func:`safe_sync_write`, which refuses content or file names that look like
OAuth/API credentials. The same detector scrubs CLI output before it is logged.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

# File names vendor CLIs / OAuth flows keep credentials in. Never synced.
FORBIDDEN_FILENAMES = frozenset({
    "auth.json",            # Codex (~/.codex/auth.json)
    ".credentials.json",    # Claude Code
    "credentials.json",
    "email_tokens.json",    # ApplyQueue's own email OAuth tokens
    "email_accounts.json",
    "site_credentials.json",
    ".api_token",
    "llm_settings.json",
    "github_token.json",    # the sync repo's own GitHub token (device-only)
    "sync_key.json",        # optional sync encryption key (device-only)
})

_PATTERNS: dict[str, re.Pattern] = {
    "jwt": re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"),
    "openai_key": re.compile(r"\bsk-(?:proj-|svcacct-|admin-)?[A-Za-z0-9_-]{20,}"),
    "anthropic_key": re.compile(r"\bsk-ant-[A-Za-z0-9_-]{20,}"),
    "xai_key": re.compile(r"\bxai-[A-Za-z0-9]{20,}"),
    "google_refresh_token": re.compile(r"\b1//0[A-Za-z0-9_-]{20,}"),
    "google_access_token": re.compile(r"\bya29\.[A-Za-z0-9_-]{20,}"),
    "github_token": re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}"),
    "bearer_header": re.compile(r"(?i)\bauthorization:\s*bearer\s+[A-Za-z0-9._~+/=-]{20,}"),
}
_TOKEN_KEYS = re.compile(
    r'"(access_token|refresh_token|id_token|OPENAI_API_KEY|api_key|apiKey|oauthAccessToken|session_token)"\s*:\s*"([^"]{16,})"'
)
# Codex auth.json: {"OPENAI_API_KEY": ..., "tokens": {"id_token", "access_token", "refresh_token", "account_id"}, "last_refresh": ...}
_CODEX_SHAPE_KEYS = {"tokens", "last_refresh"}


class SyncSecretError(ValueError):
    """Raised when a sync write would carry a credential."""


def find_tokens(text: str) -> list[str]:
    """Kinds of credential found in *text* (empty list when clean)."""
    if not text:
        return []
    kinds = [name for name, pat in _PATTERNS.items() if pat.search(text)]
    if _TOKEN_KEYS.search(text):
        kinds.append("oauth_token_field")
    stripped = text.lstrip()
    if stripped.startswith("{"):
        try:
            data = json.loads(stripped)
        except ValueError:
            data = None
        if isinstance(data, dict) and _CODEX_SHAPE_KEYS & set(data) and isinstance(data.get("tokens"), dict):
            kinds.append("codex_auth_json")
    return sorted(set(kinds))


def scrub(text: str) -> str:
    """Redact anything token-shaped (for logs and stored error messages)."""
    if not text:
        return text
    for pat in _PATTERNS.values():
        text = pat.sub("[REDACTED]", text)
    return _TOKEN_KEYS.sub(lambda m: f'"{m.group(1)}": "[REDACTED]"', text)


# A job portal's own signed-in session legitimately carries JWT cookies and access_token
# fields (Art's ruling: portal sessions sync). Vendor/AI/email tokens are still refused.
PORTAL_SESSION_ALLOWED = frozenset({"jwt", "oauth_token_field", "bearer_header"})


def check_sync_payload(rel_path: str, content: str | bytes, *, portal_session: bool = False,
                       scan_content: bool = True) -> None:
    """Raise SyncSecretError if writing *content* to *rel_path* in the sync repo is unsafe.

    ``portal_session`` relaxes only the generic JWT/access_token checks (the session sanitizer
    has already dropped AI-vendor and mail-provider origins). ``scan_content=False`` is for
    ciphertext whose plaintext was checked before encryption.
    """
    name = Path(rel_path).name
    if name in FORBIDDEN_FILENAMES or name.endswith((".pem", ".key")):
        raise SyncSecretError(f"refusing to sync credential file {name!r}")
    if not scan_content:
        return
    if isinstance(content, bytes):
        content = content.decode("utf-8", errors="replace")
    kinds = find_tokens(content)
    if portal_session:
        kinds = [k for k in kinds if k not in PORTAL_SESSION_ALLOWED]
    if kinds:
        raise SyncSecretError(
            f"refusing to sync {rel_path!r}: looks like it contains {', '.join(kinds)} "
            "(subscription/OAuth tokens stay on this device)"
        )


def _sync_target(repo_root: Path, rel_path: str) -> Path:
    root = Path(repo_root).resolve()
    target = (root / rel_path).resolve()
    if root == target or root not in target.parents:
        raise SyncSecretError("path escapes the sync repo")
    if ".git" in target.relative_to(root).parts:
        raise SyncSecretError("refusing to write inside .git")
    return target


def safe_sync_write(repo_root: Path, rel_path: str, content: str, *, portal_session: bool = False,
                    scan_content: bool = True) -> Path:
    """The only way M5 sync code may write into the sync repo working tree."""
    target = _sync_target(repo_root, rel_path)
    check_sync_payload(rel_path, content, portal_session=portal_session, scan_content=scan_content)
    target.parent.mkdir(parents=True, exist_ok=True)
    with open(target, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(content)
    return target


def safe_sync_append(repo_root: Path, rel_path: str, line: str) -> Path:
    """Append one JSON line (ledger/queue files are append-only and union-merged)."""
    if "\n" in line:
        raise SyncSecretError("ledger lines must be single-line JSON")
    target = _sync_target(repo_root, rel_path)
    check_sync_payload(rel_path, line)
    target.parent.mkdir(parents=True, exist_ok=True)
    with open(target, "a", encoding="utf-8", newline="\n") as fh:
        fh.write(line + "\n")
    return target


def safe_sync_delete(repo_root: Path, rel_path: str) -> None:
    target = _sync_target(repo_root, rel_path)
    if target.exists():
        target.unlink()
