"""M5: private GitHub sync repo — against a local bare remote and a mocked GitHub API."""
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "fixtures"))
from mock_github import FAKE_TOKEN, MockGitHub  # noqa: E402

from core import job_status as js  # noqa: E402
from core import jobs_store, token_guard  # noqa: E402
from core.credential_vault import CredentialVault  # noqa: E402
from core.sync import crypto, github, gitrepo, store  # noqa: E402
from core.sync.engine import LEDGER, SyncEngine, job_key, sanitize_storage_state  # noqa: E402

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")

FULL = "alex-example/applyqueue-state"
JOB_A = "https://boards.greenhouse.io/examplerobotics/jobs/1"
JOB_B = "https://jobs.lever.co/acmewidgets/2"


def git(*args, cwd):
    return subprocess.run(["git", "-c", "init.defaultBranch=main", *args], cwd=cwd, check=True,
                          capture_output=True, text=True,
                          env={**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
                               "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com"}).stdout


class World:
    def __init__(self, tmp: Path, monkeypatch, gh: MockGitHub):
        self.tmp, self.mp, self.gh = tmp, monkeypatch, gh
        self.remote = tmp / "remote.git"
        git("init", "--bare", "-q", "-b", "main", str(self.remote), cwd=tmp)
        seed = tmp / "seed"
        git("clone", "-q", str(self.remote), str(seed), cwd=tmp)
        (seed / "README.md").write_text("# applyqueue-state\n")
        git("add", "-A", cwd=seed)
        git("commit", "-q", "-m", "init", cwd=seed)
        git("push", "-q", "origin", "HEAD:main", cwd=seed)
        gh.repos[FULL] = {"name": "applyqueue-state", "private": True, "visibility": "private", "fork": False,
                          "permissions": {"push": True}}

    def use(self, dev: str, **cfg):
        d = self.tmp / dev
        self.mp.setenv("APPLYQUEUE_DATA_DIR", str(d))
        if not (d / "sync" / "config.json").exists():
            store.save_config({**store.DEFAULT_CONFIG, "enabled": True, "owner": "alex-example",
                               "repo": "applyqueue-state", "remote_url": str(self.remote),
                               "device_name": dev, **cfg})
            store.save_token({"access_token": FAKE_TOKEN, "source": "test"})
        elif cfg:
            store.save_config({**store.load_config(), **cfg})
        return SyncEngine()

    def remote_file(self, rel: str) -> str | None:
        out = subprocess.run(["git", "--git-dir", str(self.remote), "show", f"main:{rel}"],
                             capture_output=True, text=True)
        return out.stdout if out.returncode == 0 else None

    def remote_tree(self) -> list[str]:
        return subprocess.run(["git", "--git-dir", str(self.remote), "ls-tree", "-r", "--name-only", "main"],
                              capture_output=True, text=True, check=True).stdout.split()


@pytest.fixture
def world(tmp_path, monkeypatch):
    gh = MockGitHub()
    monkeypatch.setattr(github, "WEB", gh.url)
    monkeypatch.setattr(github, "API", gh.url)
    monkeypatch.delitem(sys.modules, "core.shared_config", raising=False)
    yield World(tmp_path, monkeypatch, gh)
    gh.close()


def add_job(url, status=js.PENDING, **kw):
    jobs_file, lock = jobs_store._paths()
    jobs = jobs_store._load(jobs_file)
    jobs[url] = {"url": url, "title": "Robotics Engineer", "company": "Example Robotics", "status": status, **kw}
    jobs_store._save(jobs_file, jobs)


