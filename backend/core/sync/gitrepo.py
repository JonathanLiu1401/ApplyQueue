"""Thin wrapper over the git CLI for the sync repo.

Auth uses an http extraheader passed through ``GIT_CONFIG_*`` environment variables, so the
token is never written to .git/config or shown on a command line.
"""
from __future__ import annotations

import base64
import os
import shutil
import subprocess
from pathlib import Path

AUTHOR = ("ApplyQueue", "applyqueue@users.noreply.invalid")


class GitError(Exception):
    def __init__(self, msg: str, stderr: str = ""):
        super().__init__(msg)
        self.stderr = stderr


class PushRejected(GitError):
    pass


class NetworkError(GitError):
    pass


_NET_HINTS = ("could not resolve host", "unable to access", "connection refused", "timed out",
              "failed to connect", "does not appear to be a git repository", "could not read from remote",
              "network is unreachable", "no such file or directory", "does not exist",
              "operation timed out", "temporary failure in name resolution", "ssl")


# A concurrent push from another device won the ref update between our fetch and our push.
# git reports that as a remote-side ref lock failure, not "[rejected]" (local bare remotes and
# github.com both do this): it is a normal lost race, so fetch/merge/retry like non-fast-forward.
_RACE_HINTS = ("cannot lock ref", "failed to update ref", "incorrect old value provided",
               "failed to lock", "reference already exists")


def git_available() -> str | None:
    return shutil.which("git")


class GitRepo:
    def __init__(self, path: Path, token: str | None = None):
        self.path = Path(path)
        self.token = token

    def _env(self) -> dict:
        env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_ASKPASS": "", "SSH_ASKPASS": "",
               "LC_ALL": "C", "GIT_AUTHOR_NAME": AUTHOR[0], "GIT_AUTHOR_EMAIL": AUTHOR[1],
               "GIT_COMMITTER_NAME": AUTHOR[0], "GIT_COMMITTER_EMAIL": AUTHOR[1]}
        if self.token:
            basic = base64.b64encode(f"x-access-token:{self.token}".encode()).decode()
            env.update({"GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "http.extraheader",
                        "GIT_CONFIG_VALUE_0": f"AUTHORIZATION: basic {basic}"})
        return env

    def run(self, *args: str, check: bool = True, cwd: Path | None = None, timeout: int = 120) -> subprocess.CompletedProcess:
        git = git_available()
        if not git:
            raise GitError("git is not installed. Install git (macOS: xcode-select --install) to use sync.")
        try:
            p = subprocess.run([git, "-c", "core.autocrlf=false", "-c", "core.quotepath=false",
                                "-c", "init.defaultBranch=main", *args],
                               cwd=str(cwd or self.path), env=self._env(), capture_output=True, text=True,
                               encoding="utf-8", errors="replace", timeout=timeout)
        except subprocess.TimeoutExpired as e:
            # A stalled connection (Wi-Fi drop mid-push, captive portal) hangs git rather than
            # failing it. Treat it as offline so the commit stays queued for the next sync.
            raise NetworkError(f"git {args[0]} timed out after {timeout}s", str(e)) from None
        if check and p.returncode != 0:
            err = (p.stderr or p.stdout or "").strip()
            low = err.lower()
            if "[rejected]" in low or "non-fast-forward" in low or "fetch first" in low \
                    or any(h in low for h in _RACE_HINTS):
                raise PushRejected("push rejected", err)
            if any(h in low for h in _NET_HINTS):
                raise NetworkError("remote unreachable", err)
            raise GitError(f"git {args[0]} failed", err)
        return p

    # ── setup ──
    def exists(self) -> bool:
        return (self.path / ".git").is_dir()

    def clone(self, url: str) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.run("clone", "--quiet", url, str(self.path), cwd=self.path.parent)
        self.run("config", "pull.rebase", "false")

    def init_local(self, url: str) -> None:
        """Offline first run: an empty local repo that will publish on the next sync."""
        self.path.mkdir(parents=True, exist_ok=True)
        self.run("init", "--quiet", "-b", "main")
        self.run("remote", "add", "origin", url)
        self.run("config", "pull.rebase", "false")

    def set_remote(self, url: str) -> None:
        cur = self.run("remote", "get-url", "origin", check=False).stdout.strip()
        if cur != url:
            self.run("remote", "set-url" if cur else "add", "origin", url)

    # ── state ──
    def branch(self) -> str:
        out = self.run("symbolic-ref", "--short", "HEAD", check=False).stdout.strip()
        return out or "main"

    def has_head(self) -> bool:
        return self.run("rev-parse", "--verify", "-q", "HEAD", check=False).returncode == 0

    def remote_has_branch(self) -> bool:
        return self.run("rev-parse", "--verify", "-q", f"origin/{self.branch()}", check=False).returncode == 0

    def ahead(self) -> int:
        if not self.has_head():
            return 0
        if not self.remote_has_branch():
            return int(self.run("rev-list", "--count", "HEAD").stdout.strip() or 0)
        return int(self.run("rev-list", "--count", f"origin/{self.branch()}..HEAD").stdout.strip() or 0)

    def dirty(self) -> bool:
        return bool(self.run("status", "--porcelain").stdout.strip())

    def commit_all(self, message: str) -> bool:
        self.run("add", "-A")
        if not self.run("diff", "--cached", "--quiet", check=False).returncode:
            return False
        self.run("commit", "--quiet", "--no-verify", "-m", message)
        return True

    def fetch(self) -> None:
        self.run("fetch", "--quiet", "origin")

    def merge_remote(self) -> list[str]:
        """Merge origin/<branch>. Returns conflicted paths (merge left in progress) or []."""
        if not self.remote_has_branch():
            return []
        ref = f"origin/{self.branch()}"
        if not self.has_head():
            self.run("reset", "--quiet", "--hard", ref)
            return []
        p = self.run("merge", "--no-edit", "--quiet", "--allow-unrelated-histories", ref, check=False)
        if p.returncode == 0:
            return []
        out = self.run("diff", "--name-only", "--diff-filter=U").stdout.split()
        if not out:
            raise GitError("merge failed", p.stderr)
        return out

    def stage_version(self, path: str, stage: int) -> str | None:
        p = self.run("show", f":{stage}:{path}", check=False)
        return p.stdout if p.returncode == 0 else None

    def resolve(self, path: str, content: str | None) -> None:
        f = self.path / path
        if content is None:
            if f.exists():
                f.unlink()
            self.run("rm", "--quiet", "--cached", "--ignore-unmatch", path)
        else:
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(content, encoding="utf-8")
            self.run("add", path)

    def finish_merge(self) -> None:
        self.run("commit", "--quiet", "--no-verify", "--no-edit")

    def push(self) -> None:
        self.run("push", "--quiet", "origin", f"HEAD:{self.branch()}")
