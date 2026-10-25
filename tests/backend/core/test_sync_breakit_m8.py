"""M8 break-it for the sync repo, against a local bare git remote.

Real concurrency (two OS processes = two devices racing the same remote), a network that drops
or hangs mid-push, a remote that turns public between a claim and its push, ledger lines that
were cut off mid-write, and very large session files.
"""
import json
import os
import shutil
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_sync import FULL, JOB_A, add_job, git, world  # noqa: E402,F401  (fixture re-export)

from core import job_status as js  # noqa: E402
from core import jobs_store  # noqa: E402
from core.credential_vault import CredentialVault  # noqa: E402
from core.sync import gitrepo, store  # noqa: E402
from core.sync.engine import LEDGER, SyncEngine, job_key  # noqa: E402

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")
BACKEND = Path(__file__).resolve().parents[3] / "backend"

WORKER = textwrap.dedent(r"""
    import json, os, sys, time
    sys.path.insert(0, sys.argv[1])
    from core import job_status as js, jobs_store
    from core.sync import store
    from core.sync.engine import SyncEngine
    remote, dev, out, start, n = sys.argv[2], sys.argv[3], sys.argv[4], float(sys.argv[5]), int(sys.argv[6])
    store.save_config({**store.DEFAULT_CONFIG, "enabled": True, "kind": "git", "remote_url": remote,
                       "device_name": dev})
    urls = [f"https://jobs.lever.co/examplerobotics/race-{i}" for i in range(n)]
    jobs_file, _ = jobs_store._paths()
    jobs_store._save(jobs_file, {u: {"url": u, "title": "Robotics Engineer", "company": "Example Robotics",
                                     "status": js.PENDING} for u in urls})
    SyncEngine().sync_now()
    while time.time() < start:
        time.sleep(0.005)
    res = []
    for url in urls:
        try:
            won = jobs_store.claim_job(url)
        except Exception as e:
            won = None
            res.append({"url": url, "ok": None, "error": type(e).__name__ + ": " + str(e)[:300]})
            continue
        if won:   # "apply", then publish the outcome right away (this releases the lock)
            jobs_store.update_job(url, status=js.SUBMITTED_UNCONFIRMED)
            SyncEngine().sync_now()
        res.append({"url": url, "ok": won, "status": jobs_store.get_job(url)["status"]})
    SyncEngine().sync_now()
    final = {u: j["status"] for u, j in jobs_store.read_jobs().items()}
    open(out, "w").write(json.dumps({"claims": res, "final": final}))
""")


def _race(world, n=8, devices=("laptop", "desktop")):
    script = world.tmp / "worker.py"
    script.write_text(WORKER)
    start = time.time() + 3
    procs = []
    for dev in devices:
        env = {**os.environ, "APPLYQUEUE_DATA_DIR": str(world.tmp / f"race-{dev}")}
        procs.append((dev, subprocess.Popen([sys.executable, str(script), str(BACKEND), str(world.remote), dev,
                                             str(world.tmp / f"{dev}.json"), str(start), str(n)], env=env)))
    for _, p in procs:
        assert p.wait(timeout=300) == 0
    return {dev: json.loads((world.tmp / f"{dev}.json").read_text()) for dev, _ in procs}


def test_two_devices_racing_for_the_same_jobs_apply_each_exactly_once(world):
    """Two OS processes (two devices) claim, "apply" and publish the same 8 jobs at the same instant.

    Each job must be applied by exactly one device, including the window after the winner has
    published "submitted" and released its lock (a free lock is not permission to apply again).
    """
    n = 8
    res = _race(world, n)
    errors = [r for d in res.values() for r in d["claims"] if r.get("ok") is None]
    assert not errors, errors          # a lost race is an answer, never an exception
    for i in range(n):
        winners = [dev for dev, d in res.items() if d["claims"][i]["ok"]]
        assert len(winners) == 1, (i, [d["claims"][i] for d in res.values()])
    rows = [json.loads(ln) for ln in world.remote_file(LEDGER).splitlines()]
    assert len({r["id"] for r in rows}) == len(rows)                 # union merge, no duplicates
    submitted: dict[str, set] = {}
    for r in rows:
        if r["event"] == "submitted":
            submitted.setdefault(r["job_key"], set()).add(r["device"])
    assert len(submitted) == n and all(len(v) == 1 for v in submitted.values()), submitted
    # one more sync each (a device that finished early has not seen the last outcomes yet):
    # both queues converge on "submitted" and no lock is left behind
    for dev in res:
        world.mp.setenv("APPLYQUEUE_DATA_DIR", str(world.tmp / f"race-{dev}"))
        assert SyncEngine().sync_now()["ok"]
    for dev in res:
        world.mp.setenv("APPLYQUEUE_DATA_DIR", str(world.tmp / f"race-{dev}"))
        assert SyncEngine().sync_now()["ok"]
        final = {u: j["status"] for u, j in jobs_store.read_jobs().items()}
        assert set(final.values()) == {js.SUBMITTED_UNCONFIRMED}, (dev, final)
    assert not [p for p in world.remote_tree() if p.startswith("locks/")]


