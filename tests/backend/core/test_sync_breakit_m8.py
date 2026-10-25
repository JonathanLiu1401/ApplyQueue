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