# ── GitHub device flow + repo ──
def test_device_flow_and_private_repo(world, tmp_path, monkeypatch):
    monkeypatch.setenv("APPLYQUEUE_DATA_DIR", str(tmp_path / "fresh"))
    with pytest.raises(github.GitHubError):
        github.start_device_flow("")
    flow = github.start_device_flow("Iv1.example")
    assert flow["user_code"] == "ABCD-1234"
    assert github.poll_device_flow("Iv1.example", flow["device_code"])["status"] == "pending"
    assert github.poll_device_flow("Iv1.example", flow["device_code"])["status"] == "ok"
    tf = store.token_file()
    assert json.loads(tf.read_text())["access_token"] == FAKE_TOKEN
    if os.name != "nt":
        assert (tf.stat().st_mode & 0o777) == 0o600
    assert github.current_user()["login"] == "alex-example"
    repo = github.create_private_repo("applyqueue-state-2")
    assert repo["private"] is True
    assert github.verify_private("alex-example", "applyqueue-state-2")["private"] is True
    world.gh.set_visibility("alex-example/applyqueue-state-2", "public")
    with pytest.raises(github.NotPrivate):
        github.verify_private("alex-example", "applyqueue-state-2")
    world.gh.set_visibility("alex-example/applyqueue-state-2", "internal")
    with pytest.raises(github.NotPrivate):
        github.verify_private("alex-example", "applyqueue-state-2")
    with pytest.raises(github.NotPrivate):
        github.verify_private("JonathanLiu1401", "ApplyQueue")  # the app repo is never a state repo


def test_device_flow_denied(world, tmp_path, monkeypatch):
    monkeypatch.setenv("APPLYQUEUE_DATA_DIR", str(tmp_path / "fresh"))
    world.gh.deny = True
    assert github.poll_device_flow("Iv1.example", "dev-123")["status"] == "denied"
    assert not store.token_file().exists()


# ── layout, ledger, union merge ──
def test_layout_and_union_merge_two_devices(world):
    a = world.use("laptop")
    add_job(JOB_A)
    ra = a.sync_now()
    assert ra["ok"] and ra["pushed"]
    assert world.remote_file(".gitattributes").strip() == "*.jsonl merge=union"
    assert "must stay private" in world.remote_file("README.md")

    b = world.use("desktop")
    add_job(JOB_B)
    rb = b.sync_now()
    assert rb["imported"]["added"] == 1           # desktop received the laptop's posting
    assert JOB_A in jobs_store.read_jobs()

    # Both append concurrently: laptop commits without pulling, desktop pushes first.
    world.use("laptop")
    jobs_store.update_job(JOB_A, status=js.SUBMITTED_UNCONFIRMED)
    world.use("desktop")
    jobs_store.update_job(JOB_B, status=js.NEEDS_HUMAN)
    world.use("desktop").sync_now()
    r = world.use("laptop").sync_now()
    assert r["pushed"]
    ledger = [json.loads(ln) for ln in world.remote_file("ledger/applications.jsonl").splitlines()]
    events = {(e["job_key"], e["event"]) for e in ledger}
    assert (job_key(JOB_A), "submitted") in events and (job_key(JOB_B), "needs_human") in events
    assert len({e["id"] for e in ledger}) == len(ledger)
    # laptop now sees the desktop's needs_human, and vice versa after its next sync
    assert jobs_store.get_job(JOB_B)["status"] == js.NEEDS_HUMAN
    world.use("desktop").sync_now()
    assert jobs_store.get_job(JOB_A)["status"] == js.SUBMITTED_UNCONFIRMED
    assert {"workers/" + store.device_id() + ".json"} <= set(world.remote_tree())


def test_import_never_downgrades(world):
    a = world.use("laptop")
    add_job(JOB_A, status=js.CONFIRMED)
    a.sync_now()
    b = world.use("desktop")
    add_job(JOB_A, status=js.PENDING)
    b.sync_now()
    assert jobs_store.get_job(JOB_A)["status"] == js.CONFIRMED
    world.use("laptop")
    jobs_store.update_job(JOB_A, status=js.PENDING)  # hand edit; remote state stays confirmed
    b.sync_now()
    assert jobs_store.get_job(JOB_A)["status"] == js.CONFIRMED


