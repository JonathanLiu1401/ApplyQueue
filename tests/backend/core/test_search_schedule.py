"""M7: per-search scheduled reruns (cron, catch-up, clock changes, backoff, re-auth, locks)."""
import json
from datetime import datetime, timezone

import pytest

from core.searches import runner, schedule, store

UTC = timezone.utc


def ts(y, mo, d, h, mi=0):
    return datetime(y, mo, d, h, mi, tzinfo=UTC).timestamp()


def iso(t):
    return datetime.fromtimestamp(t, UTC).strftime("%Y-%m-%d %H:%M")


@pytest.fixture
def data(aq_data_dir):
    return aq_data_dir


def _search(kind="hourly", **spec):
    return store.upsert_search({"titles": ["Data Analyst"], "locations": ["Springfield"],
                                "schedule": {"kind": kind, "tz": "UTC", **spec}})


# ── cron ──
@pytest.mark.parametrize("expr,start,want", [
    ("*/15 * * * *", ts(2026, 3, 2, 10, 7), "2026-03-02 10:15"),
    ("0 9 * * 1-5", ts(2026, 3, 6, 9, 0), "2026-03-09 09:00"),       # Fri 09:00 -> Mon
    ("30 8,17 * * *", ts(2026, 3, 2, 8, 30), "2026-03-02 17:30"),
    ("0 0 1 * *", ts(2026, 12, 15, 0), "2027-01-01 00:00"),
    ("0 12 29 2 *", ts(2026, 3, 1, 0), "2028-02-29 12:00"),
    ("0 6 * * 7", ts(2026, 3, 2, 0), "2026-03-08 06:00"),            # 7 == Sunday
    ("@daily", ts(2026, 3, 2, 10), "2026-03-03 00:00"),
    ("0 9 15 * 1", ts(2026, 3, 10, 0), "2026-03-15 09:00"),          # dom OR dow (Mar 15 is Sun; Mon is 16th)
])
def test_next_after(expr, start, want):
    assert iso(schedule.next_after(expr, start, "UTC")) == want


@pytest.mark.parametrize("bad", ["", "* * * *", "61 * * * *", "* 24 * * *", "*/0 * * * *", "a b c d e", "5-1 * * * *"])
def test_bad_cron(bad):
    with pytest.raises(schedule.CronError):
        schedule.parse_cron(bad)


def test_clean_schedule_validation(data):
    assert store.upsert_search({"titles": ["x"]})["schedule"] == {"kind": "off"}
    s = _search("daily", time="7:05")
    assert s["schedule"] == {"kind": "daily", "tz": "UTC", "time": "07:05"} and schedule.cron_for(s["schedule"]) == "5 7 * * *"
    for bad in ({"kind": "weekly"}, {"kind": "daily", "time": "25:00"}, {"kind": "hourly", "minute": 60},
                {"kind": "custom", "cron": "* * * * *"}, {"kind": "custom", "cron": "*/5 * * * *"},
                {"kind": "custom", "cron": "nope"}, {"kind": "daily", "tz": "../etc"}):
        with pytest.raises(store.ValidationError):
            store.upsert_search({"titles": ["x"], "schedule": bad})
    assert store.upsert_search({"titles": ["x"], "schedule": {"kind": "custom", "cron": "*/15 * * * *"}})["schedule"]["cron"] == "*/15 * * * *"


# ── catch-up, persistence, clock changes ──
def test_catch_up_runs_once_after_sleep_and_persists(data):
    s = _search("hourly", minute=0)
    t0 = ts(2026, 3, 2, 10, 30)
    assert schedule.due(now=t0) == []                        # next is 11:00
    assert iso(schedule.load_state()[s["id"]]["next_run_at"]) == "2026-03-02 11:00"
    wake = ts(2026, 3, 2, 17, 20)                            # laptop slept through 6 slots
    assert schedule.due(now=wake) == [s["id"]]
    assert schedule.begin(s["id"], "dev1", now=wake)
    st = schedule.load_state()[s["id"]]
    assert iso(st["next_run_at"]) == "2026-03-02 18:00"      # from now, not from the missed slots
    assert schedule.due(now=wake + 60) == []                 # running: no overlap
    schedule.finish(s["id"], "ok", now=wake + 300)
    assert schedule.due(now=wake + 600) == []                # no burst of missed runs
    # survives a restart: state is on disk
    raw = json.loads((data / "schedule_state.json").read_text())
    assert raw[s["id"]]["last_outcome"] == "ok" and raw[s["id"]]["history"][-1]["outcome"] == "ok"
    assert schedule.due(now=ts(2026, 3, 2, 18, 0)) == [s["id"]]


def test_min_gap_between_runs(data):
    s = _search("custom", cron="0,15,30,45 * * * *")
    schedule.begin(s["id"], now=ts(2026, 3, 2, 10, 14))
    assert iso(schedule.load_state()[s["id"]]["next_run_at"]) == "2026-03-02 10:30"


