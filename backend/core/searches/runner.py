"""Runs due scheduled searches: the in-app scheduler and the headless ``--run-schedules-once``.

One collection runs at a time per device, so a tick starts at most one search; the others stay
due and start on later ticks (each still runs once, never a burst).
"""
from __future__ import annotations

import logging
import re
import threading
import time
from typing import Callable

from . import schedule

log = logging.getLogger(__name__)

_ERRORISH = re.compile(r"error|fail|❌|denied|\b4\d\d\b|rate.?limit|too many|expired|sign.?in|log.?in", re.I)


class SyncLock:
    """One runner per search across devices via the M5 sync repo (``locks/search-<id>.json``)."""

    def __init__(self, engine_factory: Callable | None = None, enabled: Callable[[], bool] | None = None):
        self._factory = engine_factory
        self._enabled = enabled

    def _engine(self):
        if self._factory:
            return self._factory()
        try:
            from core.sync.engine import SyncEngine
        except ImportError:  # pragma: no cover
            from backend.core.sync.engine import SyncEngine
        return SyncEngine()

    def _on(self) -> bool:
        if self._enabled:
            return self._enabled()
        try:
            from core.sync import store
        except ImportError:  # pragma: no cover
            from backend.core.sync import store
        return bool(store.load_config().get("enabled"))

    def acquire(self, sid: str) -> dict:
        if not self._on():
            return {"ok": True, "local_only": True}
        try:
            return self._engine().try_named_lock(f"search-{sid}", schedule.RUN_TTL_S, {"search_id": sid})
        except Exception as e:  # git missing / repo refused: run locally; job locks still stop double applies
            log.warning("schedule lock unavailable (%s); running locally", type(e).__name__)
            return {"ok": True, "unsynced": True}

    def renew(self, sid: str) -> dict:
        """Heartbeat: push a fresh claimed_at so a long run is not taken over."""
        if not self._on():
            return {"ok": True, "local_only": True}
        try:
            return self._engine().renew_named(f"search-{sid}", schedule.RUN_TTL_S)
        except Exception as e:
            log.warning("schedule lock renew failed (%s)", type(e).__name__)
            return {"ok": True, "unsynced": True}

    def release(self, sid: str) -> None:
        if not self._on():
            return
        try:
            self._engine().release_named(f"search-{sid}")
        except Exception as e:
            log.warning("schedule lock release failed (%s)", type(e).__name__)


def outcome_from(errors: list[str], log_lines: list[str], cancelled: bool = False) -> str:
    texts = list(errors or []) + [ln for ln in (log_lines or []) if isinstance(ln, str) and _ERRORISH.search(ln)]
    kind = schedule.classify(texts)
    if kind == "ok" and cancelled:
        return "cancelled"
    if kind == "error" and not errors:
        return "ok"   # noisy log lines alone are not a failed run
    return kind


def tick(start_run: Callable[[str, Callable], bool], is_busy: Callable[[], bool], *,
         device: str = "", lock: SyncLock | None = None, now: float | None = None) -> list[dict]:
    """Start the first due search. ``start_run(sid, on_done)`` returns False if it could not start;
    ``on_done(errors, log_lines, cancelled)`` must be called once when that run ends."""
    lock = lock or SyncLock()
    results: list[dict] = []
    for sid in schedule.due(now=now):
        if is_busy():
            results.append({"id": sid, "action": "waiting_busy"})
            break
        got = lock.acquire(sid)
        if not got.get("ok"):
            schedule.skip(sid, got.get("holder"), now=now)
            results.append({"id": sid, "action": "skipped_other_device", "holder": got.get("holder")})
            continue
        if not schedule.begin(sid, device, now=now):
            lock.release(sid)
            results.append({"id": sid, "action": "already_running"})
            continue

        stop = threading.Event()

        def on_done(errors, log_lines=None, cancelled=False, _sid=sid, _stop=stop):
            _stop.set()
            try:
                res = schedule.finish(_sid, outcome_from(errors, log_lines or [], cancelled),
                                      list(errors or []) + list(log_lines or [])[-50:], device=device)
                if res.get("paused_reason") == "login_expired":
                    log.warning("Scheduled search %s paused: sign-in expired", _sid)
            finally:
                lock.release(_sid)

        try:
            started = start_run(sid, on_done)
        except Exception as e:
            on_done([str(e)])
            results.append({"id": sid, "action": "failed_to_start"})
            break
        if not started:
            schedule.defer(sid, now=now)
            lock.release(sid)
            results.append({"id": sid, "action": "waiting_busy"})
        else:
            _start_heartbeat(sid, device, lock, stop)
            results.append({"id": sid, "action": "started"})
        break
    return results


def beat_once(sid: str, device: str, lock: SyncLock) -> dict:
    """One heartbeat: renew the local running mark and the sync-repo lock."""
    schedule.heartbeat(sid, device)
    got = lock.renew(sid)
    if not got.get("ok"):
        # Our lock expired and another runner took it (e.g. this laptop slept). Job claim locks still
        # stop double applies; record it so the run history shows the overlap.
        log.warning("Scheduled search %s: lock taken over by %s", sid, got.get("holder"))
    return got


def _start_heartbeat(sid: str, device: str, lock: SyncLock, stop: threading.Event,
                     every_s: float | None = None) -> threading.Thread:
    every = schedule.HEARTBEAT_S if every_s is None else every_s

    def loop():
        while not stop.wait(every):
            try:
                beat_once(sid, device, lock)
            except Exception as e:  # never kill a run over a heartbeat
                log.warning("heartbeat failed (%s)", type(e).__name__)

    t = threading.Thread(target=loop, name=f"schedule-heartbeat-{sid}", daemon=True)
    t.start()
    return t


def wait_idle(is_busy: Callable[[], bool], timeout_s: float = schedule.MAX_RUN_S, poll_s: float = 2.0) -> bool:
    end = time.time() + timeout_s
    while is_busy():
        if time.time() > end:
            return False
        time.sleep(poll_s)
    return True