def test_job_key_ignores_tracking():
    assert job_key(JOB_A + "?utm_source=x&gh_src=y") == job_key(JOB_A) == job_key(JOB_A.replace("https://", "https://www.") + "/")
    assert job_key(JOB_A) != job_key(JOB_B)
    assert job_key("https://example.com/viewjob?jk=1") != job_key("https://example.com/viewjob?jk=2")


# ── locks ──
def test_lock_contention_and_claim_hook(world):
    a = world.use("laptop")
    add_job(JOB_A)
    a.sync_now()
    assert jobs_store.claim_job(JOB_A) is True      # goes through the sync lock
    lock = json.loads(world.remote_file(f"locks/{job_key(JOB_A)}.json"))
    assert lock["worker"] == "laptop" and lock["ttl_sec"] == 7200

    world.use("desktop").sync_now()
    assert jobs_store.get_job(JOB_A)["status"] == js.PENDING
    res = SyncEngine().try_lock(JOB_A)
    assert res == {"ok": False, "reason": "locked", "holder": "laptop", "until": lock["claimed_at"]}
    assert jobs_store.claim_job(JOB_A) is False
    job = jobs_store.get_job(JOB_A)
    assert job["status"] == js.PENDING and job["remote_claimed_until"]
    assert jobs_store.claim_next_pending() is None   # backed off, not re-tried in a loop


def test_lock_race_lost_on_push(world):
    world.use("laptop").sync_now()
    b = world.use("desktop")
    b.sync_now()
    assert world.use("laptop").try_lock(JOB_A)["ok"]
    b = world.use("desktop")
    real_pull = b._pull
    calls = {"n": 0}

    def stale_pull(repo):  # desktop does not see the laptop's lock before writing its own
        calls["n"] += 1
        return [] if calls["n"] == 1 else real_pull(repo)
    b._pull = stale_pull
    res = b.try_lock(JOB_A)
    assert res["ok"] is False and res["reason"] == "lost_race"
    assert json.loads(world.remote_file(f"locks/{job_key(JOB_A)}.json"))["worker"] == "laptop"


def test_stale_lock_recovery_and_release(world):
    a = world.use("laptop", lock_ttl_s=1)
    add_job(JOB_A)
    a.sync_now()
    assert a.try_lock(JOB_A)["ok"]
    time.sleep(1.2)
    b = world.use("desktop")
    res = b.try_lock(JOB_A)
    assert res["ok"] and res["stale_recovered"]
    assert json.loads(world.remote_file(f"locks/{job_key(JOB_A)}.json"))["worker"] == "desktop"
    # finishing the job releases the lock on the next sync
    jobs_store.update_job(JOB_A, status=js.SUBMITTED_UNCONFIRMED)
    assert b.sync_now()["released"] == 1
    assert world.remote_file(f"locks/{job_key(JOB_A)}.json") is None


# ── offline queueing ──
def test_offline_queue_and_resync(world):
    a = world.use("laptop")
    add_job(JOB_A)
    a.sync_now()
    hidden = world.remote.with_name("remote.offline")
    world.remote.rename(hidden)
    try:
        jobs_store.update_job(JOB_A, status=js.SUBMITTED_UNCONFIRMED)
        r = a.sync_now()
        assert r["offline"] and r["pending"] >= 1
        assert a.try_lock(JOB_B)["offline"] is True  # offline claims are allowed and queued
        assert a.status()["pending"] >= 2
    finally:
        hidden.rename(world.remote)
    r = a.sync_now()
    assert r["pushed"] and r["pending"] == 0
    assert "submitted" in world.remote_file("ledger/applications.jsonl")


def test_first_run_offline_then_publish(world):
    hidden = world.remote.with_name("remote.offline")
    world.remote.rename(hidden)
    a = world.use("laptop")
    add_job(JOB_A)
    assert a.sync_now()["offline"]
    hidden.rename(world.remote)
    assert a.sync_now()["pushed"]
    assert "must stay private" in world.remote_file("README.md")
    assert world.remote_file("ledger/applications.jsonl")


