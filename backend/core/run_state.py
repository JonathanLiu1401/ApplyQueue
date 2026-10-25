"""Per-run state shared by the CLI runner and the ApplyQueue MCP server.

A CLI backend run (M3) is two processes: the sidecar's runner starts the
vendor CLI, and the CLI starts ``applyqueue-backend --mcp-server --run-file X``.
They talk through this JSON file (guarded by a file lock): which jobs the run
may work on, what the MCP tools observed (confirmations, unanswered fields,
needs-human reports, failed form POSTs), and how each job finished.

The final status is ALWAYS decided here by :func:`finalize_job` from our own
observations (``decide_outcome``), never from what the CLI agent claims (#89).
"""
from __future__ import annotations

import json
import secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from filelock import FileLock

try:
    from core import job_status as js
    from core import jobs_store
    from core.outcome import PageEvidence, decide_outcome
except ImportError:  # pragma: no cover
    from backend.core import job_status as js
    from backend.core import jobs_store
    from backend.core.outcome import PageEvidence, decide_outcome


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_ledger() -> dict:
    return {
        "needs_human": [],
        "confirmations": [],
        "unanswered_last": [],
        "blocked_submits": 0,
        "submit_clicks": 0,
        "last_submit_at": None,
        "network_errors": [],
        "otp_message_ids": [],
        "tool_calls": 0,
    }


class RunState:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._lock = FileLock(str(self.path) + ".lock")

    # ── creation / io ────────────────────────────────────────────────────
    @classmethod
    def create(cls, run_dir: Path, *, backend: str, job_urls: list[str], handoff: bool = False,
               options: dict | None = None) -> "RunState":
        run_dir = Path(run_dir)
        run_dir.mkdir(parents=True, exist_ok=True)
        state = cls(run_dir / "run.json")
        data = {
            "run_id": run_dir.name or secrets.token_hex(6),
            "backend": backend,
            "created_at": _now(),
            "handoff": bool(handoff),
            "queue": list(job_urls),
            "current": None,
            "results": {},
            "ledgers": {},
            "options": options or {},
        }
        with state._lock:
            state._write(data)
        return state

    def _read(self) -> dict:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _write(self, data: dict) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        tmp.replace(self.path)

    def read(self) -> dict:
        with self._lock:
            return self._read()

    def mutate(self, fn: Callable[[dict], object]):
        with self._lock:
            data = self._read()
            out = fn(data)
            self._write(data)
            return out

    # ── helpers used by the MCP tools ────────────────────────────────────
    @property
    def options(self) -> dict:
        return self.read().get("options", {})

    def current(self) -> str | None:
        return self.read().get("current")

    def ledger(self, url: str | None = None) -> dict:
        data = self.read()
        url = url or data.get("current")
        return (data.get("ledgers") or {}).get(url or "", new_ledger())

    def update_ledger(self, fn: Callable[[dict], object], url: str | None = None):
        def _m(data):
            key = url or data.get("current")
            if not key:
                return None
            led = data.setdefault("ledgers", {}).setdefault(key, new_ledger())
            return fn(led)
        return self.mutate(_m)

    def next_job(self) -> dict | None:
        """The job to work on now: the unfinished current one, else the next queued one."""
        def _m(data):
            cur = data.get("current")
            if cur and cur not in data.get("results", {}):
                return cur
            queue = data.get("queue") or []
            while queue:
                url = queue.pop(0)
                if url not in data.get("results", {}):
                    data["current"] = url
                    data.setdefault("ledgers", {}).setdefault(url, new_ledger())
                    return url
            data["current"] = None
            return None
        url = self.mutate(_m)
        if url:
            return jobs_store.get_job(url) or {"url": url}
        if self.read().get("handoff"):
            job = jobs_store.claim_next_pending()
            if job:
                def _set(data):
                    data["current"] = job["url"]
                    data.setdefault("ledgers", {}).setdefault(job["url"], new_ledger())
                self.mutate(_set)
                return job
        return None

    def result_for(self, url: str) -> dict | None:
        return (self.read().get("results") or {}).get(url)


def finalize_job(state: RunState, url: str, *, agent_claimed_success: bool, agent_summary: str = "",
                 failure_reason: str | None = None, source: str = "finish_job") -> dict:
    """Decide and persist a job's status from ApplyQueue's own evidence.

    ``agent_claimed_success`` only ever downgrades to needs_human when no page
    confirmation exists; a failed form POST after the last submit click vetoes
    a page match (the ATS rejected the submission).
    """
    existing = state.result_for(url)
    if existing:
        return existing
    led = state.ledger(url)
    best = None
    for c in reversed(led.get("confirmations") or []):
        if c.get("confirmed"):
            best = PageEvidence(confirmed=True, matched=c.get("matched", ""), url=c.get("url", ""),
                                source=c.get("source", "text"))
            break
    last_submit = led.get("last_submit_at")
    post_errors = [e for e in led.get("network_errors") or []
                   if e.get("method") in ("POST", "PUT") and (not last_submit or e.get("at", "") >= last_submit)]
    if best and post_errors:
        best = None
        failure_reason = failure_reason or (
            f"The site rejected the submission (HTTP {post_errors[-1].get('status')}) even though the page changed")
    outcome = decide_outcome(page=best, agent_claimed_success=agent_claimed_success,
                             needs_human=led.get("needs_human"), unanswered=led.get("unanswered_last"))
    status = outcome.status
    reason = outcome.reason
    if status == js.FAILED and failure_reason:
        reason = failure_reason
    fields = {
        "status": status,
        "error": None if status in js.SUBMITTED_STATUSES else reason[:2000],
        "page_confirmation": outcome.evidence.get("page_confirmation") if status in js.SUBMITTED_STATUSES else None,
        "needs_human_fields": led.get("needs_human") or [],
        "agent_claimed_success": bool(agent_claimed_success),
        "outcome_reason": reason[:2000],
        "agent_backend": state.read().get("backend"),
    }
    if status in js.SUBMITTED_STATUSES:
        fields["submitted_at"] = _now()
    if agent_summary:
        fields["agent_summary"] = agent_summary[:1000]
    jobs_store.update_job(url, **fields)
    result = {"status": status, "reason": reason, "finished_at": _now(), "source": source}

    def _m(data):
        data.setdefault("results", {})[url] = result
        if data.get("current") == url:
            data["current"] = None
    state.mutate(_m)
    return result


def skip_job(state: RunState, url: str, reason: str) -> dict:
    """The agent decided not to apply (video funnel, not a real form, wrong job...).

    Recorded as ``blocked`` with the reason so it is not retried automatically.
    """
    reason = (reason or "Skipped by the agent").strip()[:500]
    jobs_store.update_job(url, status=js.BLOCKED, error=f"Skipped: {reason}", skip_reason=reason,
                          agent_backend=state.read().get("backend"))
    result = {"status": js.BLOCKED, "reason": reason, "finished_at": _now(), "source": "skip_job"}

    def _m(data):
        data.setdefault("results", {})[url] = result
        if data.get("current") == url:
            data["current"] = None
    state.mutate(_m)
    return result
