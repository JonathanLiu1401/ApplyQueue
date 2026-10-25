"""The sync engine: one private git repo shared by every device and agent of one user.

Layout (docs/applyqueue/DESIGN.md §6)::

    README.md, .gitattributes            (*.jsonl merge=union)
    ledger/applications.jsonl            append-only events, union-merged
    queue/postings/<yyyy-mm>.jsonl       append-only postings, union-merged
    locks/<job_key>.json                 create-only claim locks with a TTL
    credentials/portals.json[.enc]       portal logins, last-writer-wins per id by updatedAt
    sessions/<profile>/storage.json[.enc] portal sessions (AI/mail origins stripped)
    workers/<device>.json                heartbeat
    locks/search-<id>.json               single-runner lock for one scheduled search run
    schedules/searches.json              scheduled searches for always-on agents (no secrets)
    schedules/runs.jsonl                 scheduled runs by any device or agent, union-merged

Every write goes through ``token_guard`` (no AI/email OAuth tokens ever). For a GitHub repo
the visibility is checked via ``GET /repos/{owner}/{repo}`` before every credential/session
write and before every push; a public/internal repo is refused and credentials stay local.
Offline: commits accumulate locally and are pushed by the next successful sync.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlparse

from filelock import FileLock

try:
    from core import job_status as js
    from core import jobs_store, token_guard
    from core.credential_vault import CredentialVault, normalize_host
except ImportError:  # pragma: no cover
    from backend.core import job_status as js
    from backend.core import jobs_store, token_guard
    from backend.core.credential_vault import CredentialVault, normalize_host

from . import crypto, github, store
from .gitrepo import GitError, GitRepo, NetworkError, PushRejected, git_available

log = logging.getLogger(__name__)

LEDGER = "ledger/applications.jsonl"
SESSION_SYNC_MAX_BYTES = 5 * 1024 * 1024
PORTALS = "credentials/portals.json"
README = """# ApplyQueue state (private)

This repository holds your job portal passwords and signed-in sessions. It must stay private.
Anyone with access to it can sign in as you. Never make it public, never add collaborators you
would not give your passwords to.

