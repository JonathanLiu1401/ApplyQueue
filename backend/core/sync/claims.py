"""Cross-device claim hook used by core.jobs_store (lazy import, never raises)."""
from __future__ import annotations

import logging

from . import store

log = logging.getLogger(__name__)


def enabled() -> bool:
    try:
        return bool(store.load_config().get("enabled"))
    except Exception:
        return False


def remote_claim_result(url: str, job: dict | None) -> dict:
    """{"ok": bool, ...}; a refusal may carry "status" (the job was finished on another device)."""
    if not enabled():
        return {"ok": True}
    try:
        from .engine import SyncEngine
        res = SyncEngine().try_lock(url, job)
    except Exception as e:  # offline, git missing, repo refused: keep single-device behaviour
        log.warning("sync lock skipped for %s: %s", url, type(e).__name__)
        return {"ok": True, "skipped": type(e).__name__}
    if not res.get("ok"):
        log.info("job %s is not ours: %s (%s)", url, res.get("reason"), res.get("holder"))
    return res


def remote_claim(url: str, job: dict | None) -> bool:
    return bool(remote_claim_result(url, job).get("ok"))