# ── visibility gate ──
def test_public_repo_refuses_push_and_credentials(world):
    a = world.use("laptop")
    vault = CredentialVault.default()
    vault.get_or_create("boards.greenhouse.io", "alex@example.com")
    world.gh.set_visibility(FULL, "public")
    before = world.gh.visibility_checks(FULL)
    r = a.sync_now()
    assert r["ok"] is False and "public" in r["refused"]
    assert "public" in r["credentials"]["refused"]
    assert world.gh.visibility_checks(FULL) >= before + 2   # credentials write + push both checked
    assert world.remote_file("credentials/portals.json") is None
    assert world.remote_file("ledger/applications.jsonl") is None   # nothing pushed at all
    assert a.status()["visibility_error"]
    assert not (store.repo_dir() / "credentials" / "portals.json").exists()
    world.gh.set_visibility(FULL, "private")
    assert a.sync_now()["pushed"]
    assert world.remote_file("credentials/portals.json")


def test_plain_git_remote_never_gets_credentials(world):
    a = world.use("laptop", kind="git")
    CredentialVault.default().get_or_create("boards.greenhouse.io", "alex@example.com")
    r = a.sync_now()
    assert r["pushed"] and "credentials" not in r
    assert world.remote_file("credentials/portals.json") is None
    assert a.save_session("default", {"cookies": []})["saved"] == "local"


# ── credentials: LWW merge, mirror, encryption ──
def test_credentials_mirror_and_lww_conflict(world):
    a = world.use("laptop")
    va = CredentialVault.default()
    pa, _ = va.get_or_create("boards.greenhouse.io", "alex@example.com")
    a.sync_now()
    b = world.use("desktop")
    b.sync_now()
    vb = CredentialVault.default()
    assert vb.get("boards.greenhouse.io")["password"] == pa["password"]
    assert vb.get("boards.greenhouse.io")["source"] == "sync"

    # concurrent edits: laptop changes greenhouse (newer), desktop adds lever
    world.use("laptop")
    va.upsert_synced({"boards.greenhouse.io": {"username": "alex@example.com", "password": "New-Pass-1!",
                                               "updated_at": "2099-01-01T00:00:00+00:00"}})
    world.use("desktop")
    vb.get_or_create("jobs.lever.co", "alex@example.com")
    world.use("laptop").sync_now()
    world.use("desktop").sync_now()   # rejected push -> portals.json merge conflict -> LWW per id
    portals = {e["id"]: e for e in json.loads(world.remote_file("credentials/portals.json"))}
    assert portals["boards.greenhouse.io"]["password"] == "New-Pass-1!"
    assert "jobs.lever.co" in portals
    assert vb.get("boards.greenhouse.io")["password"] == "New-Pass-1!"
    world.use("laptop").sync_now()
    assert va.get("jobs.lever.co")


def test_optional_encryption(world):
    a = world.use("laptop", encrypt_credentials=True)
    CredentialVault.default().get_or_create("boards.greenhouse.io", "alex@example.com")
    pw = CredentialVault.default().get("boards.greenhouse.io")["password"]
    a.sync_now()
    key = crypto.export_key()
    assert world.remote_file("credentials/portals.json") is None
    blob = world.remote_file("credentials/portals.json.enc")
    assert blob and pw not in blob and "alex@example.com" not in blob

    b = world.use("desktop", encrypt_credentials=True)
    r = b.sync_now()   # no key yet: credentials error, rest of sync still works
    assert "sync key" in r["credentials"]["error"] and r["pushed"]
    crypto.import_key(key)
    b.sync_now()
    assert CredentialVault.default().get("boards.greenhouse.io")["password"] == pw


def test_encryption_off_by_default():
    assert store.DEFAULT_CONFIG["encrypt_credentials"] is False
    assert store.DEFAULT_CONFIG["sync_credentials"] is True