Written by ApplyQueue. Do not edit by hand while a run is active.
"""
GITATTRIBUTES = "*.jsonl merge=union\n"

STATUS_TO_EVENT = {
    js.PENDING: "queued", js.IN_PROGRESS: "claimed", js.SUBMITTED_UNCONFIRMED: "submitted",
    js.LEGACY_APPLIED: "submitted", js.CONFIRMED: "confirmed", js.NEEDS_HUMAN: "needs_human",
    js.FAILED: "failed", js.BLOCKED: "blocked",
}
EVENT_TO_STATUS = {"submitted": js.SUBMITTED_UNCONFIRMED, "confirmed": js.CONFIRMED,
                   "needs_human": js.NEEDS_HUMAN, "blocked": js.BLOCKED}
# A job whose ledger ends in one of these is finished: never claimed (and applied) again.
DONE_EVENTS = ("submitted", "confirmed", "needs_human", "blocked")
# Compaction priority: email-confirmed > submitted > blocking > failed > claimed > queued.
PRIORITY = {"confirmed": 6, "submitted": 5, "needs_human": 4, "blocked": 4, "failed": 2,
            "claimed": 1, "released": 1, "queued": 0}
_STATUS_RANK = {js.PENDING: 0, js.FAILED: 0, js.IN_PROGRESS: 1, js.NEEDS_HUMAN: 4, js.BLOCKED: 4,
                js.SUBMITTED_UNCONFIRMED: 5, js.LEGACY_APPLIED: 5, js.CONFIRMED: 6}
POSTING_FIELDS = ("title", "company", "location", "source", "category", "search_id")
# Never copied into the sync repo as part of a portal session.
SESSION_DENY_DOMAINS = (
    "google.com", "googleapis.com", "gmail.com", "microsoftonline.com", "live.com", "outlook.com",
    "office.com", "microsoft.com", "openai.com", "chatgpt.com", "anthropic.com", "claude.ai",
    "x.ai", "grok.com", "x.com", "cursor.com", "cursor.sh", "github.com", "githubusercontent.com",
)
_TRACKING = re.compile(r"^(utm_|gh_src$|source$|ref$|src$|lever-origin$|trk)")


def _auto_search_ids() -> set[str]:
    try:
        try:
            from core.searches import store as search_store
        except ImportError:  # pragma: no cover
            from backend.core.searches import store as search_store
        return {x["id"] for x in search_store.list_searches() if x.get("mode") == "auto"}
    except Exception:
        return set()


def _clock() -> float:
    """The lock clock (a seam so tests can move time without sleeping)."""
    return time.time()


def _now() -> str:
    return datetime.fromtimestamp(_clock(), timezone.utc).isoformat()


def _parse(ts: str) -> float:
    try:
        return datetime.fromisoformat(ts).timestamp()
    except (TypeError, ValueError):
        return 0.0


def canonical_url(url: str) -> str:
    p = urlparse((url or "").strip())
    host = (p.hostname or "").lower().removeprefix("www.")
    q = sorted((k, v) for k, v in parse_qsl(p.query) if not _TRACKING.match(k.lower()))
    path = p.path.rstrip("/") or "/"
    return f"{host}{path}" + (f"?{urlencode(q)}" if q else "")


def job_key(url: str) -> str:
    """Stable, filename-safe key shared by every device: <host>-<hash of canonical url>."""
    c = canonical_url(url)
    host = re.sub(r"[^a-z0-9.-]", "", c.split("/")[0])[:60] or "job"
    return f"{host}-{hashlib.sha256(c.encode()).hexdigest()[:16]}"


def sanitize_storage_state(state: dict) -> dict:
    """Keep only job-portal cookies/origins: drop AI vendors, mail providers and GitHub."""
    def bad(host: str) -> bool:
        h = (host or "").lower().lstrip(".")
        return any(h == d or h.endswith("." + d) for d in SESSION_DENY_DOMAINS)
    cookies = [c for c in state.get("cookies", []) if isinstance(c, dict) and not bad(c.get("domain", ""))]
    origins = [o for o in state.get("origins", [])
               if isinstance(o, dict) and not bad(urlparse(o.get("origin", "")).hostname or "")]
    return {"cookies": cookies, "origins": origins}


class NotConfigured(Exception):
    pass


class SyncEngine:
    def __init__(self, cfg: dict | None = None):
        self.cfg = cfg or store.load_config()
        self.dir = store.sync_dir()
        self._lock = FileLock(str(self.dir / ".engine.lock"))
        self.device = store.device_id()
        self.label = store.device_label()

    # ── config / plumbing ──
    @property
    def is_github(self) -> bool:
        return self.cfg.get("kind", "github") == "github"

    def remote_url(self) -> str:
        if self.cfg.get("remote_url"):
            return self.cfg["remote_url"]
        if self.is_github and self.cfg.get("owner") and self.cfg.get("repo"):
            return github.https_remote(self.cfg["owner"], self.cfg["repo"])
        raise NotConfigured("No sync repo is set up.")

    def _token(self) -> str | None:
        if not self.is_github:
            return None
        try:
            return github.token()
        except github.GitHubError:
            return None

    def repo(self) -> GitRepo:
        return GitRepo(store.repo_dir(), self._token())

    def _state(self) -> dict:
        try:
            return json.loads((self.dir / "state.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _save_state(self, st: dict) -> None:
        store.write_private(self.dir / "state.json", st)

    def _update_state(self, **kw) -> dict:
        st = self._state()
        st.update(kw)
        self._save_state(st)
        return st

    def credentials_allowed(self) -> bool:
        return self.is_github and bool(self.cfg.get("sync_credentials"))

    def verify_private(self) -> dict:
        """Raises github.NotPrivate / github.Offline. No-op for a plain git remote."""
        if not self.is_github:
            return {"private": None, "warnings": ["Visibility of a plain git remote cannot be verified."]}
        info = github.verify_private(self.cfg["owner"], self.cfg["repo"])
        self._update_state(verified_private_at=_now(), visibility_error=None)
        return info

    def _write(self, rel: str, content: str, **kw) -> None:
        token_guard.safe_sync_write(store.repo_dir(), rel, content, **kw)

    def _append(self, rel: str, obj: dict) -> None:
        token_guard.safe_sync_append(store.repo_dir(), rel, json.dumps(obj, sort_keys=True, separators=(",", ":")))

    # ── repo setup ──
    def ensure_repo(self) -> GitRepo:
        if not git_available():
            raise GitError("git is not installed. Install git (macOS: xcode-select --install) to use sync.")
        repo = self.repo()
        url = self.remote_url()
        if not repo.exists():
            try:
                repo.clone(url)
            except NetworkError:
                repo.init_local(url)
                self._update_state(offline=True)
        else:
            repo.set_remote(url)
        self._ensure_layout()
        return repo

    def _ensure_layout(self) -> None:
        root = store.repo_dir()
        if not (root / ".gitattributes").exists():
            self._write(".gitattributes", GITATTRIBUTES)
        if not (root / "README.md").exists() or "ApplyQueue state" not in (root / "README.md").read_text(encoding="utf-8", errors="replace"):
            self._write("README.md", README)

    # ── merging ──
    def _resolve_conflicts(self, repo: GitRepo, paths: list[str]) -> list[str]:
        """Resolve a merge of origin into local. Stage 2 = local, stage 3 = remote."""
        lost_locks = []
        for path in paths:
            ours, theirs = repo.stage_version(path, 2), repo.stage_version(path, 3)
            if path.endswith(".jsonl"):
                lines, seen = [], set()
                for blob in (theirs or "", ours or ""):
                    for ln in blob.splitlines():
                        if ln.strip() and ln not in seen:
                            seen.add(ln)
                            lines.append(ln)
                repo.resolve(path, "\n".join(lines) + "\n")
            elif path == "schedules/searches.json":
                try:
                    from core.searches import schedule as _sched
                except ImportError:  # pragma: no cover
                    from backend.core.searches import schedule as _sched
                repo.resolve(path, _sched.merge_conflict(ours, theirs))
            elif path.startswith("credentials/"):
                repo.resolve(path, self._merge_portals_blobs(path, ours, theirs))
            elif path.startswith("locks/"):
                keep = theirs
                if theirs is None or self._lock_expired(theirs):
                    keep = ours
                if ours and keep is theirs:
                    lost_locks.append(Path(path).stem)
                repo.resolve(path, keep)
            elif path.startswith("sessions/"):
                repo.resolve(path, max((b for b in (ours, theirs) if b), key=self._session_ts, default=None))
            else:
                repo.resolve(path, theirs if theirs is not None else ours)
        repo.finish_merge()
        return lost_locks

    def _pull(self, repo: GitRepo) -> list[str]:
        repo.fetch()
        # Writes not committed yet (the layout files of a first run, a write whose commit was
        # interrupted) would make git refuse the merge ("local changes would be overwritten"),
        # or be wiped by the reset of a first-run repo with no HEAD. Commit them first so the
        # normal per-file conflict rules apply.
        if repo.dirty():
            repo.commit_all(f"local changes from {self.label}")
        conflicts = repo.merge_remote()
        return self._resolve_conflicts(repo, conflicts) if conflicts else []

    def _push(self, repo: GitRepo) -> dict:
        """Verify private, push; on rejection merge and retry. Offline leaves commits queued."""
        if repo.ahead() == 0:
            return {"pushed": False, "pending": 0}
        try:
            self.verify_private()
        except github.NotPrivate as e:
            self._update_state(visibility_error=str(e), pending=repo.ahead())
            raise
        except github.Offline:
            self._update_state(offline=True, pending=repo.ahead())
            return {"pushed": False, "pending": repo.ahead(), "offline": True}
        for _ in range(4):
            try:
                repo.push()
                self._update_state(offline=False, pending=0, last_push=_now())
                return {"pushed": True, "pending": 0}
            except PushRejected:
                self._pull(repo)
            except NetworkError:
                self._update_state(offline=True, pending=repo.ahead())
                return {"pushed": False, "pending": repo.ahead(), "offline": True}
        self._update_state(pending=repo.ahead())
        return {"pushed": False, "pending": repo.ahead(), "contended": True}

    # ── ledger ──
    def append_event(self, url: str, event: str, job: dict | None = None, **extra) -> dict:
        row = {"id": hashlib.sha256(f"{self.device}{time.time_ns()}{url}{event}".encode()).hexdigest()[:20],
               "ts": _now(), "device": self.device, "job_key": job_key(url), "event": event, "url": url}
        for k in ("title", "company"):
            if job and job.get(k):
                row[k] = str(job[k])[:200]
        row.update({k: v for k, v in extra.items() if v is not None})
        self._append(LEDGER, row)
        return row

    def read_ledger(self) -> list[dict]:
        p = store.repo_dir() / LEDGER
        rows = []
        if p.exists():
            for ln in p.read_text(encoding="utf-8").splitlines():
                try:
                    rows.append(json.loads(ln))
                except ValueError:
                    continue
        return rows

    def compact(self, rows: list[dict] | None = None) -> dict[str, dict]:
        best: dict[str, dict] = {}
        for r in rows if rows is not None else self.read_ledger():
            k = r.get("job_key")
            if not k or r.get("event") not in PRIORITY:
                continue
            cur = best.get(k)
            if cur is None or (PRIORITY[r["event"]], r.get("ts", "")) >= (PRIORITY[cur["event"]], cur.get("ts", "")):
                best[k] = r
        return best

    def read_postings(self) -> dict[str, dict]:
        out = {}
        d = store.repo_dir() / "queue" / "postings"
        for f in sorted(d.glob("*.jsonl")) if d.exists() else []:
            for ln in f.read_text(encoding="utf-8").splitlines():
                try:
                    r = json.loads(ln)
                except ValueError:
                    continue
                if r.get("job_key") and r.get("url"):
                    out.setdefault(r["job_key"], r)
        return out

    # ── locks ──
    def _lock_path(self, key: str) -> str:
        return f"locks/{key}.json"

    def _lock_expired(self, blob: str) -> bool:
        try:
            d = json.loads(blob)
            at, ttl, now = _parse(d["claimed_at"]), int(d.get("ttl_sec", 0)), _clock()
            # A claim stamped more than one TTL in the future came from a device whose clock was
            # wrong (or jumped): honour it for at most ~2 TTLs instead of until that date.
            return at + ttl < now or at - now > ttl
        except (ValueError, KeyError, TypeError):
            return True

    def read_lock(self, key: str) -> dict | None:
        p = store.repo_dir() / self._lock_path(key)
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def try_lock(self, url: str, job: dict | None = None) -> dict:
        """Claim a job across devices. {"ok": bool, "reason": ..., "holder": ...}."""
        key = job_key(url)
        with self._lock:
            repo = self.ensure_repo()
            offline = False
            try:
                self._pull(repo)
            except NetworkError:
                offline = True
            done = self.compact().get(key)
            if done and done.get("event") in DONE_EVENTS:
                # Another device (or a run of ours that crashed after submitting) already
                # finished this job and released its lock. A free lock is not permission to
                # apply again: report the outcome so the local queue catches up instead. The
                # outcome is not ours to publish, so it is marked exported (no echo event).
                st = self._state()
                st.setdefault("exported", {})[key] = done["event"]
                self._save_state(st)
                return {"ok": False, "reason": "done_elsewhere", "event": done["event"],
                        "status": EVENT_TO_STATUS[done["event"]], "holder": done.get("device")}
            cur = self.read_lock(key)
            if cur and cur.get("device") != self.device and not self._lock_expired(json.dumps(cur)):
                return {"ok": False, "reason": "locked", "holder": cur.get("worker"), "until": cur.get("claimed_at")}
            stale = bool(cur and cur.get("device") != self.device)
            lock = {"job_key": key, "worker": self.label, "device": self.device, "claimed_at": _now(),
                    "ttl_sec": int(self.cfg.get("lock_ttl_s") or 7200)}
            self._write(self._lock_path(key), json.dumps(lock, indent=2) + "\n")
            self.append_event(url, "claimed", job, stale_from=(cur or {}).get("device") if stale else None)
            repo.commit_all(f"lock {key}" + (" (stale lock recovered)" if stale else ""))
            if offline:
                self._remember_lock(key)
                self._update_state(offline=True, pending=repo.ahead())
                return {"ok": True, "offline": True, "stale_recovered": stale}
            res = self._push(repo)
            mine = (self.read_lock(key) or {}).get("device") == self.device
            if not mine:
                # Our "claimed" event was union-merged into the ledger before we saw the winner's
                # lock. Retract it so the shared history shows one owner per job.
                self.append_event(url, "released", job, reason="lost_race")
                repo.commit_all(f"lost race for {key}")
                try:
                    self._push(repo)
                except (github.GitHubError, GitError):
                    pass  # queued; the next sync pushes it
                return {"ok": False, "reason": "lost_race", "holder": (self.read_lock(key) or {}).get("worker")}
            self._remember_lock(key)
            return {"ok": True, "stale_recovered": stale, **({"offline": True} if res.get("offline") else {})}

    def try_named_lock(self, name: str, ttl_s: int, meta: dict | None = None) -> dict:
        """Create-only lock ``locks/<name>.json`` that is NOT tied to a queued job.

        Used for one-runner-per-search scheduling. Kept out of ``held_locks`` so the job-lock
        cleanup in ``sync_now`` never drops it; callers release it with :meth:`release_named`.
        """
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", name or ""):
            raise ValueError("bad lock name")
        with self._lock:
            repo = self.ensure_repo()
            offline = False
            try:
                self._pull(repo)
            except NetworkError:
                offline = True
            cur = self.read_lock(name)
            if cur and cur.get("device") != self.device and not self._lock_expired(json.dumps(cur)):
                return {"ok": False, "reason": "locked", "holder": cur.get("worker"), "until": cur.get("claimed_at")}
            stale = bool(cur and cur.get("device") != self.device)
            lock = {"name": name, "worker": self.label, "device": self.device, "claimed_at": _now(),
                    "ttl_sec": int(ttl_s), **(meta or {})}
            self._write(self._lock_path(name), json.dumps(lock, indent=2) + "\n")
            repo.commit_all(f"lock {name}" + (" (stale lock recovered)" if stale else ""))
            if offline:
                self._update_state(offline=True, pending=repo.ahead())
                return {"ok": True, "offline": True, "stale_recovered": stale}
            res = self._push(repo)
            if (self.read_lock(name) or {}).get("device") != self.device:
                return {"ok": False, "reason": "lost_race", "holder": (self.read_lock(name) or {}).get("worker")}
            return {"ok": True, "stale_recovered": stale, **({"offline": True} if res.get("offline") else {})}

    def renew_named(self, name: str, ttl_s: int) -> dict:
        """Heartbeat for a named lock we hold: refresh ``claimed_at``. ``ok: False`` means another
        runner took it over after it expired (we never take it back mid-run)."""
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", name or ""):
            raise ValueError("bad lock name")
        with self._lock:
            repo = self.ensure_repo()
            offline = False
            try:
                self._pull(repo)
            except NetworkError:
                offline = True
            cur = self.read_lock(name)
            if cur and cur.get("device") != self.device:
                return {"ok": False, "reason": "taken_over", "holder": cur.get("worker")}
            lock = dict(cur or {"name": name, "worker": self.label, "device": self.device})
            lock.update(claimed_at=_now(), ttl_sec=int(ttl_s), renewed=int(lock.get("renewed") or 0) + 1)
            self._write(self._lock_path(name), json.dumps(lock, indent=2) + "\n")
            repo.commit_all(f"renew {name}")
            if offline:
                self._update_state(offline=True, pending=repo.ahead())
                return {"ok": True, "offline": True}
            try:
                res = self._push(repo)
            except (github.Offline, NetworkError):
                return {"ok": True, "offline": True}
            if (self.read_lock(name) or {}).get("device") != self.device:
                return {"ok": False, "reason": "taken_over", "holder": (self.read_lock(name) or {}).get("worker")}
            return {"ok": True, **({"offline": True} if res.get("offline") else {})}

    def release_named(self, name: str, push: bool = True) -> bool:
        with self._lock:
            repo = self.ensure_repo()
            cur = self.read_lock(name)
            if not cur or cur.get("device") != self.device:
                return False
            token_guard.safe_sync_delete(store.repo_dir(), self._lock_path(name))
            repo.commit_all(f"release {name}")
            if push:
                try:
                    self._push(repo)
                except (github.NotPrivate, github.Offline, NetworkError):
                    pass
            return True

    def _sync_schedules(self) -> dict:
        """Import runs other devices/agents recorded; export ours and the schedule list."""
        try:
            from core.searches import schedule
        except ImportError:  # pragma: no cover
            from backend.core.searches import schedule
        runs_path = store.repo_dir() / "schedules" / "runs.jsonl"
        rows = []
        if runs_path.exists():
            for ln in runs_path.read_text(encoding="utf-8").splitlines():
                try:
                    rows.append(json.loads(ln))
                except ValueError:
                    continue
        imported = schedule.record_remote_runs(rows, self.device)
        st = self._state()
        done = set(st.get("exported_runs", []))
        new = schedule.runs_to_export(self.device, done)
        for r in new:
            key = r.pop("_key")
            self._append("schedules/runs.jsonl", r)
            done.add(key)
        st["exported_runs"] = sorted(done)[-2000:]
        self._save_state(st)
        export = schedule.export_for_sync(self.device)
        path = store.repo_dir() / "schedules" / "searches.json"
        try:
            base = path.read_text(encoding="utf-8") if path.exists() else ""
        except OSError:
            base = ""
        merged = schedule.merge_exports(base, export, self.device)
        if merged["searches"] or base:
            body = json.dumps(merged, indent=2, sort_keys=True) + "\n"
            try:
                same = json.loads(base or "{}").get("searches") == merged["searches"]
            except ValueError:
                same = False
            if not same:
                self._write("schedules/searches.json", body)
        return {"imported_runs": imported, "exported_runs": len(new), "scheduled": len(export["searches"])}

    def _remember_lock(self, key: str) -> None:
        st = self._state()
        st.setdefault("held_locks", {})[key] = _now()
        self._save_state(st)

    def release(self, url_or_key: str, push: bool = True) -> bool:
        key = url_or_key if "/" not in url_or_key else job_key(url_or_key)
        with self._lock:
            repo = self.ensure_repo()
            cur = self.read_lock(key)
            st = self._state()
            st.get("held_locks", {}).pop(key, None)
            self._save_state(st)
            if not cur or cur.get("device") != self.device:
                return False
            token_guard.safe_sync_delete(store.repo_dir(), self._lock_path(key))
            repo.commit_all(f"release {key}")
            if push:
                try:
                    self._push(repo)
                except github.NotPrivate:
                    pass
            return True

    # ── credentials / sessions ──
    def _read_secret(self, rel: str) -> str | None:
        root = store.repo_dir()
        if (root / (rel + crypto.SUFFIX)).exists():
            return crypto.decrypt((root / (rel + crypto.SUFFIX)).read_text(encoding="utf-8"))
        if (root / rel).exists():
            return (root / rel).read_text(encoding="utf-8")
        return None

    def _write_secret(self, rel: str, plaintext: str, *, portal_session: bool = False) -> None:
        """Visibility was verified by the caller immediately before this write."""
        token_guard.check_sync_payload(rel, plaintext, portal_session=portal_session)
        root = store.repo_dir()
        if self.cfg.get("encrypt_credentials"):
            self._write(rel + crypto.SUFFIX, crypto.encrypt(plaintext), scan_content=False)
            token_guard.safe_sync_delete(root, rel)
        else:
            self._write(rel, plaintext, portal_session=portal_session)
            token_guard.safe_sync_delete(root, rel + crypto.SUFFIX)

    @staticmethod
    def _portals_list(text: str | None) -> list[dict]:
        if not text:
            return []
        try:
            data = json.loads(text)
        except ValueError:
            return []
        return [e for e in data if isinstance(e, dict) and e.get("id")] if isinstance(data, list) else []

    @staticmethod
    def merge_portals(*lists: list[dict]) -> list[dict]:
        """Last writer wins per id by updatedAt."""
        out: dict[str, dict] = {}
        for lst in lists:
            for e in lst:
                cur = out.get(e["id"])
                if cur is None or (e.get("updatedAt") or "") > (cur.get("updatedAt") or ""):
                    out[e["id"]] = e
        return [out[k] for k in sorted(out)]

    def _merge_portals_blobs(self, path: str, ours: str | None, theirs: str | None) -> str:
        def plain(b):
            if not b:
                return None
            return crypto.decrypt(b) if path.endswith(crypto.SUFFIX) else b
        merged = self.merge_portals(self._portals_list(plain(theirs)), self._portals_list(plain(ours)))
        text = json.dumps(merged, indent=2) + "\n"
        return crypto.encrypt(text) if path.endswith(crypto.SUFFIX) else text

    def sync_credentials(self) -> dict:
        """Merge the local vault with credentials/portals.json (both directions)."""
        if not self.credentials_allowed():
            return {"skipped": "credential sync is off or the remote is not a verified GitHub repo"}
        self.verify_private()  # raises NotPrivate/Offline: credentials stay local
        vault = CredentialVault.default()
        local = [{"id": h, "loginUrl": e.get("login_url") or f"https://{h}/", "username": e.get("username", ""),
                  "password": e["password"], "signInWith": e.get("sign_in_with", "password"),
                  "notes": e.get("notes", ""), "updatedAt": e.get("updated_at") or e.get("created_at") or "",
                  "updatedBy": self.device if e.get("source") != "sync" else e.get("updated_by", "")}
                 for h, e in vault.all_entries().items() if e.get("password")]
        remote = self._portals_list(self._read_secret(PORTALS))
        merged = self.merge_portals(remote, local)
        pulled = vault.upsert_synced({e["id"]: {"username": e.get("username", ""), "password": e.get("password", ""),
                                                "updated_at": e.get("updatedAt"), "login_url": e.get("loginUrl"),
                                                "sign_in_with": e.get("signInWith")} for e in merged})
        if merged != remote:
            self._write_secret(PORTALS, json.dumps(merged, indent=2) + "\n")
        return {"portals": len(merged), "pulled": pulled}

    def save_session(self, profile_id: str, storage_state: dict) -> dict:
        profile_id = re.sub(r"[^A-Za-z0-9_.-]", "-", profile_id or "default")[:60]
        clean = sanitize_storage_state(storage_state or {})
        clean["savedAt"] = _now()
        clean["savedBy"] = self.device
        text = json.dumps(clean, indent=1) + "\n"
        token_guard.check_sync_payload("storage.json", text, portal_session=True)
        local = store.vault_dir() / "sessions" / f"{profile_id}.json"
        store.write_private(local, clean)
        if not (self.credentials_allowed() and self.cfg.get("sync_sessions")):
            return {"saved": "local", "cookies": len(clean["cookies"])}
        if len(text.encode("utf-8")) > SESSION_SYNC_MAX_BYTES:
            # Every save is a new blob in git history forever, and GitHub rejects files over
            # 100 MB. A session this big is almost all site localStorage; keep it on this device.
            return {"saved": "local", "too_large": True, "bytes": len(text.encode("utf-8")),
                    "cookies": len(clean["cookies"])}
        with self._lock:
            repo = self.ensure_repo()
            try:
                self.verify_private()
            except github.Offline:
                return {"saved": "local", "pending": True, "cookies": len(clean["cookies"])}
            self._write_secret(f"sessions/{profile_id}/storage.json", text, portal_session=True)
            repo.commit_all(f"session {profile_id}")
            res = self._push(repo)
        return {"saved": "repo", "cookies": len(clean["cookies"]), **res}

    @staticmethod
    def _session_ts(blob: str) -> str:
        try:
            return json.loads(blob).get("savedAt", "")
        except (ValueError, AttributeError):
            return ""

    def load_session(self, profile_id: str) -> dict | None:
        profile_id = re.sub(r"[^A-Za-z0-9_.-]", "-", profile_id or "default")[:60]
        cands = []
        try:
            cands.append(json.loads((store.vault_dir() / "sessions" / f"{profile_id}.json").read_text(encoding="utf-8")))
        except (OSError, ValueError):
            pass
        try:
            blob = self._read_secret(f"sessions/{profile_id}/storage.json")
            if blob:
                cands.append(json.loads(blob))
        except (ValueError, crypto.KeyMissing):
            pass
        return max(cands, key=lambda d: d.get("savedAt", ""), default=None)

    # ── jobs <-> repo ──
    def _export_jobs(self) -> int:
        st = self._state()
        exported = st.setdefault("exported", {})
        n = 0
        month = datetime.now(timezone.utc).strftime("%Y-%m")
        for url, job in jobs_store.read_jobs().items():
            if not isinstance(job, dict):
                continue
            key = job_key(url)
            ev = STATUS_TO_EVENT.get(js.normalize_status(job.get("status")))
            if key not in exported:
                post = {"job_key": key, "url": url, "added_at": job.get("added_at") or _now(), "device": self.device}
                post.update({f: str(job[f])[:200] for f in POSTING_FIELDS if job.get(f)})
                self._append(f"queue/postings/{month}.jsonl", post)
            if ev and exported.get(key) != ev and ev != "claimed":
                evidence = None
                if ev == "confirmed":
                    evidence = {k: job[f"email_{k}"] for k in ("from_domain", "subject") if job.get(f"email_{k}")} or None
                self.append_event(url, ev, job, evidence=evidence)
                n += 1
            exported[key] = ev
        self._save_state(st)
        return n

    def _import_jobs(self) -> dict:
        compacted = self.compact()
        postings = self.read_postings()
        local = jobs_store.read_jobs()
        local_keys = {job_key(u): u for u in local}
        updates, added = {}, {}
        auto_ids = _auto_search_ids()
        for key, post in postings.items():
            if key not in local_keys:
                ev = (compacted.get(key) or {}).get("event")
                added[post["url"]] = {**{f: post[f] for f in POSTING_FIELDS if post.get(f)}, "url": post["url"],
                                      "status": EVENT_TO_STATUS.get(ev, js.PENDING), "added_at": post.get("added_at"),
                                      "synced_from": post.get("device")}
                # Postings an agent found for a saved search: review-first unless that search is "auto".
                if post.get("search_id") and post["search_id"] not in auto_ids \
                        and added[post["url"]]["status"] == js.PENDING:
                    added[post["url"]]["review"] = "pending"
        for key, row in compacted.items():
            url = local_keys.get(key)
            target = EVENT_TO_STATUS.get(row["event"])
            if not url or not target or row.get("device") == self.device:
                continue
            cur = js.normalize_status(local[url].get("status"))
            if _STATUS_RANK.get(target, 0) > _STATUS_RANK.get(cur, 0) and cur != js.IN_PROGRESS:
                updates[url] = {"status": target, "synced_from": row.get("device"), "synced_at": _now()}
        n = jobs_store.merge_from_sync(added, updates)
        st = self._state()
        exported = st.setdefault("exported", {})
        for url, f in {**added, **updates}.items():
            exported[job_key(url)] = STATUS_TO_EVENT.get(f.get("status", js.PENDING))
        self._save_state(st)
        return {"added": len(added), "updated": len(updates), "changed": n}

    def _release_finished(self, repo: GitRepo) -> int:
        held = dict(self._state().get("held_locks", {}))
        if not held:
            return 0
        local = {job_key(u): j for u, j in jobs_store.read_jobs().items()}
        n = 0
        for key in held:
            job = local.get(key)
            if job is None or js.normalize_status(job.get("status")) != js.IN_PROGRESS:
                cur = self.read_lock(key)
                if cur and cur.get("device") == self.device:
                    token_guard.safe_sync_delete(store.repo_dir(), self._lock_path(key))
                    n += 1
                st = self._state()
                st.get("held_locks", {}).pop(key, None)
                self._save_state(st)
        return n

    def sync_now(self) -> dict:
        """Pull, merge jobs both ways, sync credentials, heartbeat, push."""
        out: dict = {"ok": True}
        with self._lock:
            repo = self.ensure_repo()
            try:
                lost = self._pull(repo)
                out["lost_locks"] = lost
                out["offline"] = False
            except NetworkError:
                out["offline"] = True
            self._ensure_layout()
            out["imported"] = self._import_jobs()
            out["exported"] = self._export_jobs()
            out["released"] = self._release_finished(repo)
            try:
                out["schedules"] = self._sync_schedules()
            except Exception as e:  # schedules must never break job/credential sync
                log.warning("schedule sync failed: %s", type(e).__name__)
            if self.credentials_allowed():
                try:
                    out["credentials"] = self.sync_credentials()
                except github.NotPrivate as e:
                    out["credentials"] = {"refused": str(e)}
                    out["ok"] = False
                except github.Offline:
                    out["credentials"] = {"pending": "offline; credentials stay local until GitHub is reachable"}
                except crypto.KeyMissing as e:
                    out["credentials"] = {"error": str(e)}
            self._write(f"workers/{self.device}.json", json.dumps(
                {"device": self.device, "label": self.label, "last_seen": _now()}, indent=2) + "\n")
            repo.commit_all(f"sync from {self.label}")
            try:
                out.update(self._push(repo))
            except github.NotPrivate as e:
                out.update(ok=False, refused=str(e), pending=repo.ahead())
            self._update_state(last_sync=_now(), last_result={k: v for k, v in out.items() if k != "credentials"})
        return out

    def status(self) -> dict:
        st = self._state()
        repo = self.repo()
        info = {"configured": bool(self.cfg.get("enabled")), "kind": self.cfg.get("kind"),
                "owner": self.cfg.get("owner"), "repo": self.cfg.get("repo"), "device": self.device,
                "device_label": self.label, "git": bool(git_available()), "cloned": repo.exists(),
                "github_linked": bool(store.load_token().get("access_token")),
                "encrypt_credentials": bool(self.cfg.get("encrypt_credentials")),
                "sync_credentials": bool(self.cfg.get("sync_credentials")),
                "has_key": crypto.load_key() is not None,
                "last_sync": st.get("last_sync"), "offline": bool(st.get("offline")),
                "pending": 0, "visibility_error": st.get("visibility_error"),
                "verified_private_at": st.get("verified_private_at"),
                "held_locks": sorted(st.get("held_locks", {}))}
        try:
            if repo.exists():
                info["pending"] = repo.ahead()
                wd = store.repo_dir() / "workers"
                info["devices"] = sorted(p.stem for p in wd.glob("*.json")) if wd.exists() else []
        except GitError:
            pass
        return info
