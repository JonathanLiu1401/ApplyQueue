"""M8 break-it: mailbox sign-in revoked mid-run, OTPs that arrive late or twice, confirmations for the wrong job."""
import json
import sys
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "fixtures"))
from mock_mail_server import MockMail  # noqa: E402

from core import jobs_store  # noqa: E402
from core.mail import classify, oauth, providers, service  # noqa: E402
from core.searches import runner, schedule  # noqa: E402

PORT = 8743
GH_SENDER = "no-reply" + "@" + "us.greenhouse-mail.io"
CO = "Example Robotics"
JOB_A = "https://boards.greenhouse.io/examplerobotics/jobs/1"
JOB_B = "https://boards.greenhouse.io/examplerobotics/jobs/2"


@pytest.fixture
def mock(aq_data_dir, monkeypatch):
    m = MockMail()
    m.point(monkeypatch, oauth, providers)
    monkeypatch.setattr(service, "POLL_S", 0.05)
    yield m
    m.close()


def link(mock, provider="gmail"):
    oauth.set_client(provider, f"client-{provider}.apps.example.com")
    state = mock.authorize(oauth.start_authorization(provider, PORT))
    oauth.complete_authorization(state, "code-1")


def _iso(minutes_ago):
    return time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(time.time() - minutes_ago * 60))


def _revoke(mock):
    mock.access.clear()
    mock.revoked.update(mock.refresh_tokens)


# ── invalid_grant mid-run ───────────────────────────────────────────────
def test_invalid_grant_while_waiting_for_otp_fails_fast_with_reauth(mock, aq_data_dir):
    link(mock)
    threading.Timer(0.2, _revoke, args=(mock,)).start()
    t0 = time.monotonic()
    with pytest.raises(oauth.ReauthRequired) as e:
        service.get_otp(hint=CO, wait_seconds=30)
    assert time.monotonic() - t0 < 5          # did not sit out the 30 s wait
    assert "re-auth" in str(e.value) and "Gmail" in str(e.value)
    assert oauth.status("gmail")["needs_reauth"] and service.reauth_providers() == ["gmail"]
    # Next call (e.g. another job in the same run) is told to reconnect, not "no code yet".
    with pytest.raises(oauth.ReauthRequired):
        service.get_otp(hint=CO, wait_seconds=0)


def test_unlinked_mailbox_while_waiting_returns_none_not_reauth(mock, aq_data_dir):
    link(mock)
    threading.Timer(0.2, oauth.disconnect, args=("gmail",)).start()
    assert service.get_otp(hint=CO, wait_seconds=5) is None


def test_mail_reauth_pauses_the_scheduled_search_with_a_mail_notice(aq_data_dir):
    from core.searches import store
    s = store.upsert_search({"titles": ["Robotics Engineer"], "schedule": {"cron": "0 9 * * *"}})
    msg = service.reauth_message(["gmail"])
    log_lines = ["step 4: get_email_code", f"❌ {msg}"]
    outcome = runner.outcome_from([], log_lines)
    assert outcome == "auth"
    st = schedule.finish(s["id"], outcome, log_lines)
    assert st["paused"] and st["paused_reason"] == "login_expired"
    assert st["notice"]["target"] == "mail" and "Settings > Email" in st["notice"]["message"]
    # A job-site sign-in that expired still says portal, an AI key still says AI.
    assert schedule._notice(["Greenhouse session expired, please sign in"])["target"] == "portal"
    assert schedule._notice(["401 invalid api key"])["target"] == "ai"


def test_apply_tool_reports_reauth_instead_of_asking_for_a_new_code(mock, aq_data_dir):
    import asyncio
    from core import apply_tools
    link(mock)
    _revoke(mock)
    oauth._update("gmail", expires_at=0)
    with pytest.raises(oauth.ReauthRequired):
        oauth.access_token("gmail")
    assert not service.has_linked_account()
    tools = apply_tools.build_apply_tools(ledger=apply_tools.RunLedger(), profile={}, qa={},
                                          context={"company": CO, "url": JOB_A})
    act = tools.registry.registry.actions["get_email_code"]
    res = asyncio.run(act.function(params=act.param_model(sender_hint=CO, wait_seconds=0)))
    assert res.error and "re-auth" in res.error and "Request a new code" not in res.error


# ── OTP late / twice ────────────────────────────────────────────────────
def test_duplicate_delivery_of_a_used_code_is_not_handed_out_again(mock, aq_data_dir):
    link(mock)
    first = mock.add_gmail(sender=f"{CO} <{GH_SENDER}>", subject=f"Your {CO} security code",
                           body="Your security code is: 482913", minutes_ago=2)
    got = service.get_otp(hint=CO, wait_seconds=0)
    assert got["code"] == "482913" and got["message_id"] == first
    # The mailer sends the same code again (new message id) ...
    mock.add_gmail(sender=f"{CO} <{GH_SENDER}>", subject=f"Your {CO} security code",
                   body="Your security code is: 482913", minutes_ago=0)
    assert service.get_otp(hint=CO, wait_seconds=0, exclude_ids=[first]) is None
    # ... and only a genuinely new code is returned.
    threading.Timer(0.2, lambda: mock.add_gmail(sender=f"{CO} <{GH_SENDER}>", subject=f"Your {CO} security code",
                                                body="Your security code is: 771204", minutes_ago=0)).start()
    again = service.get_otp(hint=CO, wait_seconds=5, exclude_ids=[first])
    assert again and again["code"] == "771204"


