"""OAuth 2.0 (authorization code + PKCE, loopback redirect) for Gmail and Outlook.

Google refresh tokens issued to an app in "Testing" publishing status expire after 7 days, so a
Gmail link is flagged for a one-click reconnect at day 6 (or as soon as Google answers
invalid_grant). Microsoft public clients use PKCE without a secret.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import threading
import time
from pathlib import Path
from urllib.parse import urlencode

import httpx

PROVIDERS = ("gmail", "outlook")
NAMES = {"gmail": "Gmail", "outlook": "Outlook"}

# Module-level so tests can point them at mock servers.
ENDPOINTS = {
    "gmail": {
        "auth": "https://accounts.google.com/o/oauth2/v2/auth",
        "token": "https://oauth2.googleapis.com/token",
        "revoke": "https://oauth2.googleapis.com/revoke",
    },
    "outlook": {
        "auth": "https://login.microsoftonline.com/{tenant}/oauth2/v2.0/authorize",
        "token": "https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token",
        "revoke": "",
    },
}
SCOPES = {
    "gmail": ["https://www.googleapis.com/auth/gmail.modify"],
    "outlook": ["offline_access", "Mail.ReadWrite", "User.Read"],
}
CALLBACK_PATH = "/email/oauth/callback"
REAUTH_AFTER_S = 6 * 24 * 3600   # Google "Testing" apps: refresh tokens die at 7 days
STATE_TTL_S = 15 * 60
_TOKEN_KEYS = ("refresh_token", "access_token", "expires_at")

_lock = threading.RLock()
_pending: dict[str, dict] = {}


class ReauthRequired(Exception):
    """The provider rejected the refresh token (invalid_grant) or the account was never linked."""


class OAuthError(Exception):
    pass


# ── storage ─────────────────────────────────────────────────────────────
def _data_dir() -> Path:
    try:
        from core.config import get_data_dir
    except ImportError:  # pragma: no cover
        from backend.core.config import get_data_dir
    return get_data_dir()


def accounts_file() -> Path:
    return _data_dir() / "vault" / "email_accounts.json"


def load_accounts() -> dict:
    p = accounts_file()
    try:
        data = json.loads(p.read_text("utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_accounts(data: dict) -> None:
    p = accounts_file()
    p.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(p.parent, 0o700)
    except OSError:
        pass
    tmp = p.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, p)
    try:
        os.chmod(p, 0o600)
    except OSError:
        pass


def _account(provider: str) -> dict:
    return dict(load_accounts().get(provider) or {})


def _update(provider: str, **fields) -> dict:
    with _lock:
        data = load_accounts()
        acct = dict(data.get(provider) or {})
        for k, v in fields.items():
            if v is None:
                acct.pop(k, None)
            else:
                acct[k] = v
        data[provider] = acct
        save_accounts(data)
        return acct


def _check(provider: str) -> None:
    if provider not in PROVIDERS:
        raise OAuthError(f"unknown provider {provider!r}")


# ── config ──────────────────────────────────────────────────────────────
def set_client(provider: str, client_id: str, client_secret: str | None = None, tenant: str | None = None,
               testing_app: bool | None = None) -> dict:
    """Store the user's own OAuth client. Changing the client drops the old tokens."""
    _check(provider)
    client_id = (client_id or "").strip()
    if not client_id or len(client_id) > 300 or any(c.isspace() for c in client_id):
        raise OAuthError("client_id looks invalid")
    old = _account(provider)
    fields: dict = {"client_id": client_id}
    if client_secret is not None:
        fields["client_secret"] = client_secret.strip() or None
    if provider == "outlook":
        t = (tenant or old.get("tenant") or "common").strip()
        if not t.replace("-", "").replace(".", "").isalnum():
            raise OAuthError("tenant looks invalid")
        fields["tenant"] = t
    if testing_app is not None:
        fields["testing_app"] = bool(testing_app)
    if old.get("client_id") and old["client_id"] != client_id:
        for k in (*_TOKEN_KEYS, "email", "authorized_at", "needs_reauth", "last_error"):
            fields[k] = None
    _update(provider, **fields)
    return status(provider)


def _endpoint(provider: str, kind: str) -> str:
    url = ENDPOINTS[provider][kind]
    return url.replace("{tenant}", _account(provider).get("tenant") or "common")


def redirect_uri(provider: str, port: int) -> str:
    # Google desktop clients accept any loopback port; Microsoft ignores the port of http://localhost.
    host = "127.0.0.1" if provider == "gmail" else "localhost"
    return f"http://{host}:{int(port)}{CALLBACK_PATH}"


def registration_uri(provider: str) -> str:
    """What the user registers in their Google/Microsoft console."""
    return "http://127.0.0.1" if provider == "gmail" else f"http://localhost{CALLBACK_PATH}"


