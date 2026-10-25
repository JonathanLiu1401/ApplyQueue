"""Lightweight jobs.json access for processes that must not import the browser stack.

The MCP server child process and the email sweep only need to read and update
job rows. ``core.shared_config`` pulls in boto3 and browser-use at import time,
so this module re-implements the few operations they need with the same file
and lock. When ``core.shared_config`` is already loaded (the sidecar), its
paths are used so both always point at the same file.
"""
from __future__ import annotations

import json
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path

from filelock import FileLock

try:
    from core import job_status as js
    from core.config import get_data_dir
except ImportError:  # pragma: no cover
    from backend.core import job_status as js
    from backend.core.config import get_data_dir

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
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


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
        jobs[url]["claimed_at"] = datetime.now(timezone.utc).isoformat()
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
                if isinstance(job, dict) and job.get("status") == js.PENDING and not _remote_backoff(job):
                    job["status"] = js.IN_PROGRESS
                    job["claimed_at"] = datetime.now(timezone.utc).isoformat()
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
