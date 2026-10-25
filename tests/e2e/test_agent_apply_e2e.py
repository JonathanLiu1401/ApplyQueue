"""Full apply runs, end to end (M8): the real browser_use agent loop + ApplyQueue's tools.

Nothing is stubbed between the agent and the employer's page: apply_jobs.apply_to_job
claims the job, launches headless Chromium, and the browser_use Agent talks to an
OpenAI-compatible endpoint. That endpoint is tests/fixtures/scripted_llm (a deterministic
"model" that reads the same browser state a real model would and picks actions), so the
run is reproducible and free. The employer is tests/fixtures/mock_ats, which records
exactly what it received. The data dir has spaces and non-ASCII characters on purpose.

Asserted on every run: the job's final status comes from page evidence, every question
the employer asked got a truthful answer from the Alex Example persona, the resume
arrived, and there is never more than one submission.
"""
import asyncio
import json
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
for p in (ROOT / "cli", ROOT / "tests" / "fixtures"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from tests.fixtures.mock_ats import MockATS  # noqa: E402
from tests.fixtures.persona import PROFILE, QA, write_resume_pdf  # noqa: E402
from tests.fixtures.scripted_llm import ScriptedLLM  # noqa: E402

pytest.importorskip("browser_use")

DECLINE = "I don't wish to answer"


@pytest.fixture
def env(tmp_path, monkeypatch):
    d = tmp_path / "Alex Example ü data dir"
    d.mkdir()
    monkeypatch.setenv("APPLYQUEUE_DATA_DIR", str(d / "data"))
    monkeypatch.setenv("APPLYQUEUE_MCP_HEADLESS", "1")
    for name in ("core.shared_config", "backend.core.shared_config"):
        mod = sys.modules.get(name)
        if mod is not None and hasattr(mod, "JOBS_FILE"):
            monkeypatch.setattr(mod, "JOBS_FILE", d / "data" / "jobs.json")
            monkeypatch.setattr(mod, "JOBS_LOCK", d / "data" / "jobs.json.lock")
    # Own Chromium profile per test. The default profile is shared machine-wide, so a sidecar
    # starting or stopping elsewhere (UI e2e, a dev instance) used to kill this test's browser
    # ("WebSocket connection closed"), and a slow-exiting previous Chromium held its lock.
    import apply_jobs as _aj
    import core.shared_config as _sc
    for mod in (_aj, _sc):
        monkeypatch.setattr(mod, "BROWSER_PROFILE_DIR", d / "browser_profile", raising=False)
    ats, llm = MockATS(), ScriptedLLM()
    monkeypatch.setenv("APPLYQUEUE_E2E_ATS_PORTS", str(ats.port))   # the only way 127.0.0.1 is allowed
    import apply_jobs
    import core.shared_config as sc
    from browser_use.llm import ChatOpenAI
    monkeypatch.setattr(sc, "get_llm", lambda *a, **k: ChatOpenAI(model="scripted", base_url=llm.base_url,
                                                                   api_key="test-not-a-key"))
    monkeypatch.setattr(apply_jobs, "get_llm", lambda *a, **k: sc.get_llm(), raising=False)
    resume = write_resume_pdf(d / "Alex Example résumé.pdf")
    yield {"dir": d, "ats": ats, "llm": llm, "resume": resume, "apply_jobs": apply_jobs}
    llm.close()
    ats.close()


def run_job(env, url, qa=None):
    from core import jobs_store
    jf, _ = jobs_store._paths()
    jf.parent.mkdir(parents=True, exist_ok=True)
    jobs = json.loads(jf.read_text("utf-8")) if jf.exists() else {}
    jobs.setdefault(url, {"url": url, "title": "Platform Engineer", "company": "Example Corp", "status": "pending"})
    jf.write_text(json.dumps(jobs), "utf-8")
    t0 = time.monotonic()
    try:
        status = asyncio.run(env["apply_jobs"].apply_to_job(
            {"url": url, "title": "Platform Engineer", "company": "Example Corp"},
            PROFILE, QA if qa is None else qa, [], False, 1, resume_path_override=env["resume"]))
    except Exception as e:  # pragma: no cover
        if "executable" in str(e).lower() or "chrom" in str(e).lower() and "not found" in str(e).lower():
            pytest.skip(f"No Chromium for browser_use: {e}")
        raise
    return status, jobs_store.get_job(url), time.monotonic() - t0


def _assert_truthful(sub):
    a = sub["answers"]
    assert all(v not in ("", None, []) for v in a.values()), f"blank answer sent: {a}"
    assert sub["files"]["resume"]["head"] == "%PDF-1.4"
    assert sub["files"]["resume"]["filename"] == "Alex Example résumé.pdf"
    if "email" in a:
        assert a["email"] == PROFILE["email"]
    first, last = PROFILE["name"].split(" ", 1)
    assert a.get("first_name", first) == first and a.get("last_name", last) == last
    if "name" in a:
        assert a["name"] == PROFILE["name"]
    if "authorized" in a:
        assert a["authorized"] == "Yes"
    if "sponsorship" in a:
        assert a["sponsorship"] == "No"
    for k in ("gender", "hispanic", "race", "veteran", "disability"):
        if k in a:
            assert a[k] == DECLINE, f"EEO {k} must use the persona's decline choice"
    for k, v in a.items():  # essays only state resume facts
        if isinstance(v, str) and len(v) > 60:
            assert "Example Labs" in v and "invent" not in v.lower()


@pytest.mark.parametrize("site", ["gh", "lever", "ashby", "wd", "icims"])
def test_full_apply_each_ats_style(env, site):
    url = env["ats"].job_url(site)
    status, job, secs = run_job(env, url)
    subs = env["ats"].submissions_for(site)
    assert status == "submitted_unconfirmed", job
    assert job["page_confirmation"]["confirmed"] is True
    assert len(subs) == 1
    _assert_truthful(subs[0])
    assert job["submit_clicks"] >= 1
    if site == "wd":
        assert subs[0]["account"] == PROFILE["email"]   # no login email set -> the profile email, never ""
        from core.credential_vault import CredentialVault
        assert CredentialVault.default().password_for_sign_in("127.0.0.1")   # unique per-site password stored
    # The scripted model only sees what a real model sees: it found every unanswered
    # question through autofill's long_term_memory, not through hidden state.
    assert "report_needs_human" not in env["llm"].flat_actions()
    assert secs < 240
    # No browser_use judge call: one model call per agent step, nothing extra.
    assert not any("verdict" in json.dumps(r.get("response_format", {})) for r in env["llm"].requests)

    # Re-running a submitted job never applies twice.
    status2, _, _ = run_job(env, url)
    assert status2 == "skipped"
    assert len(env["ats"].submissions_for(site)) == 1


def test_captcha_goes_to_a_person(env):
    status, job, _ = run_job(env, env["ats"].job_url("gh", chaos="captcha"))
    assert status == "needs_human" and job["needs_human_fields"][0]["field_label"] == "CAPTCHA"
    assert env["ats"].submissions_for("gh") == []


def test_no_confirmation_is_not_called_success(env):
    status, job, _ = run_job(env, env["ats"].job_url("gh", chaos="noconfirm"))
    # The agent said success, the page did not: a person checks before any retry.
    assert job["agent_claimed_success"] is True and status == "needs_human"
    assert job["page_confirmation"] is None and "verify manually" in job["outcome_reason"]
    assert len(env["ats"].submissions_for("gh")) == 1


def test_server_error_on_submit_is_not_success_and_not_resubmitted_blindly(env):
    status, job, _ = run_job(env, env["ats"].job_url("gh", chaos="submit500"))
    assert status == "needs_human" and job["page_confirmation"] is None
    assert len(env["ats"].submissions_for("gh")) <= 1


def test_unanswerable_question_flags_a_person(env):
    # An essay the persona's resume cannot answer: the scripted model withholds it and the
    # guard refuses to submit, so the job is flagged instead of sent with a blank.
    from tests.fixtures import scripted_llm
    orig = scripted_llm.essay_for
    scripted_llm.essay_for = lambda label: ""
    try:
        status, job, _ = run_job(env, env["ats"].job_url("gh"))
    finally:
        scripted_llm.essay_for = orig
    assert status == "needs_human"
    assert env["ats"].submissions_for("gh") == []


def test_workday_email_code_through_linked_outlook(env, monkeypatch):
    """Full Outlook path: link Outlook (PKCE, mock Microsoft identity), the ATS emails a
    code to the linked mailbox, get_email_code reads it through Microsoft Graph, the
    agent types it, finishes 6 pages and the confirmation is page-verified."""
    from mock_mail_server import MockMail
    from core.mail import oauth, providers, service
    mail = MockMail()
    try:
        mail.point(monkeypatch, oauth, providers)
        monkeypatch.setattr(service, "POLL_S", 0.05)
        oauth.set_client("outlook", "client-outlook.apps.example.com")
        url = oauth.start_authorization("outlook", 8743)
        assert oauth.complete_authorization(mail.authorize(url), "code-1") == "outlook"
        env["ats"].on_code = lambda email, code: mail.add_graph(
            name="Example Health Recruiting", address="recruiting" + "@" + "example.com",
            subject="Verify your Example Health candidate account",
            body=f"Your verification code is {code}. It expires in 10 minutes.", minutes_ago=0)
        status, job, _ = run_job(env, env["ats"].job_url("wd", verify="1"))
        assert "get_email_code" in env["llm"].flat_actions()
        assert any(p.startswith("/graph/") for _, p in mail.calls)
        assert status == "submitted_unconfirmed" and job["page_confirmation"]["confirmed"]
        subs = env["ats"].submissions_for("wd")
        assert len(subs) == 1
        _assert_truthful(subs[0])
    finally:
        mail.close()