# ── sessions + token guard ──
def test_session_sanitized_and_synced(world):
    a = world.use("laptop")
    a.sync_now()
    jwt = "eyJ" + "a" * 12 + "." + "b" * 12 + "." + "c" * 12
    state = {"cookies": [{"domain": ".greenhouse.io", "name": "_session", "value": jwt},
                         {"domain": ".google.com", "name": "SID", "value": "x"},
                         {"domain": "chatgpt.com", "name": "s", "value": "y"},
                         {"domain": "login.microsoftonline.com", "name": "e", "value": "z"}],
             "origins": [{"origin": "https://boards.greenhouse.io", "localStorage": [{"name": "access_token", "value": "p" * 20}]},
                         {"origin": "https://claude.ai", "localStorage": []}]}
    r = a.save_session("default", state)
    assert r["saved"] == "repo" and r["cookies"] == 1
    saved = json.loads(world.remote_file("sessions/default/storage.json"))
    assert [c["domain"] for c in saved["cookies"]] == [".greenhouse.io"]
    assert [o["origin"] for o in saved["origins"]] == ["https://boards.greenhouse.io"]
    b = world.use("desktop")
    b.sync_now()
    assert b.load_session("default")["cookies"][0]["value"] == jwt


def test_session_with_vendor_key_refused(world):
    a = world.use("laptop")
    leaked = "sk-" + "proj-" + "Q" * 30
    with pytest.raises(token_guard.SyncSecretError):
        a.save_session("default", {"cookies": [{"domain": "jobs.example.com", "name": "k", "value": leaked}]})


def test_ledger_rejects_tokens_and_github_token_never_in_repo(world):
    a = world.use("laptop")
    a.ensure_repo()
    with pytest.raises(token_guard.SyncSecretError):
        a.append_event(JOB_A, "queued", {"title": "x " + "xai-" + "Z" * 24})
    codex = json.dumps({"tokens": {"id_token": "a", "access_token": "b"}, "last_refresh": "now"})
    with pytest.raises(token_guard.SyncSecretError):
        token_guard.safe_sync_write(store.repo_dir(), "notes/auth-copy.json", codex)
    for name in ("github_token.json", "sync_key.json", "site_credentials.json", "email_tokens.json"):
        with pytest.raises(token_guard.SyncSecretError):
            token_guard.safe_sync_write(store.repo_dir(), f"x/{name}", "{}")
    add_job(JOB_A)
    a.sync_now()
    for p in store.repo_dir().rglob("*"):
        if p.is_file():
            assert FAKE_TOKEN not in p.read_text(encoding="utf-8", errors="replace"), p


def test_sanitize_storage_state_drops_mail_and_ai():
    s = sanitize_storage_state({"cookies": [{"domain": "mail.google.com"}, {"domain": "x.ai"},
                                            {"domain": "myworkdayjobs.com"}], "origins": []})
    assert s["cookies"] == [{"domain": "myworkdayjobs.com"}]


# ── M7: scheduled searches over sync ──
def test_named_search_lock_single_runner_and_survives_sync(world):
    a = world.use("laptop")
    a.sync_now()
    assert a.try_named_lock("search-s_1", 600)["ok"]
    assert json.loads(world.remote_file("locks/search-s_1.json"))["worker"] == "laptop"
    assert a.sync_now()["released"] == 0                    # not a job lock: sync keeps it
    assert world.remote_file("locks/search-s_1.json")
    b = world.use("desktop")
    res = b.try_named_lock("search-s_1", 600)
    assert res["ok"] is False and res["holder"] == "laptop"
    assert b.release_named("search-s_1") is False            # cannot release someone else's
    assert world.use("laptop").release_named("search-s_1") is True
    assert world.remote_file("locks/search-s_1.json") is None
    assert world.use("desktop").try_named_lock("search-s_1", 600)["ok"]
    with pytest.raises(ValueError):
        b.try_named_lock("../x", 1)


