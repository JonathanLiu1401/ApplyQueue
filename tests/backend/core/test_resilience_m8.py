"""Break-it cases found in M8: crash recovery, concurrent instances, corrupt files, partial
profiles, submit guards. Each test pins a bug that was fixed in M8 (see TEST_REPORT.md)."""
import json
import os
import socket
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "cli"))

from core import jobs_store  # noqa: E402
from core import job_status as js  # noqa: E402
from core.config import read_json_file  # noqa: E402

URL = "https://boards.greenhouse.io/examplecorp/jobs/1"


def _jobs(d):
    return json.loads((d / "jobs.json").read_text("utf-8"))


def _seed(d, **job):
    (d / "jobs.json").write_text(json.dumps({URL: {"url": URL, "status": "pending", **job}}), "utf-8")


def _child(d, code):
    env = {**os.environ, "APPLYQUEUE_DATA_DIR": str(d), "PYTHONPATH": os.pathsep.join([str(ROOT), str(ROOT / "backend")])}
    return subprocess.Popen([sys.executable, "-c", code], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


KILLED_AFTER_SUBMIT = """
import os, signal, time
from core import jobs_store
assert jobs_store.claim_job(%r)
jobs_store.mark_submit_click(%r)
print("clicked", flush=True)
time.sleep(60)
""" % (URL, URL)

KILLED_BEFORE_SUBMIT = """
import time
from core import jobs_store
assert jobs_store.claim_job(%r)
print("claimed", flush=True)
time.sleep(60)
""" % URL


@pytest.mark.parametrize("code,expect", [(KILLED_AFTER_SUBMIT, "needs_human"), (KILLED_BEFORE_SUBMIT, "pending")])
def test_process_killed_mid_apply_never_resubmits(aq_data_dir, code, expect):
    _seed(aq_data_dir)
    p = _child(aq_data_dir, code)
    try:
        assert p.stdout.readline().strip() in (b"clicked", b"claimed"), p.stderr.read()
        job = _jobs(aq_data_dir)[URL]
        assert job["status"] == js.IN_PROGRESS and job["claimed_pid"] == p.pid
        # Another instance starting while the first is alive must not steal its job.
        jobs = _jobs(aq_data_dir)
        assert jobs_store.recover_stale_claims(jobs) == (0, 0) and jobs[URL]["status"] == js.IN_PROGRESS
    finally:
        p.kill()
        p.wait()
    jobs = _jobs(aq_data_dir)
    requeued, flagged = jobs_store.recover_stale_claims(jobs)
    assert jobs[URL]["status"] == expect
    if expect == "needs_human":
        assert flagged == 1 and jobs[URL]["error"] == jobs_store.INTERRUPTED_AFTER_SUBMIT
    else:
        assert requeued == 1


def test_claim_from_other_host_or_old_is_stale(aq_data_dir):
    job = {"status": "in_progress", "claimed_pid": os.getppid(), "claimed_host": socket.gethostname(),
           "claimed_at": "2020-01-01T00:00:00+00:00"}
    assert not jobs_store.claim_is_live(job)                     # older than the stale window
    job["claimed_at"] = "2999-01-01T00:00:00+00:00"
    job["claimed_host"] = "another-machine"
    assert not jobs_store.claim_is_live(job)                     # a pid on another host means nothing here


def test_reclaim_clears_old_submit_markers(aq_data_dir):
    _seed(aq_data_dir, submit_clicks=3, last_submit_click_at="2026-01-01T00:00:00+00:00")
    assert jobs_store.claim_job(URL)
    job = _jobs(aq_data_dir)[URL]
    assert "submit_clicks" not in job or job["submit_clicks"] == 0


@pytest.mark.parametrize("raw", [b"{not json", b"\xff\xfe\x00garbage", b"[1, 2, 3]", b""])
def test_corrupt_jobs_file_is_quarantined_not_fatal(aq_data_dir, raw):
    (aq_data_dir / "jobs.json").write_bytes(raw)
    assert jobs_store.get_job(URL) is None
    if raw:
        assert list(aq_data_dir.glob("jobs.json.corrupt-*")), "corrupt file must be kept for the user"
    _seed(aq_data_dir)
    assert jobs_store.claim_job(URL)


def test_read_json_file_bom_and_type_mismatch(tmp_path):
    p = tmp_path / "settings ü.json"
    p.write_bytes(b"\xef\xbb\xbf" + json.dumps({"a": "é"}).encode())
    assert read_json_file(p, {}) == {"a": "é"}
    p.write_text("[]", "utf-8")
    assert read_json_file(p, {}) == {}
    assert list(tmp_path.glob("settings ü.json.corrupt-*"))


@pytest.mark.parametrize("profile", [
    {},
    {"name": "Alex Example"},                                        # quick-start profile
    {"name": "Alex Example", "address": "Springfield", "education": ["x"], "salary_expectation": "150k"},
    {"name": "عليكس 例子 🚀", "email": "alex@example.com", "salary_expectation": {"min": "150000"}},
    {"name": "A" * 20000, "skills": ["x"] * 5000},
])
def test_memory_context_survives_partial_or_odd_profiles(profile):
    from core.shared_config import build_memory_context
    out = build_memory_context(profile, {})
    assert isinstance(out, str)
    if "email" not in profile:
        assert "never invent" in out


def test_safe_log_name_is_one_segment():
    from apply_jobs import _safe_log_name
    n = _safe_log_name("Acme/Widgets: Inc.", 'Sr. Eng <C++> | "AI" \\ Remote?*')
    assert "/" not in n and "\\" not in n and not any(c in n for c in '<>:"|?*') and len(n) <= 80
    assert _safe_log_name("", "") == "apply"
    assert _safe_log_name("例子", "エンジニア")


def _node(tag, text="", **attrs):
    return SimpleNamespace(tag_name=tag, attributes=attrs, get_all_children_text=lambda: text)


@pytest.mark.parametrize("node,expect", [
    (_node("button", "Submit Application"), True),
    (_node("button", "Save and Continue"), True),
    (_node("input", type="submit", value="Go"), True),
    (_node("a", "Next", role="button"), True),
    (_node("button", "Apply"), False),          # a job page's Apply button is allowed
    (_node("button", "Upload resume"), False),
    (_node("div", "Submit"), False),
    (_node("label", "Continue"), False),
])
def test_looks_like_submit(node, expect):
    from core.apply_tools import looks_like_submit
    assert looks_like_submit(node) is expect


async def test_builtin_click_cannot_bypass_the_guard(monkeypatch, aq_data_dir):
    from core import apply_tools as at
    from core import form_guard as fg
    ledger = at.RunLedger()
    clicked = []

    class Sess:
        async def get_element_by_index(self, i):
            return _node("button", "Submit Application")

    tools = at.Tools()
    tools.registry.registry.actions["click"].function = lambda **k: _coro(clicked.append(1) or "ok")
    at._guard_builtin_click(tools, ledger, {"url": URL})
    fn = tools.registry.registry.actions["click"].function
    unanswered = [fg.Field(key="f1", kind="textarea", label="Why do you want to work here?", empty=True)]
    monkeypatch.setattr(at.fg, "scan", lambda ev: _coro(unanswered))
    monkeypatch.setattr(at, "session_evaluator", lambda s: None)
    res = await fn(params=SimpleNamespace(index=3), browser_session=Sess())
    assert res.error and "Refused" in res.error and not clicked and ledger.blocked_submits == 1
    monkeypatch.setattr(at.fg, "scan", lambda ev: _coro([]))
    _seed(aq_data_dir)
    assert jobs_store.claim_job(URL)
    assert await fn(params=SimpleNamespace(index=3), browser_session=Sess()) == "ok" and clicked == [1]
    assert _jobs(aq_data_dir)[URL]["submit_clicks"] == 1     # marker written before the click


async def _coro(v):
    return v
