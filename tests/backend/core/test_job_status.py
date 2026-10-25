"""#89: statuses never trust agent self-report; legacy 'applied' rows are migrated."""
from core import job_status as js


def test_legacy_applied_is_not_a_writable_status():
    assert js.LEGACY_APPLIED not in js.ALL_STATUSES
    assert js.LEGACY_APPLIED not in js.MANUAL_STATUSES
    assert js.IN_PROGRESS not in js.MANUAL_STATUSES


def test_normalize_and_is_submitted():
    assert js.normalize_status("applied") == js.SUBMITTED_UNCONFIRMED
    assert js.normalize_status(None) == js.PENDING
    assert js.is_submitted("confirmed") and js.is_submitted("applied")
    assert not js.is_submitted("needs_human") and not js.is_submitted("failed")


def test_migrate_jobs_keeps_legacy_marker_and_dates():
    jobs = {
        "u1": {"status": "applied", "applied_at": "2026-01-02T00:00:00Z"},
        "u2": {"status": "pending"},
        "u3": "not a dict",
    }
    assert js.migrate_jobs(jobs) == 1
    assert jobs["u1"]["status"] == js.SUBMITTED_UNCONFIRMED
    assert jobs["u1"]["legacy_status"] == "applied"
    assert jobs["u1"]["submitted_at"] == "2026-01-02T00:00:00Z"
    assert js.migrate_jobs(jobs) == 0  # idempotent


def test_email_confirmation_requires_page_evidence():
    assert not js.can_confirm_by_email({"status": "submitted_unconfirmed"})
    assert js.can_confirm_by_email({"status": "submitted_unconfirmed", "page_confirmation": {"confirmed": True}})
    assert js.can_confirm_by_email({"status": "submitted_unconfirmed", "legacy_status": "applied"})
    assert not js.can_confirm_by_email({"status": "pending", "page_confirmation": {"confirmed": True}})
    fields = js.confirmation_fields({"message_id": "m"})
    assert fields["status"] == js.CONFIRMED and fields["confirmed_at"]