def test_schedules_exported_and_remote_runs_and_agent_postings(world):
    from core.searches import schedule, store as search_store
    a = world.use("laptop")
    s = search_store.upsert_search({"titles": ["Robotics Engineer"], "mode": "review",
                                    "schedule": {"kind": "daily", "time": "09:00", "tz": "UTC"}})
    out = a.sync_now()
    assert out["schedules"]["scheduled"] == 1
    exp = json.loads(world.remote_file("schedules/searches.json"))
    assert exp["searches"][0]["id"] == s["id"] and exp["searches"][0]["cron"] == "0 9 * * *"
    # an always-on agent ran the search and pushed a posting + a run row
    bot = world.tmp / "bot"
    git("clone", "-q", str(world.remote), str(bot), cwd=world.tmp)
    (bot / "queue" / "postings").mkdir(parents=True, exist_ok=True)
    with open(bot / "queue" / "postings" / "2026-03.jsonl", "a") as f:
        f.write(json.dumps({"job_key": job_key(JOB_B), "url": JOB_B, "title": "Robotics Engineer",
                            "company": "Acme Widgets", "search_id": s["id"], "device": "bot"}) + "\n")
    (bot / "schedules").mkdir(exist_ok=True)
    started = "2099-01-01T09:00:00+00:00"
    with open(bot / "schedules" / "runs.jsonl", "a") as f:
        f.write(json.dumps({"search_id": s["id"], "device": "bot", "started_at": started, "outcome": "ok"}) + "\n")
    git("add", "-A", cwd=bot)
    git("commit", "-q", "-m", "bot run", cwd=bot)
    git("push", "-q", "origin", "HEAD:main", cwd=bot)
    out = world.use("laptop").sync_now()
    assert out["schedules"]["imported_runs"] == 1
    st = schedule.load_state()[s["id"]]
    assert st["last_remote_run"].startswith("2099-01-01T09:00")
    job = jobs_store.get_job(JOB_B)
    assert job["search_id"] == s["id"] and job["review"] == "pending"   # review-first search
    # our own runs go to runs.jsonl once
    schedule.begin(s["id"], a.device)
    schedule.finish(s["id"], "ok", device=a.device)
    assert world.use("laptop").sync_now()["schedules"]["exported_runs"] == 1
    assert world.use("laptop").sync_now()["schedules"]["exported_runs"] == 0
    rows = [json.loads(x) for x in world.remote_file("schedules/runs.jsonl").splitlines()]
    assert {r["device"] for r in rows} == {"bot", a.device}
    assert "token" not in world.remote_file("schedules/searches.json").lower()


def test_named_lock_heartbeat_and_takeover(world, monkeypatch):
    # A fake lock clock: slow CI runners (git push/pull can take seconds) made a sleep-based
    # version of this test flaky on Windows and the Intel Mac.
    from core.sync import engine as eng
    now = [1_800_000_000.0]
    monkeypatch.setattr(eng, "_clock", lambda: now[0])
    a = world.use("laptop")
    a.sync_now()
    assert a.try_named_lock("search-s_1", 2)["ok"]
    now[0] += 1.5
    assert world.use("laptop").renew_named("search-s_1", 2)["ok"]      # heartbeat pushes a fresh claim
    now[0] += 1.5                                                    # 3 s after the claim, 1.5 s after the beat
    assert world.use("desktop").try_named_lock("search-s_1", 600)["ok"] is False   # still alive
    assert json.loads(world.remote_file("locks/search-s_1.json"))["renewed"] == 1
    now[0] += 2.5                                                    # beats stopped (laptop asleep)
    assert world.use("desktop").try_named_lock("search-s_1", 600)["stale_recovered"]
    res = world.use("laptop").renew_named("search-s_1", 2)           # laptop wakes up
    assert res == {"ok": False, "reason": "taken_over", "holder": "desktop"}
    assert json.loads(world.remote_file("locks/search-s_1.json"))["worker"] == "desktop"


