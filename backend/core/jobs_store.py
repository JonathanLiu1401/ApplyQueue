"""Lightweight jobs.json access for processes that must not import the browser stack.

The MCP server child process and the email sweep only need to read and update
job rows. ``core.shared_config`` pulls in boto3 and browser-use at import time,
so this module re-implements the few operations they need with the same file
and lock. When ``core.shared_config`` is already loaded (the sidecar), its
paths are used so both always point at the same file.
"""
from __future__ import annotations

import json
import os
import socket
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path

from filelock import FileLock

try:
    from core import job_status as js
    from core.config import get_data_dir, read_json_file
except ImportError:  # pragma: no cover
    from backend.core import job_status as js
    from backend.core.config import get_data_dir, read_json_file

_claim_lock = threading.Lock()

# Evidence keys a confirmation email may store on a job (never the body).
EMAIL_EVIDENCE_KEYS = ("message_id", "from_domain", "subject", "received_at", "provider")


def _paths() -> tuple[Path, Path]:
    sc = sys.modules.get("core.shared_config") or sys.modules.get("backend.core.shared_config")
    if sc is not None and hasattr(sc, "JOBS_FILE"):
        return Path(sc.JOBS_FILE), Path(sc.JOBS_LOCK)
    d = get_data_dir()
    return d / "jobs.json", d / "jobs.json.lock"


def _load(path: Path) -> dict:
    return read_json_file(path, {})


