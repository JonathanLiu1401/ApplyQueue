"""#89: outcome comes from page evidence read by our code, not the agent's done(success)."""
import pytest

from core import job_status as js
from core.outcome import decide_outcome, detect_confirmation


@pytest.mark.parametrize("text", [
    "Thank you for applying to Example Corp!",
    "Your application has been submitted.",
    "We've received your application and will be in touch.",
    "Application submitted",
])
def test_detects_confirmation_text(text):
    ev = detect_confirmation(text, "https://boards.example.com/apply")
    assert ev.confirmed and ev.source == "text"


def test_detects_confirmation_url():
    ev = detect_confirmation("All done", "https://boards.example.com/jobs/1/thank-you")
    assert ev.confirmed and ev.source == "url"


@pytest.mark.parametrize("text", [
    "Please fix the following errors. Thank you for applying!",
    "This field is required",
    "Your application could not be submitted",
    "Apply for this job",
])
def test_rejects_non_confirmation(text):
    assert not detect_confirmation(text, "https://boards.example.com/apply").confirmed


def test_agent_self_report_alone_is_never_submitted():
    out = decide_outcome(page=detect_confirmation("Apply now"), agent_claimed_success=True)
    assert out.status == js.NEEDS_HUMAN
    assert out.evidence["agent_claimed_success"] is True


def test_page_confirmation_gives_submitted_unconfirmed():
    out = decide_outcome(page=detect_confirmation("Thank you for applying"), agent_claimed_success=False)
    assert out.status == js.SUBMITTED_UNCONFIRMED


def test_page_and_email_give_confirmed():
    out = decide_outcome(page=detect_confirmation("Application submitted"), agent_claimed_success=True,
                         email_confirmed=True)
    assert out.status == js.CONFIRMED


def test_email_without_page_is_not_confirmed():
    out = decide_outcome(page=None, agent_claimed_success=False, email_confirmed=True)
    assert out.status == js.FAILED


def test_unanswered_fields_need_a_human():
    out = decide_outcome(page=None, agent_claimed_success=False,
                         needs_human=[{"field_label": "Security clearance", "reason": "not in profile"}])
    assert out.status == js.NEEDS_HUMAN and "Security clearance" in out.reason


def test_nothing_is_failed():
    assert decide_outcome(page=None, agent_claimed_success=False).status == js.FAILED
