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


# ── a device with a clock far ahead ──
def test_lock_from_a_device_with_a_fast_clock_does_not_block_for_a_year(world, monkeypatch):
    from core.sync import engine as eng
    now = [1_800_000_000.0]
    monkeypatch.setattr(eng, "_clock", lambda: now[0])
    a = world.use("laptop", kind="git")
    a.sync_now()
    now[0] += 365 * 86400                                   # laptop's clock is a year ahead
    assert a.try_named_lock("search-s_1", 600)["ok"]        # ... and it dies mid-run
    now[0] -= 365 * 86400
    b = world.use("desktop", kind="git")
    assert b.try_named_lock("search-s_1", 600)["stale_recovered"]
    # a modest skew (a few minutes ahead) is still honoured
    now[0] += 120
    assert world.use("laptop", kind="git").try_named_lock("search-s_2", 600)["ok"]
    now[0] -= 120
    assert world.use("desktop", kind="git").try_named_lock("search-s_2", 600)["ok"] is False


# ── a timed-out git must not wait for its children ──
def test_timeout_kills_children_that_hold_the_output_pipes(tmp_path):
    """A stalled hook / ssh child keeps git's stdout open. The timeout must still return promptly
    (subprocess.run waits for those pipes on Windows) and must not leave the child running."""
    pidfile = tmp_path / "child.pid"
    child = f"import os,time; open({str(pidfile)!r},'w').write(str(os.getpid())); time.sleep(60)"
    parent = f"import subprocess,sys,time; subprocess.Popen([sys.executable,'-c',{child!r}]); time.sleep(60)"
    t0 = time.monotonic()
    with pytest.raises(subprocess.TimeoutExpired):
        gitrepo.run_killing_tree([sys.executable, "-c", parent], cwd=str(tmp_path), env=dict(os.environ), timeout=3)
    assert time.monotonic() - t0 < 20
    for _ in range(50):
        if pidfile.exists() and pidfile.read_text():
            break
        time.sleep(0.1)
    pid = int(pidfile.read_text())
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and _alive(pid):
        time.sleep(0.2)
    assert not _alive(pid), "the grandchild survived the timeout"


def _alive(pid: int) -> bool:
    if os.name == "nt":
        out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True).stdout
        return str(pid) in out
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    try:   # a zombie (killed, not yet reaped by init) is not running
        with open(f"/proc/{pid}/stat") as f:
            return f.read().split(")")[-1].split()[0] != "Z"
    except OSError:
        return True


# ── lock files are independent keys: a merge must never treat one as a rename of another ──
def test_merge_does_not_pair_a_released_lock_with_a_new_one_as_a_rename(tmp_path):
    """Lock files are near-identical JSON. With git's rename detection on, "device A released
    lock X and claimed lock Y" looks like a rename X -> Y, and device B's change to X is carried
    onto Y. Then a lock is resurrected or rewritten under the wrong job."""
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--quiet", "--bare", "-b", "main", str(remote)], check=True)
    a, b = gitrepo.GitRepo(tmp_path / "a"), gitrepo.GitRepo(tmp_path / "b")

    def lock(key, dev):
        return json.dumps({"job_key": key, "worker": f"{dev}-worker", "device": dev,
                           "claimed_at": "2026-10-26T10:00:00+00:00", "ttl_sec": 7200}, indent=2) + "\n"

    def write(repo, rel, text):
        p = repo.path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")

    a.path.mkdir()
    a.run("init", "--quiet", "-b", "main")
    a.run("remote", "add", "origin", str(remote))
    write(a, "locks/jobs.lever.co-1111111111111111.json", lock("jobs.lever.co-1111111111111111", "laptop"))
    a.commit_all("lock x")
    a.push()
    b.path.mkdir()
    b.run("init", "--quiet", "-b", "main")
    b.run("remote", "add", "origin", str(remote))
    b.fetch()
    assert b.merge_remote() == []
    # A: release X, claim Y (one push)
    (a.path / "locks/jobs.lever.co-1111111111111111.json").unlink()
    write(a, "locks/jobs.lever.co-2222222222222222.json", lock("jobs.lever.co-2222222222222222", "laptop"))
    a.commit_all("release x, lock y")
    a.push()
    # B: recovers X as stale (rewrites it with its own device) before seeing A's push
    write(b, "locks/jobs.lever.co-1111111111111111.json", lock("jobs.lever.co-1111111111111111", "desktop"))
    b.commit_all("stale lock x recovered")
    b.fetch()
    conflicts = b.merge_remote()
    y = b.path / "locks/jobs.lever.co-2222222222222222.json"
    if not conflicts:
        assert '"device": "laptop"' in y.read_text(encoding="utf-8")
    # Y must be A's lock exactly; the only conflict allowed is on X (modify/delete)
    assert conflicts in ([], ["locks/jobs.lever.co-1111111111111111.json"]), conflicts
    assert '"job_key": "jobs.lever.co-2222222222222222"' in y.read_text(encoding="utf-8")
    assert '"device": "laptop"' in y.read_text(encoding="utf-8")
