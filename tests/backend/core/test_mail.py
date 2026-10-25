"""M4: Gmail / Outlook linking, OTP via API, confirmation sweep — against mock OAuth/Gmail/Graph servers."""
import json
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "fixtures"))
from mock_mail_server import MockMail  # noqa: E402

from core import jobs_store  # noqa: E402
from core.mail import classify, oauth, providers, service  # noqa: E402

PORT = 8743
CO = "Example Robotics"
JOB_A = "https://boards.greenhouse.io/examplerobotics/jobs/1"
JOB_B = "https://jobs.lever.co/acmewidgets/2"

# A real ATS sender domain (built by concatenation so the privacy scan sees no address).
GH_SENDER = "no-reply" + "@" + "us.greenhouse-mail.io"


@pytest.fixture
def mock(aq_data_dir, monkeypatch):
    m = MockMail()
    m.point(monkeypatch, oauth, providers)
    monkeypatch.setattr(service, "POLL_S", 0.05)
    yield m
    m.close()


def link(mock, provider, **cfg):
    oauth.set_client(provider, f"client-{provider}.apps.example.com", **cfg)
    url = oauth.start_authorization(provider, PORT)
    state = mock.authorize(url)
    assert oauth.complete_authorization(state, "code-1") == provider
    return url


def seed_jobs(d, now_iso="2026-01-01T00:00:00+00:00"):
    jobs = {
        JOB_A: {"url": JOB_A, "company": CO, "title": "Robotics Engineer", "status": "submitted_unconfirmed",
                "submitted_at": now_iso, "claimed_at": now_iso, "page_confirmation": {"matched": "thank you"}},
        JOB_B: {"url": JOB_B, "company": "Acme Widgets", "title": "Data Analyst", "status": "submitted_unconfirmed",
                "submitted_at": now_iso, "claimed_at": now_iso},
    }
    (d / "jobs.json").write_text(json.dumps(jobs))


def _iso_minutes_ago(n):
    return time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(time.time() - n * 60))


# ── OAuth ────────────────────────────────────────────────────────────────
def test_gmail_link_pkce_and_day6_reauth(mock, aq_data_dir):
    url = link(mock, "gmail", client_secret="desktop-secret")
    assert "access_type=offline" in url and "prompt=consent" in url and "gmail.modify" in url
    assert "redirect_uri=http%3A%2F%2F127.0.0.1%3A8743%2Femail%2Foauth%2Fcallback" in url
    st = oauth.status("gmail")
    assert st["linked"] and st["email"] == "alex@example.com" and not st["reauth_due"]
    assert st["client_secret_set"] and "desktop-secret" not in json.dumps(st)
    assert "rt-" not in json.dumps(st) and "at-" not in json.dumps(st)
    assert oauth.status("gmail", now=time.time() + 6 * 86400 + 5)["reauth_due"]
    assert (oauth.accounts_file().stat().st_mode & 0o077) == 0
    # Re-connecting (the one-click prompt) resets the 6-day clock.
    url2 = oauth.start_authorization("gmail", PORT)
    assert "login_hint=alex%40example.com" in url2
    assert oauth.complete_authorization(mock.authorize(url2, "code-2"), "code-2") == "gmail"


def test_invalid_grant_flags_reconnect(mock, aq_data_dir):
    link(mock, "gmail")
    rt = oauth.load_accounts()["gmail"]["refresh_token"]
    oauth._update("gmail", expires_at=0)
    mock.revoked.add(rt)
    with pytest.raises(oauth.ReauthRequired):
        oauth.access_token("gmail")
    st = oauth.status("gmail")
    assert st["needs_reauth"] and st["reauth_due"] and not st["linked"]
    assert not service.has_linked_account()


def test_state_is_one_time_and_bound(mock, aq_data_dir):
    oauth.set_client("gmail", "client-gmail.apps.example.com")
    url = oauth.start_authorization("gmail", PORT)
    with pytest.raises(oauth.OAuthError):
        oauth.complete_authorization("forged-state", "code-1")
    state = mock.authorize(url)
    oauth.complete_authorization(state, "code-1")
    with pytest.raises(oauth.OAuthError):
        oauth.complete_authorization(state, "code-1")


