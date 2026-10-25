"""M3: run file shared by the runner and the MCP server; status comes from ApplyQueue's evidence."""
import json

from core import jobs_store
from core.run_state import RunState, finalize_job, skip_job

A, B = "https://jobs.example.com/a", "https://jobs.example.com/b"


def seed(d, **status):
    jobs = {A: {"url": A, "status": status.get("a", "pending")}, B: {"url": B, "status": status.get("b", "pending")}}
    (d / "jobs.json").write_text(json.dumps(jobs))


def confirm(state, url, at="2026-10-25T00:00:01"):
    state.update_ledger(lambda led: led["confirmations"].append(
        {"confirmed": True, "matched": "thank you for applying", "url": url, "source": "text", "at": at}), url)


def test_queue_and_handoff(aq_data_dir):
    seed(aq_data_dir)
    st = RunState.create(aq_data_dir / "runs" / "x", backend="cli:codex", job_urls=[A], handoff=True)
    assert st.next_job()["url"] == A
    skip_job(st, A, "closed posting")
    nxt = st.next_job()  # handoff: claims the next pending job from jobs.json
    assert nxt["url"] == B and jobs_store.get_job(B)["status"] == "in_progress"


def test_page_confirmation_submits_and_is_idempotent(aq_data_dir):
    seed(aq_data_dir)
    st = RunState.create(aq_data_dir / "runs" / "y", backend="cli:cursor", job_urls=[A])
    st.next_job()
    confirm(st, A)
    r1 = finalize_job(st, A, agent_claimed_success=True)
    r2 = finalize_job(st, A, agent_claimed_success=False, failure_reason="late crash")
    assert r1 == r2 and r1["status"] == "submitted_unconfirmed"
    job = jobs_store.get_job(A)
    assert job["page_confirmation"] and job["agent_backend"] == "cli:cursor"


def test_post_error_after_submit_vetoes_confirmation(aq_data_dir):
    seed(aq_data_dir)
    st = RunState.create(aq_data_dir / "runs" / "z", backend="cli:grok", job_urls=[A])
    st.next_job()
    st.update_ledger(lambda led: led.update(last_submit_at="2026-10-25T00:00:00"), A)
    confirm(st, A)
    st.update_ledger(lambda led: led["network_errors"].append(
        {"method": "POST", "status": 422, "url": "https://ats.example.com/apply", "at": "2026-10-25T00:00:02"}), A)
    r = finalize_job(st, A, agent_claimed_success=True)
    assert r["status"] != "submitted_unconfirmed"
    assert "422" in jobs_store.get_job(A)["outcome_reason"] or r["status"] == "needs_human"


def test_confirm_by_email(aq_data_dir):
    seed(aq_data_dir, a="submitted_unconfirmed")
    jobs_store.update_job(A, page_confirmation={"matched": "thank you for applying"})
    assert jobs_store.confirm_by_email(B, {"message_id": "m1"}) == (False, "not_submitted")
    assert jobs_store.confirm_by_email(A, {"body": "x"}) == (False, "missing_evidence")
    assert jobs_store.confirm_by_email("https://nope.example.com", {"message_id": "m"}) == (False, "not_found")
    assert jobs_store.confirm_by_email(A, {"message_id": "m1", "from_domain": "ats.example.com",
                                           "body": "never stored"}) == (True, "confirmed")
    job = jobs_store.get_job(A)
    assert job["status"] == "confirmed" and "never stored" not in json.dumps(job)