def _save(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    tmp.replace(path)


def read_jobs() -> dict:
    jobs_file, lock = _paths()
    with FileLock(str(lock)):
        return _load(jobs_file)


def get_job(url: str) -> dict | None:
    return read_jobs().get(url)


def update_job(url: str, **fields) -> bool:
    jobs_file, lock = _paths()
    with FileLock(str(lock)):
        jobs = _load(jobs_file)
        if url not in jobs:
            return False
        jobs[url].update(fields)
        _save(jobs_file, jobs)
        return True


REMOTE_BACKOFF_S = 15 * 60
# An in_progress claim older than this is stale even when its pid looks alive (pid reuse).
STALE_CLAIM_S = 6 * 3600


def _stamp_claim(job: dict) -> None:
    job["claimed_at"] = datetime.now(timezone.utc).isoformat()
    job["claimed_pid"] = os.getpid()
    job["claimed_host"] = socket.gethostname()
    job.pop("submit_clicks", None)
    job.pop("last_submit_click_at", None)


def mark_submit_click(url: str) -> bool:
    """Record, BEFORE the click, that a Next/Submit button is about to be pressed for this job.
    If the process dies right after, crash recovery knows the application may already be in
    and flags it for a person instead of re-applying (no duplicate submissions)."""
    if not url:
        return False
    jobs_file, lock = _paths()
    with FileLock(str(lock)):
        jobs = _load(jobs_file)
        job = jobs.get(url)
        if not isinstance(job, dict):
            return False
        job["submit_clicks"] = int(job.get("submit_clicks") or 0) + 1
        job["last_submit_click_at"] = datetime.now(timezone.utc).isoformat()
        _save(jobs_file, jobs)
        return True


def _pid_alive(pid) -> bool:
    try:
        pid = int(pid or 0)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    try:
        import psutil
    except ImportError:  # psutil is a transitive dependency; never treat live claims as dead without it
        if os.name == "nt":
            return True  # cannot tell: err on the side of not stealing (stale window still applies)
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True
    try:
        return psutil.pid_exists(pid)
    except Exception:
        return True


def claim_is_live(job: dict, now: datetime | None = None) -> bool:
    """True while the process that claimed this job (on this machine) is still running."""
    if job.get("claimed_host") != socket.gethostname() or not _pid_alive(job.get("claimed_pid")):
        return False
    if job.get("claimed_pid") == os.getpid():
        return False  # our own pid after a restart is a coincidence, never a live worker
    try:
        at = datetime.fromisoformat(str(job.get("claimed_at")))
    except ValueError:
        return False
    if at.tzinfo is None:
        at = at.replace(tzinfo=timezone.utc)
    return ((now or datetime.now(timezone.utc)) - at).total_seconds() < STALE_CLAIM_S


INTERRUPTED_AFTER_SUBMIT = ("The app stopped right after pressing Next/Submit on this application, so it may "
                            "already have gone in. Check the employer's site or your email before retrying.")


def recover_stale_claims(jobs: dict) -> tuple[int, int]:
    """Crash recovery for in-memory ``jobs``: dead claims go back to pending, unless a submit click
    was recorded (then needs_human, never a silent re-apply). Live claims of another running
    instance are left alone. Returns (requeued, flagged)."""
    requeued = flagged = 0
    for job in jobs.values():
        if not isinstance(job, dict) or job.get("status") != js.IN_PROGRESS or claim_is_live(job):
            continue
        if int(job.get("submit_clicks") or 0) > 0:
            job["status"] = js.NEEDS_HUMAN
            job["error"] = INTERRUPTED_AFTER_SUBMIT
            flagged += 1
        else:
            job["status"] = js.PENDING
            requeued += 1
    return requeued, flagged


def remote_claim(url: str, job: dict | None) -> bool:
    """Cross-device claim through the sync repo's lock files (True when sync is off/offline)."""
    try:
        try:
            from core.sync import claims
        except ImportError:  # pragma: no cover
            from backend.core.sync import claims
        return claims.remote_claim(url, job)
    except Exception:  # never let sync problems stop a single-device run
        return True


def _revert_remote_claim(url: str) -> None:
    until = datetime.fromtimestamp(datetime.now(timezone.utc).timestamp() + REMOTE_BACKOFF_S, timezone.utc)
    update_job(url, status=js.PENDING, claimed_at=None, remote_claimed_until=until.isoformat())


def _remote_backoff(job: dict) -> bool:
    until = job.get("remote_claimed_until")
    if not until:
        return False
    try:
        return datetime.fromisoformat(until) > datetime.now(timezone.utc)
    except ValueError:
        return False


def claim_job(url: str) -> bool:
    """pending -> in_progress, atomically. False when someone else (or another device) has it."""
    jobs_file, lock = _paths()
    with _claim_lock, FileLock(str(lock)):
        jobs = _load(jobs_file)
        if not (url in jobs and jobs[url].get("status") == js.PENDING):
            return False
        jobs[url]["status"] = js.IN_PROGRESS
        _stamp_claim(jobs[url])
        _save(jobs_file, jobs)
        job = dict(jobs[url])
    if not remote_claim(url, job):
        _revert_remote_claim(url)
        return False
    return True


def claim_next_pending() -> dict | None:
    """Claim the oldest pending job (used by hand-off mode, e.g. a Grok Bot run)."""
    jobs_file, lock = _paths()
    for _ in range(25):
        with _claim_lock, FileLock(str(lock)):
            jobs = _load(jobs_file)
            picked = None
            for url, job in jobs.items():
                if (isinstance(job, dict) and job.get("status") == js.PENDING and job.get("review") != "pending"
                        and not _remote_backoff(job)):
                    job["status"] = js.IN_PROGRESS
                    _stamp_claim(job)
                    _save(jobs_file, jobs)
                    picked = (url, {**job, "url": job.get("url") or url})
                    break
        if picked is None:
            return None
        if remote_claim(*picked):
            return picked[1]
        _revert_remote_claim(picked[0])
    return None


def merge_from_sync(added: dict[str, dict], updates: dict[str, dict]) -> int:
    """Apply jobs/status changes another device recorded in the sync repo. Never downgrades."""
    if not added and not updates:
        return 0
    jobs_file, lock = _paths()
    n = 0
    with FileLock(str(lock)):
        jobs = _load(jobs_file)
        for url, job in added.items():
            if url not in jobs:
                jobs[url] = job
                n += 1
        for url, fields in updates.items():
            if url in jobs and jobs[url].get("status") != js.IN_PROGRESS:
                jobs[url].update(fields)
                n += 1
        if n:
            _save(jobs_file, jobs)
    return n


def confirm_by_email(url: str, evidence: dict) -> tuple[bool, str]:
    """submitted_unconfirmed -> confirmed. Shared by /jobs/confirm-email and the email sweep.

    Returns (ok, code): code is "confirmed", "not_found", "not_submitted" or "missing_evidence".
    Only identifying evidence is stored, never the email body.
    """
    allowed = {k: str(v)[:300] for k, v in (evidence or {}).items() if k in EMAIL_EVIDENCE_KEYS and v}
    if not allowed:
        return False, "missing_evidence"
    jobs_file, lock = _paths()
    with FileLock(str(lock)):
        jobs = _load(jobs_file)
        job = jobs.get(url)
        if not job:
            return False, "not_found"
        if not js.can_confirm_by_email(job):
            return False, "not_submitted"
        job.update(js.confirmation_fields(allowed))
        _save(jobs_file, jobs)
    return True, "confirmed"