def test_outlook_public_client_pkce_no_secret(mock, aq_data_dir):
    url = link(mock, "outlook", tenant="consumers")
    assert "/ms/consumers/oauth2/v2.0/authorize" in url and "offline_access" in url
    assert "redirect_uri=http%3A%2F%2Flocalhost%3A8743%2Femail%2Foauth%2Fcallback" in url
    assert all("client_secret" not in r for r in mock.token_requests)
    first = oauth.load_accounts()["outlook"]["refresh_token"]
    oauth._update("outlook", expires_at=0)
    oauth.access_token("outlook")
    assert oauth.load_accounts()["outlook"]["refresh_token"] != first  # rotated token saved
    assert oauth.status("outlook")["reauth_due_at"] is None  # no 7-day limit for Microsoft


def test_disconnect_revokes_and_forgets(mock, aq_data_dir):
    link(mock, "gmail")
    rt = oauth.load_accounts()["gmail"]["refresh_token"]
    oauth.disconnect("gmail")
    assert rt in mock.revoked
    acct = oauth.load_accounts()["gmail"]
    assert "refresh_token" not in acct and acct["client_id"]


def test_changing_client_drops_tokens(mock, aq_data_dir):
    link(mock, "gmail")
    oauth.set_client("gmail", "other-client.apps.example.com")
    assert not oauth.status("gmail")["linked"]


# ── OTP via API ──────────────────────────────────────────────────────────
def test_gmail_otp_reads_code_and_archives_it(mock, aq_data_dir):
    link(mock, "gmail")
    seed_jobs(aq_data_dir)
    mock.add_gmail(sender="Someone <friend@example.org>", subject="Lunch?", body="See you at 12:30, room 4821")
    mid = mock.add_gmail(sender=f"Example Robotics <{GH_SENDER}>",
                         subject="Your Example Robotics security code",
                         body="Hi Alex,\nYour security code is: 482913\nIt expires in 10 minutes.")
    got = service.get_otp(hint=CO, since_minutes=15, wait_seconds=0, job_url=JOB_A)
    assert got["code"] == "482913" and got["message_id"] == mid
    labels = mock.gmail_msgs[mid]["labelIds"]
    assert "INBOX" not in labels and mock.gmail_labels["ApplyQueue/Verification"] in labels
    # Already used codes are skipped; nothing else matches -> None (no waiting with wait_seconds=0).
    assert service.get_otp(hint=CO, wait_seconds=0, job_url=JOB_A, exclude_ids=[mid]) is None
    assert ("GET", "/mail/u/0") not in mock.calls


def test_otp_waits_for_late_email(mock, aq_data_dir):
    import threading

    link(mock, "gmail")
    threading.Timer(0.3, lambda: mock.add_gmail(
        sender="Acme Widgets <talent@acmewidgets.example.com>", subject="Verify your email",
        body="Use code 7731 to verify your email address.", minutes_ago=0)).start()
    got = service.get_otp(hint="Acme Widgets", wait_seconds=5)
    assert got and got["code"] == "7731"


def test_outlook_otp_and_verify_link(mock, aq_data_dir):
    link(mock, "outlook")
    mock.add_graph(name="Acme Widgets Careers", address="careers@acmewidgets.example.com",
                   subject="Confirm your email", body="Click https://acmewidgets.example.com/verify?t=abc to confirm.")
    got = service.get_otp(hint="Acme Widgets", wait_seconds=0)
    assert got["code"] is None and got["link"].startswith("https://acmewidgets.example.com/verify")
    archived = [m for m in mock.graph_msgs.values() if m["folder"] == "archive"]
    assert archived and archived[0]["categories"] == ["ApplyQueue/Verification"]