def test_late_code_after_a_resend_newest_wins_and_stale_ones_expire(mock, aq_data_dir):
    link(mock)
    mock.add_gmail(sender=f"{CO} <{GH_SENDER}>", subject="Verification code", body="Code: 111111", minutes_ago=40)
    assert service.get_otp(hint=CO, since_minutes=15, wait_seconds=0) is None   # too old to be this attempt's
    mock.add_gmail(sender=f"{CO} <{GH_SENDER}>", subject="Verification code", body="Code: 222222", minutes_ago=3)
    mock.add_gmail(sender=f"{CO} <{GH_SENDER}>", subject="Verification code", body="Code: 333333", minutes_ago=1)
    assert service.get_otp(hint=CO, wait_seconds=0)["code"] == "333333"


def test_otp_from_another_company_is_never_used(mock, aq_data_dir):
    link(mock)
    mock.add_gmail(sender="Acme Widgets <talent@acmewidgets.example.com>", subject="Verify your email",
                   body="Use code 7731 to verify your email address.", minutes_ago=0)
    assert service.get_otp(hint=CO, job_url=JOB_A, wait_seconds=0) is None


# ── confirmation matched to the wrong job ───────────────────────────────
def _seed(d, jobs):
    (d / "jobs.json").write_text(json.dumps({j["url"]: j for j in jobs}))


def test_two_jobs_same_company_untitled_confirmation_is_left_for_review(mock, aq_data_dir):
    link(mock)
    base = {"company": CO, "status": "submitted_unconfirmed", "page_confirmation": {"matched": "thank you"}}
    _seed(aq_data_dir, [{**base, "url": JOB_A, "title": "Robotics Engineer", "submitted_at": _iso(30)},
                        {**base, "url": JOB_B, "title": "Controls Engineer", "submitted_at": _iso(20)}])
    mock.add_gmail(sender=f"{CO} <{GH_SENDER}>", subject=f"Thank you for applying to {CO}",
                   body="We have received your application.", minutes_ago=5)
    s = service.sweep()
    assert s["confirmed"] == []                          # used to confirm JOB_B (the newer one)
    assert {jobs_store.get_job(u)["status"] for u in (JOB_A, JOB_B)} == {"submitted_unconfirmed"}
    # The title disambiguates.
    mock.add_gmail(sender=f"{CO} <{GH_SENDER}>", subject=f"Thank you for applying to {CO}",
                   body="We received your application for Robotics Engineer.", minutes_ago=4)
    assert service.sweep()["confirmed"] == [JOB_A]


def test_email_between_two_submissions_goes_to_the_earlier_job():
    now = time.time()
    a = {"url": JOB_A, "company": CO, "title": "Robotics Engineer", "submitted_at": now - 3600}
    b = {"url": JOB_B, "company": CO, "title": "Controls Engineer", "submitted_at": now - 600}
    msg = {"subject": f"Thanks for applying to {CO}", "text": "", "from_domain": "us.greenhouse-mail.io",
           "received_at": now - 1800}
    assert classify.match_job(msg, [a, b]) is a


@pytest.mark.parametrize("company,text", [
    ("Box", "Check your inbox for next steps. Thanks for applying to Example Robotics"),
    ("Box", "Thanks for applying to Dropbox"),
    ("Ramp", "Thank you for applying. Trampoline Park Careers"),
    ("Meta", "Thanks for applying to Metadata Systems"),
])
def test_short_company_is_not_matched_inside_other_words(company, text):
    msg = {"subject": "Application received", "text": text, "from_domain": "us.greenhouse-mail.io",
           "received_at": time.time()}
    job = {"url": "https://boards.greenhouse.io/x/jobs/1", "company": company, "title": "Engineer"}
    assert classify.job_score(msg, job) == 0


@pytest.mark.parametrize("company,text", [
    ("DataDog", "Thanks for applying to Data Dog"),
    ("Data Dog", "Thanks for applying to Datadog"),
    ("Box", "Thanks for applying to Box!"),
    ("Example Robotics Inc.", "Thanks for applying to Example Robotics"),
])
def test_company_still_matches_across_spacing(company, text):
    msg = {"subject": "Application received", "text": text, "from_domain": "us.greenhouse-mail.io"}
    assert classify.job_score(msg, {"url": "https://boards.greenhouse.io/x/jobs/1", "company": company})


def test_more_specific_company_wins():
    now = time.time()
    acme = {"url": "https://acme.example.com/jobs/1", "company": "Acme", "title": "Analyst", "submitted_at": now - 900}
    widgets = {"url": "https://acmewidgets.example.com/jobs/2", "company": "Acme Widgets", "title": "Analyst",
               "submitted_at": now - 1200}
    msg = {"subject": "Thanks for applying to Acme Widgets", "text": "", "from_domain": "us.greenhouse-mail.io",
           "received_at": now}
    assert classify.match_job(msg, [acme, widgets]) is widgets