def test_clock_moved_back_pulls_next_run_in(data):
    s = _search("daily", time="09:00")
    schedule.due(now=ts(2026, 3, 2, 10))                     # next = Mar 3 09:00
    schedule.due(now=ts(2026, 2, 1, 10))                     # clock jumped back a month
    assert iso(schedule.load_state()[s["id"]]["next_run_at"]) == "2026-02-02 09:00"
    schedule._mutate(s["id"], lambda st: st.update(backoff_until=ts(2027, 1, 1, 0)))
    schedule.due(now=ts(2026, 2, 1, 10))
    assert schedule.load_state()[s["id"]]["backoff_until"] <= ts(2026, 2, 1, 10) + schedule.BACKOFF_MAX_S


def test_changed_schedule_recomputes(data):
    s = _search("daily", time="09:00")
    schedule.due(now=ts(2026, 3, 2, 10))
    store.upsert_search({"id": s["id"], "schedule": {"kind": "hourly", "minute": 30, "tz": "UTC"}})
    schedule.due(now=ts(2026, 3, 2, 10, 5))
    assert iso(schedule.load_state()[s["id"]]["next_run_at"]) == "2026-03-02 10:30"
    store.upsert_search({"id": s["id"], "schedule": {"kind": "off"}})
    assert schedule.due(now=ts(2026, 3, 9, 0)) == []


def test_stale_running_mark_is_forgotten(data):
    s = _search("hourly")
    t = ts(2026, 3, 2, 10, 0)
    schedule.begin(s["id"], now=t)                           # app killed / laptop slept mid-run
    assert schedule.due(now=t + 3600) == []
    assert schedule.due(now=t + schedule.RUN_TTL_S + 60) == [s["id"]]
    assert "interrupted" in schedule.load_state()[s["id"]]


# ── outcomes ──
def test_rate_limit_backoff_grows_and_caps(data):
    s = _search("hourly")
    t = ts(2026, 3, 2, 10, 0)
    waits = []
    for i in range(9):
        schedule.begin(s["id"], now=t)
        st = schedule.finish(s["id"], "rate_limit", ["HTTP 429 Too Many Requests"], now=t)
        waits.append(st["backoff_until"] - t)
        assert st["next_run_at"] >= st["backoff_until"]
        t = st["backoff_until"] + 1
    assert waits[:4] == [900, 1800, 3600, 7200] and max(waits) == schedule.BACKOFF_MAX_S
    assert not schedule.load_state()[s["id"]].get("paused")  # rate limits never pause
    schedule.begin(s["id"], now=t)
    st = schedule.finish(s["id"], "ok", now=t)
    assert st["failures"] == 0 and "backoff_until" not in st


def test_expired_login_pauses_with_reauth_notice_and_resume(data):
    s = _search("hourly")
    t = ts(2026, 3, 2, 10, 0)
    schedule.begin(s["id"], now=t)
    st = schedule.finish(s["id"], runner.outcome_from(["Error: 401 Unauthorized: invalid api key"], []), now=t)
    assert st["paused"] and st["paused_reason"] == "login_expired"
    assert st["notice"]["kind"] == "reauth" and st["notice"]["action"] == "/llm"
    assert schedule.due(now=t + 86400) == []
    schedule.begin(s["id"], now=t)
    st = schedule.finish(s["id"], "auth", ["LinkedIn session expired, please sign in"], now=t)
    assert st["notice"]["target"] == "portal"
    st = schedule.resume(s["id"], now=t + 86400 + 1800)
    assert not st.get("paused") and "notice" not in st
    assert iso(st["next_run_at"]) == "2026-03-03 11:00"      # next slot from now, no catch-up burst


def test_repeated_errors_pause(data):
    s = _search("hourly")
    t = ts(2026, 3, 2, 10)
    for _ in range(schedule.MAX_ERRORS_BEFORE_PAUSE):
        st = schedule.finish(s["id"], "error", ["Error: boom"], now=t)
    assert st["paused_reason"] == "errors"


def test_classify():
    assert schedule.classify([]) == "ok"
    assert schedule.classify(["429 rate limit exceeded"]) == "rate_limit"
    assert schedule.classify(["Please log in to continue"]) == "auth"
    assert schedule.classify(["Timeout"]) == "error"
    assert runner.outcome_from([], ["Fetching descriptions", "❌ Error: Too Many Requests"]) == "rate_limit"
    assert runner.outcome_from([], ["all good"], cancelled=True) == "cancelled"
    assert runner.outcome_from([], ["0 failed of 3"]) == "ok"


def test_user_pause(data):
    s = _search("hourly")
    schedule.pause(s["id"])
    assert schedule.due(now=ts(2027, 1, 1, 0)) == []


# ── runner: overlap + cross-device lock ──
class FakeLock:
    def __init__(self, ok=True, holder=None):
        self.ok, self.holder, self.acquired, self.released = ok, holder, [], []

    def acquire(self, sid):
        self.acquired.append(sid)
        return {"ok": self.ok, "holder": self.holder}

    def release(self, sid):
        self.released.append(sid)