def test_schedule_lists_merge_across_devices(world):
    from core.searches import store as search_store
    world.use("laptop")
    sa = search_store.upsert_search({"titles": ["Data Analyst"], "schedule": {"kind": "hourly", "tz": "UTC"}})
    world.use("laptop").sync_now()
    world.use("desktop")
    sb = search_store.upsert_search({"titles": ["Nurse"], "schedule": {"kind": "daily", "time": "08:00", "tz": "UTC"}})
    world.use("desktop").sync_now()
    rows = json.loads(world.remote_file("schedules/searches.json"))["searches"]
    assert sorted((r["id"], r["device"] != "") for r in rows) == sorted([(sa["id"], True), (sb["id"], True)])
    # the laptop turns its schedule off: only its entry goes, the desktop's stays
    world.use("laptop")
    search_store.upsert_search({"id": sa["id"], "schedule": {"kind": "off"}})
    world.use("laptop").sync_now()
    assert [r["id"] for r in json.loads(world.remote_file("schedules/searches.json"))["searches"]] == [sb["id"]]


# ── M8 break-it: the remote's history is rewritten under us ──
def _remote_text(world) -> str:
    return "\n".join(world.remote_file(p) or "" for p in world.remote_tree())


def _force_push_orphan(world):
    """Someone 'cleans up' the state repo: a brand-new root commit, force-pushed over main."""
    w = world.tmp / "rewriter"
    shutil.rmtree(w, ignore_errors=True)
    git("init", "-q", "-b", "main", str(w), cwd=world.tmp)
    (w / "README.md").write_text("# fresh start\n")
    git("add", "-A", cwd=w)
    git("commit", "-q", "-m", "fresh start", cwd=w)
    git("push", "-q", "--force", str(world.remote), "HEAD:main", cwd=w)


def _force_rewind(world, n=1):
    w = world.tmp / "rewinder"
    shutil.rmtree(w, ignore_errors=True)
    git("clone", "-q", str(world.remote), str(w), cwd=world.tmp)
    git("reset", "-q", "--hard", f"HEAD~{n}", cwd=w)
    git("push", "-q", "--force", "origin", "HEAD:main", cwd=w)


def test_force_pushed_orphan_history_loses_nothing(world):
    a = world.use("laptop")
    add_job(JOB_A, status=js.SUBMITTED_UNCONFIRMED)
    assert a.sync_now()["pushed"]
    _force_push_orphan(world)
    assert world.remote_tree() == ["README.md"]

    a = world.use("laptop")
    add_job(JOB_B, status=js.NEEDS_HUMAN)
    r = a.sync_now()
    assert r["ok"] and r["pushed"], r
    assert jobs_store.get_job(JOB_A)["status"] == js.SUBMITTED_UNCONFIRMED   # local data untouched
    text = _remote_text(world)
    assert job_key(JOB_A) in text and job_key(JOB_B) in text                 # re-published
    assert world.remote_file(".gitattributes").strip() == "*.jsonl merge=union"
    # a second device starting from the rewritten remote sees both
    world.use("desktop").sync_now()
    assert {JOB_A, JOB_B} <= set(jobs_store.read_jobs())
    assert world.use("laptop").sync_now()["ok"]                               # and it stays converged


def test_remote_rewound_by_force_push_is_restored(world):
    a = world.use("laptop")
    add_job(JOB_A)
    a.sync_now()
    b = world.use("desktop")
    b.sync_now()
    jobs_store.update_job(JOB_A, status=js.CONFIRMED)
    assert b.sync_now()["pushed"]
    _force_rewind(world)                                  # drops the desktop's 'confirmed' commit
    assert "confirmed" not in (world.remote_file("ledger/applications.jsonl") or "")

    r = world.use("desktop").sync_now()
    assert r["ok"], r
    world.use("desktop").sync_now()
    assert "confirmed" in (world.remote_file("ledger/applications.jsonl") or "")
    world.use("laptop").sync_now()
    assert jobs_store.get_job(JOB_A)["status"] == js.CONFIRMED
    ledger = [json.loads(ln) for ln in world.remote_file("ledger/applications.jsonl").splitlines()]
    assert len({e["id"] for e in ledger}) == len(ledger)  # no duplicated events after the re-merge


# ── a token GitHub refuses (found by the real github.com e2e, tests/e2e/test_sync_real_github_e2e.py) ──