# ── authorization flow ──────────────────────────────────────────────────
def start_authorization(provider: str, port: int) -> str:
    _check(provider)
    acct = _account(provider)
    if not acct.get("client_id"):
        raise OAuthError(f"Add your {NAMES[provider]} OAuth client ID first")
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    state = secrets.token_urlsafe(32)
    redir = redirect_uri(provider, port)
    now = time.time()
    with _lock:
        for k in [k for k, v in _pending.items() if now - v["created"] > STATE_TTL_S]:
            _pending.pop(k, None)
        _pending[state] = {"provider": provider, "verifier": verifier, "redirect_uri": redir, "created": now}
    params = {
        "client_id": acct["client_id"],
        "response_type": "code",
        "redirect_uri": redir,
        "scope": " ".join(SCOPES[provider]),
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    if provider == "gmail":
        params.update(access_type="offline", prompt="consent", include_granted_scopes="true")
        if acct.get("email"):
            params["login_hint"] = acct["email"]
    else:
        params.update(response_mode="query", prompt="select_account")
    return _endpoint(provider, "auth") + "?" + urlencode(params)


def _token_request(provider: str, form: dict) -> dict:
    acct = _account(provider)
    form = {**form, "client_id": acct.get("client_id", "")}
    if provider == "gmail" and acct.get("client_secret"):
        form["client_secret"] = acct["client_secret"]
    if provider == "outlook":
        form["scope"] = " ".join(SCOPES[provider])
    try:
        r = httpx.post(_endpoint(provider, "token"), data=form, timeout=20,
                       headers={"Accept": "application/json"})
    except httpx.HTTPError as e:
        raise OAuthError(f"{NAMES[provider]} token endpoint unreachable: {type(e).__name__}") from None
    try:
        body = r.json()
    except ValueError:
        body = {}
    if r.status_code >= 400 or "access_token" not in body:
        err = str(body.get("error") or f"HTTP {r.status_code}")
        if err == "invalid_grant":
            raise ReauthRequired(err)
        desc = str(body.get("error_description") or "")[:200]
        raise OAuthError(f"{err}: {desc}" if desc else err)
    return body


def _store_tokens(provider: str, body: dict, **extra) -> None:
    fields = {"access_token": body["access_token"],
              "expires_at": time.time() + float(body.get("expires_in") or 3600)}
    if body.get("refresh_token"):
        fields["refresh_token"] = body["refresh_token"]
    _update(provider, **fields, **extra)


def complete_authorization(state: str, code: str) -> str:
    """Exchange the code. Returns the provider id. The state must be one we issued (CSRF guard)."""
    with _lock:
        pend = _pending.pop(state or "", None)
    if not pend or time.time() - pend["created"] > STATE_TTL_S:
        raise OAuthError("This sign-in link expired or was not started by ApplyQueue. Try Connect again.")
    provider = pend["provider"]
    try:
        body = _token_request(provider, {"grant_type": "authorization_code", "code": code,
                                         "redirect_uri": pend["redirect_uri"], "code_verifier": pend["verifier"]})
    except ReauthRequired:
        raise OAuthError("The sign-in code was rejected. Try Connect again.") from None
    if not body.get("refresh_token") and not _account(provider).get("refresh_token"):
        raise OAuthError("No refresh token was returned (offline access was not granted).")
    _store_tokens(provider, body, authorized_at=time.time(), needs_reauth=None, last_error=None)
    try:  # best effort: show which mailbox is linked
        try:
            from core.mail.providers import client_for
        except ImportError:  # pragma: no cover
            from backend.core.mail.providers import client_for
        _update(provider, email=client_for(provider).profile_email())
    except Exception:
        pass
    return provider


def access_token(provider: str) -> str:
    """A valid access token, refreshing when needed. Raises ReauthRequired on invalid_grant."""
    _check(provider)
    with _lock:
        acct = _account(provider)
        if not acct.get("refresh_token"):
            raise ReauthRequired("not linked")
        if acct.get("needs_reauth"):
            raise ReauthRequired("needs reauth")
        if acct.get("access_token") and float(acct.get("expires_at") or 0) - 60 > time.time():
            return acct["access_token"]
        try:
            body = _token_request(provider, {"grant_type": "refresh_token", "refresh_token": acct["refresh_token"]})
        except ReauthRequired:
            _update(provider, needs_reauth=True, access_token=None, last_error="invalid_grant")
            raise
        _store_tokens(provider, body, last_error=None)
        return body["access_token"]


def mark_reauth(provider: str, reason: str = "invalid_grant") -> None:
    _update(provider, needs_reauth=True, access_token=None, last_error=reason[:200])


def disconnect(provider: str) -> None:
    _check(provider)
    acct = _account(provider)
    tok = acct.get("refresh_token")
    if tok and ENDPOINTS[provider].get("revoke"):
        try:
            httpx.post(ENDPOINTS[provider]["revoke"], data={"token": tok}, timeout=10)
        except httpx.HTTPError:
            pass
    _update(provider, **{k: None for k in (*_TOKEN_KEYS, "email", "authorized_at", "needs_reauth", "last_error")})


def is_linked(provider: str) -> bool:
    acct = _account(provider)
    return bool(acct.get("refresh_token")) and not acct.get("needs_reauth")


def status(provider: str, now: float | None = None) -> dict:
    """Public view. Never includes tokens or the client secret."""
    _check(provider)
    acct = _account(provider)
    now = time.time() if now is None else now
    linked = bool(acct.get("refresh_token"))
    authorized = acct.get("authorized_at")
    due_at = None
    if linked and authorized and provider == "gmail" and acct.get("testing_app", True):
        due_at = float(authorized) + REAUTH_AFTER_S
    needs = bool(acct.get("needs_reauth"))
    return {
        "provider": provider,
        "name": NAMES[provider],
        "configured": bool(acct.get("client_id")),
        "client_id": acct.get("client_id", ""),
        "client_secret_set": bool(acct.get("client_secret")),
        "tenant": acct.get("tenant", "common") if provider == "outlook" else None,
        "testing_app": bool(acct.get("testing_app", True)) if provider == "gmail" else None,
        "linked": linked and not needs,
        "email": acct.get("email") or "",
        "authorized_at": authorized,
        "reauth_due_at": due_at,
        "needs_reauth": needs,
        "reauth_due": needs or (due_at is not None and now >= due_at),
        "last_error": acct.get("last_error") or "",
        "registration_redirect_uri": registration_uri(provider),
        "scopes": SCOPES[provider],
    }