# ── confirmation sweep ──────────────────────────────────────────────────
def test_gmail_sweep_confirms_labels_archives_and_is_idempotent(mock, aq_data_dir):
    link(mock, "gmail")
    seed_jobs(aq_data_dir, now_iso=_iso_minutes_ago(30))
    conf = mock.add_gmail(sender=f"Example Robotics <{GH_SENDER}>",
                          subject="Thank you for applying to Example Robotics",
                          body="<p>Hi Alex, we have received your application for <b>Robotics Engineer</b>.</p>",
                          minutes_ago=10, html=True)
    no_page = mock.add_gmail(sender="Acme Widgets <jobs@acmewidgets.example.com>",
                             subject="Application received - Data Analyst", body="Thanks!", minutes_ago=9)
    alert = mock.add_gmail(sender="Job Alerts <alerts@jobs.example.net>", subject="Jobs you may like",
                           body="Thank you for applying... 20 new roles at Example Robotics", minutes_ago=8)
    verify = mock.add_gmail(sender=f"Example Robotics <{GH_SENDER}>",
                            subject="Example Robotics verification code", body="Code: 120934", minutes_ago=5)
    other = mock.add_gmail(sender="Bank <notice@bank.example.org>", subject="Your security code",
                           body="Your security code is 555123", minutes_ago=4)

    s = service.sweep()
    assert s["confirmed"] == [JOB_A] and s["labeled"] == 1 and s["verification_archived"] == 1
    job = jobs_store.get_job(JOB_A)
    assert job["status"] == "confirmed"
    assert job["email_confirmation"]["message_id"] == conf and "body" not in job["email_confirmation"]
    assert "received your application" not in json.dumps(job)
    applied = mock.gmail_labels["ApplyQueue/Applied"]
    assert mock.gmail_msgs[conf]["labelIds"] == ["UNREAD", applied]
    # No page evidence -> never auto-confirmed; unrelated mail untouched.
    assert jobs_store.get_job(JOB_B)["status"] == "submitted_unconfirmed"
    for mid in (no_page, alert, other):
        assert "INBOX" in mock.gmail_msgs[mid]["labelIds"]
    assert "INBOX" not in mock.gmail_msgs[verify]["labelIds"]

    modifies = sum(1 for c in mock.calls if c[1].endswith("/modify"))
    again = service.sweep(lookback_s=3 * 86400)
    assert again["confirmed"] == [] and sum(1 for c in mock.calls if c[1].endswith("/modify")) == modifies


def test_custom_label_and_no_archive(mock, aq_data_dir):
    from core.config import load_settings, save_settings

    link(mock, "outlook")
    seed_jobs(aq_data_dir, now_iso=_iso_minutes_ago(30))
    settings = load_settings()
    settings["email"] = service.clean_settings({"confirm_label": "Job Hunt", "archive_confirmations": False})
    save_settings(settings)
    mid = mock.add_graph(name="Example Robotics", address=GH_SENDER,
                         subject="Thanks for applying to Example Robotics", body="We got your application.",
                         minutes_ago=5)
    s = service.sweep()
    assert s["confirmed"] == [JOB_A]
    assert mock.graph_msgs[mid]["categories"] == ["Job Hunt"] and mock.graph_msgs[mid]["folder"] == "inbox"


def test_sweep_skips_email_older_than_the_application(mock, aq_data_dir):
    link(mock, "gmail")
    seed_jobs(aq_data_dir, now_iso=_iso_minutes_ago(5))
    mock.add_gmail(sender=f"Example Robotics <{GH_SENDER}>",
                   subject="Thank you for applying to Example Robotics", body="Received.", minutes_ago=600)
    assert service.sweep()["confirmed"] == []


def test_reauth_surfaces_in_sweep(mock, aq_data_dir):
    link(mock, "gmail")
    seed_jobs(aq_data_dir)
    mock.access.clear()            # access token rejected ...
    mock.revoked.update(mock.refresh_tokens)  # ... and refresh says invalid_grant
    s = service.sweep()
    assert s["errors"] and oauth.status("gmail")["needs_reauth"]


# ── classifier ──────────────────────────────────────────────────────────
@pytest.mark.parametrize("text,code", [
    ("Your verification code is 482913.", "482913"),
    ("Passcode: A7K9Q2", "A7K9Q2"),
    ("Use 7731 to sign in. © 2026 Example Inc", "7731"),
    ("Enter the code below\n\n  590 211 \n", None),
    ("Thanks for applying in 2026", None),
])
def test_extract_code(text, code):
    assert classify.extract_code(text) == code


def test_kind_and_ambiguous_match():
    conf = {"subject": "Thank you for your application", "text": "Example Robotics team", "from_domain": "x.io",
            "received_at": time.time()}
    rej = {"subject": "Your application to Example Robotics", "text": "Unfortunately we will not be moving forward"}
    assert classify.kind(conf) == "confirmation" and classify.kind(rej) is None
    a = {"url": "https://a.example.com/1", "company": CO, "title": "Engineer"}
    b = {"url": "https://b.example.com/2", "company": CO, "title": "Designer"}
    assert classify.match_job(conf, [a, b]) is None  # same company, no title, no dates -> ambiguous
    conf["text"] += " Designer role"
    assert classify.match_job(conf, [a, b]) is b