# ── network stalls mid-push ──
def test_push_that_hangs_is_offline_and_resyncs(world, monkeypatch):
    a = world.use("laptop", kind="git")
    add_job(JOB_A)
    assert a.sync_now()["pushed"]
    hook = world.remote / "hooks" / "pre-receive"
    hook.write_text("#!/bin/sh\nsleep 30\nexit 1\n")   # a stalled link: the push never finishes
    hook.chmod(0o755)
    real = gitrepo.GitRepo.run

    def short(self, *args, **kw):
        if args and args[0] == "push":
            kw["timeout"] = 2
        return real(self, *args, **kw)

    monkeypatch.setattr(gitrepo.GitRepo, "run", short)
    jobs_store.update_job(JOB_A, status=js.SUBMITTED_UNCONFIRMED)
    t0 = time.monotonic()
    r = a.sync_now()
    assert time.monotonic() - t0 < 25
    assert r["offline"] and r["pending"] >= 1, r
    hook.unlink()
    r = a.sync_now()
    assert r["pushed"] and r["pending"] == 0, r
    assert '"event":"submitted"' in world.remote_file(LEDGER)


# ── a ledger left without its trailing newline ──
def test_append_after_truncated_ledger_line_keeps_both_events(world):
    a = world.use("laptop", kind="git")
    add_job(JOB_A)
    a.sync_now()
    p = store.repo_dir() / LEDGER
    p.write_text(p.read_text().rstrip("\n"))            # crash / hand edit dropped the newline
    before = len(a.read_ledger())
    jobs_store.update_job(JOB_A, status=js.SUBMITTED_UNCONFIRMED)
    assert a.sync_now()["pushed"]
    rows = a.read_ledger()
    assert len(rows) == before + 1 and rows[-1]["event"] == "submitted"
    b = world.use("desktop", kind="git")
    b.sync_now()
    assert jobs_store.read_jobs()[JOB_A]["status"] == js.SUBMITTED_UNCONFIRMED


def test_remote_ledger_without_trailing_newline_merges(world):
    a = world.use("laptop", kind="git")
    add_job(JOB_A)
    a.sync_now()
    other = world.tmp / "other"
    git("clone", "-q", str(world.remote), str(other), cwd=world.tmp)
    led = other / LEDGER
    row = {"id": "ext-1", "event": "submitted", "job_key": job_key(JOB_A), "url": JOB_A,
           "device": "device-other", "ts": "2026-10-25T10:00:00+00:00"}
    led.write_text(led.read_text() + json.dumps(row))   # no newline at EOF
    git("commit", "-qam", "other", cwd=other)
    git("push", "-q", "origin", "HEAD:main", cwd=other)
    jobs_store.update_job(JOB_A, notes="local edit")
    a.append_event(JOB_A, "claimed")
    assert a.sync_now()["pushed"]
    events = [r["event"] for r in a.read_ledger() if r.get("job_key") == job_key(JOB_A)]
    assert "submitted" in events and "claimed" in events


# ── visibility flipped to public right before a claim ──
def test_claim_while_repo_turned_public_pushes_nothing(world):
    a = world.use("laptop")
    add_job(JOB_A)
    assert a.sync_now()["pushed"]
    CredentialVault.default().get_or_create("boards.greenhouse.io", "alex@example.com")
    tree = world.remote_tree()
    world.gh.set_visibility(FULL, "public")
    # the claim still works locally (sync is advisory), but nothing reaches the public remote
    assert jobs_store.claim_job(JOB_A) is True
    assert world.remote_tree() == tree
    assert world.remote_file(f"locks/{job_key(JOB_A)}.json") is None
    assert world.remote_file("credentials/portals.json") is None
    assert a.status()["visibility_error"]


# ── very large browser sessions ──
def test_huge_session_stays_local_and_is_fast(world):
    a = world.use("laptop")
    a.sync_now()
    blob = "x" * (20 * 1024 * 1024)
    state = {"cookies": [{"domain": ".greenhouse.io", "name": "_s", "value": "v" * 40}],
             "origins": [{"origin": "https://boards.greenhouse.io",
                          "localStorage": [{"name": "cache", "value": blob}]}]}
    t0 = time.monotonic()
    r = a.save_session("default", state)
    assert time.monotonic() - t0 < 20
    assert r["saved"] == "local" and r["too_large"], r
    assert world.remote_file("sessions/default/storage.json") is None
    assert a.load_session("default")["cookies"][0]["name"] == "_s"
    small = {"cookies": state["cookies"], "origins": []}
    assert a.save_session("default", small)["saved"] == "repo"