@pytest.mark.parametrize("stderr,kind", [
    ("remote: Invalid username or token. Password authentication is not supported for Git operations.\n"
     "fatal: Authentication failed for 'https://github.com/alex-example/applyqueue-state.git/'", gitrepo.AuthError),
    ("fatal: could not read Username for 'https://github.com': terminal prompts disabled", gitrepo.AuthError),
    ("remote: Repository not found.\nfatal: repository 'https://github.com/alex-example/x.git/' not found",
     gitrepo.AuthError),
    ("remote: Permission to alex-example/applyqueue-state.git denied to alex-bot.\n"
     "fatal: unable to access 'https://github.com/alex-example/applyqueue-state.git/': "
     "The requested URL returned error: 403", gitrepo.AuthError),
    ("fatal: unable to access 'https://github.com/alex-example/x.git/': Could not resolve host: github.com",
     gitrepo.NetworkError),
    ("fatal: unable to access 'https://github.com/x/': Failed to connect to 127.0.0.1 port 9", gitrepo.NetworkError),
])
def test_git_auth_failures_are_not_mistaken_for_offline(tmp_path, monkeypatch, stderr, kind):
    monkeypatch.setattr(gitrepo, "run_killing_tree",
                        lambda *a, **k: subprocess.CompletedProcess(a[0], 128, "", stderr))
    with pytest.raises(gitrepo.GitError) as ei:
        gitrepo.GitRepo(tmp_path).run("fetch", "--quiet", "origin")
    assert type(ei.value) is kind


def test_revoked_token_pauses_sync_with_a_clear_message_then_recovers(world):
    a = world.use("laptop")
    add_job(JOB_A)
    assert a.sync_now()["ok"]
    store.save_token({"access_token": "gho" + "_" + "R" * 36, "source": "test"})   # revoked on GitHub
    jobs_store.update_job(JOB_A, status=js.SUBMITTED_UNCONFIRMED)
    r = SyncEngine().sync_now()
    assert r["ok"] is False and r["pushed"] is False and r["pending"] >= 1, r
    assert "refused the token" in r["auth_error"] and "alex-example/applyqueue-state" in r["auth_error"]
    assert "R" * 36 not in json.dumps(r)
    assert "submitted" not in (world.remote_file(LEDGER) or "")
    assert SyncEngine().status()["auth_error"] == r["auth_error"]
    store.save_token({"access_token": FAKE_TOKEN, "source": "test"})                # linked again
    r = SyncEngine().sync_now()
    assert r["ok"] and r["pushed"] and r["pending"] == 0, r
    assert "submitted" in world.remote_file(LEDGER)
    assert SyncEngine().status()["auth_error"] is None


def test_git_refusing_the_token_on_first_clone_is_reported_not_raised(world, monkeypatch):
    world.use("desktop")

    def refuse(self, url):
        raise gitrepo.AuthError("refused", "fatal: Authentication failed for 'https://github.example/'")
    monkeypatch.setattr(gitrepo.GitRepo, "clone", refuse)
    r = SyncEngine().sync_now()
    assert r == {"ok": False, "auth_error": r["auth_error"], "offline": False, "pushed": False, "pending": 0}
    assert "Link GitHub again" in r["auth_error"]
    assert not store.repo_dir().joinpath(".git").exists()


def test_unlinked_github_says_so(world):
    a = world.use("laptop")
    add_job(JOB_A)
    a.sync_now()
    store.clear_token()
    jobs_store.update_job(JOB_A, status=js.SUBMITTED_UNCONFIRMED)
    r = SyncEngine().sync_now()
    assert r["ok"] is False and r["auth_error"] == "GitHub is not linked.", r


def test_token_without_push_permission_is_reported(world):
    world.gh.repos[FULL]["permissions"] = {"push": False, "pull": True}
    a = world.use("laptop")
    add_job(JOB_A)
    r = a.sync_now()
    assert r["ok"] is False and "cannot push" in r["auth_error"], r
