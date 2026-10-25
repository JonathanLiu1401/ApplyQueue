"""GitHub device flow + the few REST calls sync needs.

The user registers their own OAuth App (or GitHub App) with device flow enabled and pastes its
client ID; no client secret is involved. A fine-grained personal access token also works.
The token lives only in ``<data>/vault/github_token.json`` (0600) on this device.
"""
from __future__ import annotations

import time

import httpx

from . import store

# Module-level so tests can point them at a mock server.
WEB = "https://github.com"
API = "https://api.github.com"
SCOPE = "repo"
# Never use the public app repos as a state repo.
FORBIDDEN_REPOS = {("jaimaann", "langhire"), ("jonathanliu1401", "applyqueue")}


class GitHubError(Exception):
    pass


class NotPrivate(GitHubError):
    """The repo is public/internal or its visibility could not be confirmed."""


class Offline(GitHubError):
    pass


def _headers(token: str | None = None) -> dict:
    h = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28",
         "User-Agent": "ApplyQueue"}
    if token:
        h["Authorization"] = f"Bearer {token}"
    return h


def start_device_flow(client_id: str) -> dict:
    client_id = (client_id or "").strip()
    if not client_id:
        raise GitHubError("Enter the client ID of your GitHub OAuth App first.")
    try:
        r = httpx.post(f"{WEB}/login/device/code", data={"client_id": client_id, "scope": SCOPE},
                       headers={"Accept": "application/json"}, timeout=20)
    except httpx.HTTPError as e:
        raise Offline(f"GitHub unreachable: {type(e).__name__}") from e
    body = r.json() if r.content else {}
    if r.status_code != 200 or "device_code" not in body:
        raise GitHubError(body.get("error_description") or body.get("error") or f"HTTP {r.status_code}")
    return {"device_code": body["device_code"], "user_code": body["user_code"],
            "verification_uri": body.get("verification_uri", f"{WEB}/login/device"),
            "interval": int(body.get("interval", 5)), "expires_at": time.time() + int(body.get("expires_in", 900))}


def poll_device_flow(client_id: str, device_code: str) -> dict:
    """One poll. Returns {"status": "pending"|"slow_down"|"ok"|"denied"|"expired", ...}."""
    try:
        r = httpx.post(f"{WEB}/login/oauth/access_token",
                       data={"client_id": client_id, "device_code": device_code,
                             "grant_type": "urn:ietf:params:oauth:grant-type:device_code"},
                       headers={"Accept": "application/json"}, timeout=20)
    except httpx.HTTPError as e:
        raise Offline(f"GitHub unreachable: {type(e).__name__}") from e
    body = r.json() if r.content else {}
    if body.get("access_token"):
        tok = {"access_token": body["access_token"], "token_type": body.get("token_type", "bearer"),
               "scope": body.get("scope", ""), "obtained_at": time.time(), "source": "device_flow"}
        if body.get("refresh_token"):
            tok["refresh_token"] = body["refresh_token"]
            tok["expires_at"] = time.time() + int(body.get("expires_in", 28800))
        store.save_token(tok)
        return {"status": "ok"}
    err = body.get("error", "")
    return {"status": {"authorization_pending": "pending", "slow_down": "slow_down",
                       "access_denied": "denied", "expired_token": "expired"}.get(err, "error"),
            "error": body.get("error_description") or err}


def save_pat(token: str) -> None:
    token = (token or "").strip()
    if len(token) < 20 or any(c.isspace() for c in token):
        raise GitHubError("That does not look like a GitHub token.")
    store.save_token({"access_token": token, "source": "pat", "obtained_at": time.time()})


def token() -> str:
    t = store.load_token()
    if not t.get("access_token"):
        raise GitHubError("GitHub is not linked.")
    if t.get("expires_at") and t["expires_at"] < time.time():
        raise GitHubError("The GitHub sign-in expired. Link GitHub again.")
    return t["access_token"]


def _api(method: str, path: str, **kw) -> httpx.Response:
    try:
        return httpx.request(method, f"{API}{path}", headers=_headers(token()), timeout=20, **kw)
    except httpx.HTTPError as e:
        raise Offline(f"GitHub unreachable: {type(e).__name__}") from e


def current_user() -> dict:
    r = _api("GET", "/user")
    if r.status_code == 401:
        raise GitHubError("GitHub rejected the token. Link GitHub again.")
    r.raise_for_status()
    return r.json()


def _check_name(owner: str, repo: str) -> None:
    if (owner.lower(), repo.lower()) in FORBIDDEN_REPOS:
        raise NotPrivate("That is the ApplyQueue app repository. Use a separate private state repo.")


def get_repo(owner: str, repo: str) -> dict | None:
    r = _api("GET", f"/repos/{owner}/{repo}")
    if r.status_code == 404:
        return None
    if r.status_code == 401:
        raise GitHubError("GitHub rejected the token. Link GitHub again.")
    r.raise_for_status()
    return r.json()


def create_private_repo(name: str) -> dict:
    r = _api("POST", "/user/repos", json={
        "name": name, "private": True, "auto_init": True, "has_issues": False, "has_wiki": False,
        "has_projects": False,
        "description": "ApplyQueue state (portal logins + sessions). MUST stay private.",
    })
    if r.status_code not in (200, 201):
        raise GitHubError(f"Could not create the repo: {r.json().get('message', r.status_code)}")
    data = r.json()
    if not data.get("private"):
        raise NotPrivate("GitHub created the repo as public; refusing to use it.")
    return data


def verify_private(owner: str, repo: str) -> dict:
    """GET /repos/{owner}/{repo} and require private=true. Raises NotPrivate or Offline."""
    _check_name(owner, repo)
    data = get_repo(owner, repo)
    if data is None:
        raise NotPrivate("The state repo was not found or this token cannot see it.")
    vis = data.get("visibility") or ("private" if data.get("private") else "public")
    if data.get("private") is not True or vis != "private":
        raise NotPrivate(f"The state repo is {vis}. It holds your portal passwords; make it private on GitHub.")
    perms = data.get("permissions") or {}
    if perms and not perms.get("push"):
        raise GitHubError("This token cannot push to the state repo.")
    warnings = []
    if data.get("fork"):
        warnings.append("The state repo is a fork.")
    return {"private": True, "warnings": warnings, "checked_at": time.time(),
            "html_url": data.get("html_url", "")}


def https_remote(owner: str, repo: str) -> str:
    return f"{WEB}/{owner}/{repo}.git"