def test_tick_runs_once_and_skips_overlap(data):
    a, b = _search("hourly"), _search("hourly")
    t = ts(2026, 3, 2, 10, 30)
    schedule.due(now=t - 3600)
    lock, done = FakeLock(), {}

    def start(sid, on_done):
        done[sid] = on_done
        return True
    res = runner.tick(start, lambda: False, lock=lock, now=t)
    assert [r["action"] for r in res] == ["started"] and len(done) == 1
    # collector busy: nothing else starts
    assert runner.tick(start, lambda: True, lock=lock, now=t + 30) == [
        {"id": next(i for i in (a["id"], b["id"]) if i not in done), "action": "waiting_busy"}]
    first = next(iter(done))
    done[first](["429 Too Many Requests"], [], False)
    assert lock.released == [first]
    assert schedule.load_state()[first]["backoff_until"] > t
    res = runner.tick(start, lambda: False, lock=lock, now=t + 60)
    assert res[0]["action"] == "started" and res[0]["id"] != first


def test_tick_skips_when_other_device_holds_lock(data):
    s = _search("hourly")
    t = ts(2026, 3, 2, 10, 30)
    schedule.due(now=t - 3600)
    res = runner.tick(lambda *_: pytest.fail("must not run"), lambda: False,
                      lock=FakeLock(ok=False, holder="Laptop"), now=t)
    assert res == [{"id": s["id"], "action": "skipped_other_device", "holder": "Laptop"}]
    assert schedule.due(now=t + 60) == []                    # slot counted as run elsewhere


def test_tick_defers_when_start_refused(data):
    s = _search("hourly")
    t = ts(2026, 3, 2, 10, 30)
    schedule.due(now=t - 3600)
    lock = FakeLock()
    assert runner.tick(lambda *_: False, lambda: False, lock=lock, now=t)[0]["action"] == "waiting_busy"
    assert lock.released == [s["id"]] and schedule.due(now=t + 61) == [s["id"]]


def test_sync_lock_uses_named_lock_not_job_locks():
    calls = []

    class Eng:
        def try_named_lock(self, name, ttl, meta):
            calls.append(("lock", name, ttl, meta))
            return {"ok": False, "reason": "locked", "holder": "Desktop"}

        def release_named(self, name):
            calls.append(("release", name))
    lk = runner.SyncLock(engine_factory=Eng, enabled=lambda: True)
    assert lk.acquire("s_1") == {"ok": False, "reason": "locked", "holder": "Desktop"}
    lk.release("s_1")
    assert calls == [("lock", "search-s_1", schedule.RUN_TTL_S, {"search_id": "s_1"}), ("release", "search-s_1")]
    assert runner.SyncLock(engine_factory=Eng, enabled=lambda: False).acquire("s_1")["local_only"]

    class Broken:
        def try_named_lock(self, *a):
            raise RuntimeError("git missing")
    assert runner.SyncLock(engine_factory=Broken, enabled=lambda: True).acquire("s_1")["unsynced"]


# ── always-on export, remote runs, prompts ──
def test_export_remote_runs_and_prompts(data):
    from core.cli_backends.prompt import grok_bot_setup_prompt, schedule_runner_prompt
    s = _search("daily", time="09:00")
    _search("off")
    exp = schedule.export_for_sync("dev1")
    assert [r["id"] for r in exp["searches"]] == [s["id"]] and exp["searches"][0]["cron"] == "0 9 * * *"
    assert "token" not in json.dumps(exp).lower()
    t = ts(2026, 3, 2, 9, 1)
    n = schedule.record_remote_runs([{"search_id": s["id"], "device": "bot", "started_at": "2026-03-02T09:00:00+00:00"},
                                     {"search_id": s["id"], "device": "dev1", "started_at": "2026-03-05T09:00:00+00:00"},
                                     {"bad": 1}], "dev1")
    assert n == 1 and iso(schedule.load_state()[s["id"]]["next_run_at"]) == "2026-03-03 09:00"
    assert schedule.due(now=t) == []
    setup = grok_bot_setup_prompt([], exp)
    assert s["id"] in setup and "0 9 * * *" in setup and "locks/search-<id>.json" in setup and "run ONCE" in setup
    for target in ("grok", "chatgpt", "claude", "codex", "cursor"):
        p = schedule_runner_prompt(exp, target)
        assert "Data Analyst" in p and "back off 15 minutes" in p and "pause this search" in p
    assert "--run-schedules-once" in schedule_runner_prompt(exp, "codex")
    with pytest.raises(ValueError):
        schedule_runner_prompt(exp, "nope")
    assert "No searches have a schedule" in schedule_runner_prompt({"searches": []})
    schedule.begin(s["id"], "dev1", now=t + 86400)
    schedule.finish(s["id"], "ok", now=t + 86400 + 60, device="dev1")
    rows = schedule.runs_to_export("dev1", set())
    assert rows and rows[-1]["outcome"] == "ok" and rows[-1]["device"] == "dev1"
    assert schedule.runs_to_export("dev1", {r["_key"] for r in rows}) == []
