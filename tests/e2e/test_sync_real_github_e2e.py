"""Sync against a REAL private github.com repo (opt-in; skipped unless configured).

Everything else in the suite syncs against a local bare repo and a mock GitHub API. This file
drives the same SyncEngine against github.com itself: the real REST visibility check, real
HTTPS git with the token passed only through GIT_CONFIG_* env vars, real push races.

Run it only against a throwaway PRIVATE repo you own (it writes fictional "Alex Example" jobs,
portal logins and locks into it and never deletes anything):

    APPLYQUEUE_SYNC_E2E_REPO=<owner>/<throwaway-private-repo> \
    APPLYQUEUE_SYNC_E2E_TOKEN=<token with repo access> \
    uv run pytest tests/e2e/test_sync_real_github_e2e.py -v

The token is read from the environment, written only into this test's temporary data dirs
(0600, removed at the end) and never printed. Each run uses its own job URLs and lock names, so
the repo can be reused.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import textwrap
import time
import uuid
from pathlib import Path

import pytest

REPO = os.environ.get("APPLYQUEUE_SYNC_E2E_REPO", "").strip()
TOKEN = os.environ.get("APPLYQUEUE_SYNC_E2E_TOKEN", "").strip()

pytestmark = [
    pytest.mark.skipif(not (REPO and TOKEN), reason="set APPLYQUEUE_SYNC_E2E_REPO and APPLYQUEUE_SYNC_E2E_TOKEN"),
    pytest.mark.skipif(shutil.which("git") is None, reason="git not installed"),
]

from core import job_status as js  # noqa: E402
from core import jobs_store  # noqa: E402
from core.credential_vault import CredentialVault  # noqa: E402
from core.sync import engine as eng  # noqa: E402
from core.sync import github, store  # noqa: E402
from core.sync.engine import SyncEngine, job_key  # noqa: E402
from core.sync.gitrepo import GitRepo  # noqa: E402

BACKEND = Path(__file__).resolve().parents[2] / "backend"
RUN = time.strftime("%Y%m%d%H%M%S") + "-" + uuid.uuid4().hex[:6]
OWNER, NAME = (REPO.split("/", 1) + [""])[:2] if REPO else ("", "")
# Token-shaped but never valid (assembled so the privacy scan never sees a literal).
BAD_TOKEN = "ghp" + "_" + "0" * 36


def job_url(n: int | str) -> str:
    return f"https://boards.greenhouse.io/examplerobotics/jobs/e2e-{RUN}-{n}"


class World:
    def __init__(self, tmp: Path, mp):
        self.tmp, self.mp = tmp, mp
        self.observer = GitRepo(tmp / "observer", TOKEN)

    def use(self, dev: str, token: str = TOKEN, **cfg) -> SyncEngine:
        d = self.tmp / dev
        self.mp.setenv("APPLYQUEUE_DATA_DIR", str(d))
        if not (d / "sync" / "config.json").exists():
            github.save_pat(token)
            # Same steps as POST /sync/repo with create=false (adopt an existing repo).
            if token == TOKEN:
                assert github.current_user()["login"].lower() == OWNER.lower()
                assert github.get_repo(OWNER, NAME) is not None
                assert github.verify_private(OWNER, NAME)["private"] is True
            store.save_config({**store.DEFAULT_CONFIG, "enabled": True, "kind": "github", "owner": OWNER,
                               "repo": NAME, "remote_url": "", "device_name": dev, **cfg})
        elif cfg:
            store.save_config({**store.load_config(), **cfg})
        return SyncEngine()

    def remote_file(self, rel: str) -> str | None:
        if not self.observer.exists():
            self.observer.clone(github.https_remote(OWNER, NAME))
        self.observer.fetch()
        p = self.observer.run("show", f"origin/main:{rel}", check=False)
        return p.stdout if p.returncode == 0 else None

    def ledger_events(self) -> set[tuple[str, str]]:
        rows = [json.loads(x) for x in (self.remote_file("ledger/applications.jsonl") or "").splitlines() if x.strip()]
        return {(r["job_key"], r["event"]) for r in rows}

    def cleanup_tokens(self):
        for f in self.tmp.rglob("github_token.json"):
            f.unlink()


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.delitem(sys.modules, "core.shared_config", raising=False)
    w = World(tmp_path, monkeypatch)
    yield w
    w.cleanup_tokens()


def add_job(url, status=js.PENDING):
    jobs_file, _ = jobs_store._paths()
    jobs = jobs_store._load(jobs_file)
    jobs[url] = {"url": url, "title": "Robotics Engineer", "company": "Example Robotics", "status": status}
    jobs_store._save(jobs_file, jobs)


def test_repo_is_private_and_the_app_repo_is_refused():
    data = github.httpx.get(f"{github.API}/repos/{OWNER}/{NAME}", headers=github._headers(TOKEN), timeout=20).json()
    assert data["private"] is True and data["visibility"] == "private", "the e2e repo must be private"
    for owner, repo in github.FORBIDDEN_REPOS:      # the app's own repos are never used for state
        with pytest.raises(github.NotPrivate):
            github._check_name(owner, repo)


def test_bootstrap_push_pull_and_concurrent_edits(world):
    a = world.use("laptop")
    add_job(job_url(1))
    ra = a.sync_now()
    assert ra["ok"] and ra["offline"] is False, ra
    assert "must stay private" in world.remote_file("README.md")
    assert world.remote_file(".gitattributes").strip() == "*.jsonl merge=union"
    assert (job_key(job_url(1)), "discovered") in world.ledger_events() or \
        job_key(job_url(1)) in (world.remote_file("ledger/applications.jsonl") or "")

    b = world.use("desktop")       # second device: a fresh clone of the real repo
    add_job(job_url(2))
    rb = b.sync_now()
    assert rb["ok"] and job_url(1) in jobs_store.read_jobs()

    # Concurrent edits: both change state without pulling; desktop pushes first, laptop's push is
    # rejected by github.com and must merge and retry.
    world.use("laptop")
    jobs_store.update_job(job_url(1), status=js.SUBMITTED_UNCONFIRMED)
    world.use("desktop")
    jobs_store.update_job(job_url(2), status=js.NEEDS_HUMAN)
    assert world.use("desktop").sync_now()["pushed"]
    r = world.use("laptop").sync_now()
    assert r["pushed"] and r["pending"] == 0, r
    ev = world.ledger_events()
    assert (job_key(job_url(1)), "submitted") in ev and (job_key(job_url(2)), "needs_human") in ev
    assert jobs_store.get_job(job_url(2))["status"] == js.NEEDS_HUMAN
    world.use("desktop").sync_now()
    assert jobs_store.get_job(job_url(1))["status"] == js.SUBMITTED_UNCONFIRMED


def test_credential_conflict_last_writer_wins_and_merges(world):
    portal = f"e2e-{RUN}.greenhouse.example"
    world.use("laptop").sync_now()
    world.use("desktop").sync_now()
    # Both devices create a login for the same portal before either syncs: a real merge conflict
    # on credentials/portals.json.
    world.use("laptop")
    pa, _ = CredentialVault.default().get_or_create(portal, "alex@example.com")
    world.use("desktop")
    time.sleep(1.1)
    pb, _ = CredentialVault.default().get_or_create(portal, "alex@example.com")
    other = f"other-{RUN}.lever.example"
    CredentialVault.default().get_or_create(other, "alex@example.com")
    assert world.use("laptop").sync_now()["ok"]
    rd = world.use("desktop").sync_now()
    assert rd["ok"] and rd["pending"] == 0, rd
    world.use("laptop").sync_now()
    va = CredentialVault.default()
    assert va.get(other), "the desktop-only login reached the laptop"
    world.use("desktop")
    vb = CredentialVault.default()
    assert va.get(portal)["password"] == vb.get(portal)["password"] == pb["password"]   # newer wins
    assert pa["password"] != pb["password"]
    assert portal in (world.remote_file("credentials/portals.json") or "")


def test_lock_contention_takeover_and_release(world, monkeypatch):
    url = job_url("lock")
    a = world.use("laptop")
    add_job(url)
    a.sync_now()
    assert a.try_lock(url)["ok"]
    assert json.loads(world.remote_file(f"locks/{job_key(url)}.json"))["worker"] == "laptop"
    b = world.use("desktop")
    b.sync_now()
    res = b.try_lock(url)
    assert res["ok"] is False and res["reason"] == "locked" and res["holder"] == "laptop"

    # Named (search) lock: heartbeat, takeover after the holder stops beating, the old holder's
    # renew is refused, then the new holder releases it.
    now = [time.time()]
    monkeypatch.setattr(eng, "_clock", lambda: now[0])
    name = f"search-e2e-{RUN}"
    assert world.use("laptop").try_named_lock(name, 30)["ok"]
    now[0] += 20
    assert world.use("laptop").renew_named(name, 30)["ok"]
    now[0] += 20
    assert world.use("desktop").try_named_lock(name, 600)["ok"] is False     # still alive
    now[0] += 40
    assert world.use("desktop").try_named_lock(name, 600)["stale_recovered"]
    assert world.use("laptop").renew_named(name, 30) == {"ok": False, "reason": "taken_over", "holder": "desktop"}
    assert json.loads(world.remote_file(f"locks/{name}.json"))["worker"] == "desktop"
    assert world.use("desktop").release_named(name)
    assert world.remote_file(f"locks/{name}.json") is None

    # Job lock: the laptop finishes the job; its next sync releases the lock on github.com.
    world.use("laptop")
    jobs_store.update_job(url, status=js.SUBMITTED_UNCONFIRMED)
    assert world.use("laptop").sync_now()["released"] == 1
    assert world.remote_file(f"locks/{job_key(url)}.json") is None


def test_offline_then_reconnect(world, monkeypatch):
    url = job_url("offline")
    a = world.use("laptop")
    add_job(url)
    a.sync_now()
    # Network down: every HTTPS connection (REST and git) goes to a closed local port.
    for k in ("HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy"):
        monkeypatch.setenv(k, "http://127.0.0.1:9")
    monkeypatch.delenv("NO_PROXY", raising=False)
    monkeypatch.delenv("no_proxy", raising=False)
    jobs_store.update_job(url, status=js.SUBMITTED_UNCONFIRMED)
    r = world.use("laptop").sync_now()
    assert r["offline"] and r["pending"] >= 1, r
    off = world.use("laptop").try_lock(job_url("offline-claim"))
    assert off.get("offline") is True, off
    for k in ("HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy"):
        monkeypatch.delenv(k)
    r = world.use("laptop").sync_now()
    assert r["pushed"] and r["pending"] == 0, r
    assert (job_key(url), "submitted") in world.ledger_events()


def test_token_without_access_is_refused_cleanly(world):
    """A token GitHub rejects: no crash, a clear message, nothing pushed, the app repo untouched."""
    world.use("laptop").sync_now()   # the real repo exists with content
    e = world.use("intruder", token=BAD_TOKEN)
    with pytest.raises(github.GitHubError, match="rejected the token"):
        github.current_user()
    before = world.remote_file("ledger/applications.jsonl")
    add_job(job_url("intruder"))
    r = e.sync_now()
    assert r["ok"] is False, r
    assert "token" in json.dumps(r).lower(), r
    assert world.remote_file("ledger/applications.jsonl") == before
    st = e.status()
    assert st["github_linked"] is True and BAD_TOKEN not in json.dumps(st)
    assert BAD_TOKEN not in json.dumps(r) and TOKEN not in json.dumps(r)


WORKER = textwrap.dedent(r"""
    import json, os, sys, time
    sys.path.insert(0, sys.argv[1])
    from core import job_status as js, jobs_store
    from core.sync import store
    from core.sync.engine import SyncEngine
    owner, name, dev, out, start, run = sys.argv[2:8]
    store.save_token({"access_token": os.environ["APPLYQUEUE_SYNC_E2E_TOKEN"], "source": "pat"})
    store.save_config({**store.DEFAULT_CONFIG, "enabled": True, "kind": "github", "owner": owner,
                       "repo": name, "device_name": dev})
    urls = [f"https://jobs.lever.co/examplerobotics/race-{run}-{i}" for i in range(4)]
    jobs_file, _ = jobs_store._paths()
    jobs_store._save(jobs_file, {u: {"url": u, "title": "Robotics Engineer", "company": "Example Robotics",
                                     "status": js.PENDING} for u in urls})
    SyncEngine().sync_now()
    while time.time() < float(start):
        time.sleep(0.005)
    res = []
    for url in urls:
        won = jobs_store.claim_job(url)
        if won:
            jobs_store.update_job(url, status=js.SUBMITTED_UNCONFIRMED)
            SyncEngine().sync_now()
        res.append({"url": url, "ok": won})
    SyncEngine().sync_now()
    store.clear_token()
    open(out, "w").write(json.dumps(res))
