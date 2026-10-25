"""Job application statuses (fix for upstream LangHire #89).

Upstream marked a job "applied" whenever the browser agent *said* it succeeded
(``AgentHistoryList.is_successful()``). ApplyQueue never trusts self-report:

- ``submitted_unconfirmed`` - our own code saw a submission confirmation on the
  page (text/URL), but no confirmation email has been matched yet.
- ``confirmed`` - page confirmation AND a matching confirmation email
  (email linking arrives in M4; until then nothing reaches this state
  automatically, only via ``confirm_by_email`` or a manual status change).
- ``needs_human`` - the agent could not answer a field, or claimed success
  without any page evidence; a person must check before anything is retried.

Legacy ``applied`` rows (written by LangHire) are migrated to
``submitted_unconfirmed`` with ``legacy_status: "applied"`` because they were
never independently verified.
"""
from __future__ import annotations

from datetime import datetime, timezone

PENDING = "pending"
IN_PROGRESS = "in_progress"
SUBMITTED_UNCONFIRMED = "submitted_unconfirmed"
CONFIRMED = "confirmed"
NEEDS_HUMAN = "needs_human"
FAILED = "failed"
BLOCKED = "blocked"
LEGACY_APPLIED = "applied"  # read-only: never written by ApplyQueue

SUBMITTED_STATUSES: tuple[str, ...] = (SUBMITTED_UNCONFIRMED, CONFIRMED)
ALL_STATUSES: tuple[str, ...] = (
    PENDING, IN_PROGRESS, SUBMITTED_UNCONFIRMED, CONFIRMED, NEEDS_HUMAN, FAILED, BLOCKED,
)
# Statuses a user may set by hand from the UI / API.
MANUAL_STATUSES: tuple[str, ...] = (
    PENDING, SUBMITTED_UNCONFIRMED, CONFIRMED, NEEDS_HUMAN, FAILED, BLOCKED,
)
# Statuses the apply runner may pick up again.
RETRYABLE_STATUSES: tuple[str, ...] = (PENDING, FAILED)


def normalize_status(status: str | None) -> str:
    """Map legacy values onto the current vocabulary (``applied`` -> submitted_unconfirmed)."""
    if status == LEGACY_APPLIED:
        return SUBMITTED_UNCONFIRMED
    return status or PENDING


def is_submitted(status: str | None) -> bool:
    return normalize_status(status) in SUBMITTED_STATUSES


def migrate_job(job: dict) -> bool:
    """Migrate one job dict in place. Returns True when it changed."""
    if job.get("status") == LEGACY_APPLIED:
        job["status"] = SUBMITTED_UNCONFIRMED
        job["legacy_status"] = LEGACY_APPLIED
        if job.get("applied_at") and not job.get("submitted_at"):
            job["submitted_at"] = job["applied_at"]
        return True
    return False


def migrate_jobs(jobs: dict) -> int:
    """Migrate every job in a jobs.json mapping in place. Returns how many changed."""
    return sum(1 for job in jobs.values() if isinstance(job, dict) and migrate_job(job))


def confirmation_fields(email_evidence: dict) -> dict:
    """Fields for submitted_unconfirmed -> confirmed (the M4 email-linking hook)."""
    return {
        "status": CONFIRMED,
        "confirmed_at": datetime.now(timezone.utc).isoformat(),
        "email_confirmation": email_evidence,
    }


def can_confirm_by_email(job: dict) -> bool:
    """Only a page-confirmed submission can be upgraded by a confirmation email."""
    return normalize_status(job.get("status")) == SUBMITTED_UNCONFIRMED and bool(
        job.get("page_confirmation") or job.get("legacy_status") == LEGACY_APPLIED
    )