""")


def test_two_parallel_devices_claim_each_job_exactly_once(world):
    """Two OS processes (two devices, separate data dirs) race for the same 4 jobs on github.com."""
    script = world.tmp / "worker.py"
    script.write_text(WORKER)
    start = time.time() + 20      # both have cloned and synced by then
    procs = []
    for dev in ("race-laptop", "race-desktop"):
        env = {**os.environ, "APPLYQUEUE_DATA_DIR": str(world.tmp / dev), "APPLYQUEUE_SYNC_E2E_TOKEN": TOKEN}
        procs.append((dev, subprocess.Popen([sys.executable, str(script), str(BACKEND), OWNER, NAME, dev,
                                             str(world.tmp / f"{dev}.json"), str(start), RUN], env=env)))
    for _, p in procs:
        assert p.wait(timeout=600) == 0
    out = {dev: json.loads((world.tmp / f"{dev}.json").read_text()) for dev, _ in procs}
    for i in range(4):
        winners = [dev for dev in out for row in out[dev] if row["ok"] and row["url"].endswith(f"-{i}")]
        assert len(winners) == 1, (i, out)
    ev = world.ledger_events()
    keys = {job_key(f"https://jobs.lever.co/examplerobotics/race-{RUN}-{i}") for i in range(4)}
    assert keys <= {k for k, e in ev if e == "submitted"}, ev


def test_token_that_cannot_see_the_repo_is_refused(world):
    """A valid token pointed at a repo it cannot see (GitHub answers 404 for both "missing" and
    "no access" on private repos): refused with a clear message, nothing is created."""
    missing = f"applyqueue-sync-e2e-no-access-{RUN}"
    world.use("stranger", repo=missing)
    r = SyncEngine().sync_now()
    assert r["ok"] is False and "Link GitHub again" in r["auth_error"], r
    assert github.get_repo(OWNER, missing) is None


def test_the_token_never_lands_in_the_repo_history(world):
    world.use("laptop").sync_now()
    world.remote_file("README.md")
    log = world.observer.run("log", "-p", "--all").stdout
    assert TOKEN not in log and "x-access-token" not in log
