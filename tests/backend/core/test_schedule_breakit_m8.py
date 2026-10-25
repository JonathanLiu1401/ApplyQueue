"""M8 break-it: schedules under clock jumps, kills mid-run and long 429 streaks."""
import json
import shutil
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from core.searches import schedule, store

UTC = timezone.utc
LA = "America/Los_Angeles"
YEAR = 365 * 86400


def ts(y, mo, d, h, mi=0, tz=UTC, fold=0):
    return datetime(y, mo, d, h, mi, tzinfo=tz if tz is UTC else ZoneInfo(tz), fold=fold).timestamp()


@pytest.fixture
def data(aq_data_dir):
    return aq_data_dir


def _search(kind="hourly", tz="UTC", **spec):
    return store.upsert_search({"titles": ["Data Analyst"], "locations": ["Springfield"],
                                "schedule": {"kind": kind, "tz": tz, **spec}})


def test_killed_after_a_forward_clock_jump_does_not_block_for_a_year(data):
    s = _search("hourly")
    t = ts(2026, 10, 25, 10, 30)
    schedule.due(now=t - 3600)
    assert schedule.begin(s["id"], now=t + YEAR)       # clock was a year ahead, then the app was killed
    # clock corrected: the "running" mark is from the future and must not hold the search forever;
    # the next slot after the correction (11:00, the runner polls every minute) runs
    assert schedule.due(now=t + 1800 - 60) == []
    assert schedule.due(now=t + 1800 + 60) == [s["id"]]
    assert schedule.begin(s["id"], now=t + 1800 + 60)


def test_clock_jump_forward_then_back_keeps_the_hourly_cadence(data):
    s = _search("hourly")
    t = ts(2026, 10, 25, 10, 30)
    schedule.due(now=t - 3600)
    schedule.begin(s["id"], now=t + YEAR)
    schedule.finish(s["id"], "ok", now=t + YEAR + 60)
    # back to the real time: the next slot is the next hour, not next year
    assert schedule.due(now=t + 1800 - 60) == []
    assert schedule.due(now=t + 1800 + 60) == [s["id"]]


def test_rate_limit_streak_never_exceeds_the_ceiling_even_with_jumps(data):
    s = _search("hourly")
    t = ts(2026, 10, 25, 10, 0)
    schedule.due(now=t - 3600)
    for _ in range(40):                                  # 40 x 429 in a row: no overflow, no pause
        schedule.begin(s["id"], now=t)
        st = schedule.finish(s["id"], "rate_limit", ["429 Too Many Requests"], now=t)
        assert st["backoff_until"] - t <= schedule.BACKOFF_MAX_S
        t = st["backoff_until"] + 1
    assert not st.get("paused")
    # a backoff written by a clock running far ahead is clamped back to the ceiling
    d = json.loads(schedule._path().read_text())
    d[s["id"]]["backoff_until"] = t + 10 * YEAR
    schedule._path().write_text(json.dumps(d))
    schedule.due(now=t)
    assert schedule.load_state()[s["id"]]["backoff_until"] <= t + schedule.BACKOFF_MAX_S


def test_daily_la_schedule_runs_once_on_fall_back_day_with_device_checks_every_minute(data):
    s = _search("custom", tz=LA, cron="30 1 * * *")
    start, end = ts(2026, 10, 31, 12, 0, tz=LA), ts(2026, 11, 2, 12, 0, tz=LA)
    schedule.due(now=start)
    runs, now = [], start
    while now < end:
        if schedule.due(now=now) == [s["id"]]:
            assert schedule.begin(s["id"], now=now)
            schedule.finish(s["id"], "ok", now=now + 30)
            runs.append(now)
        now += 60
    local = [datetime.fromtimestamp(r, ZoneInfo(LA)).strftime("%m-%d %H:%M %Z") for r in runs]
    assert local == ["11-01 01:30 PDT", "11-02 01:30 PST"], local


def test_hourly_la_schedule_on_fall_back_day_runs_every_real_hour_once(data):
    s = _search("hourly", tz=LA)
    start, end = ts(2026, 10, 31, 22, 0, tz=LA), ts(2026, 11, 1, 4, 0, tz=LA)
    schedule.due(now=start)
    runs, now = [], start + 60
    while now < end:
        if schedule.due(now=now) == [s["id"]]:
            schedule.begin(s["id"], now=now)
            schedule.finish(s["id"], "ok", now=now + 30)
            runs.append(now)
        now += 60
    gaps = {round((b - a) / 60) for a, b in zip(runs, runs[1:])}
    # 23:00, 00:00, 01:00 PDT, 01:00 PST (the repeated hour), 02:00, 03:00 PST
    assert len(runs) == 6 and gaps == {60}, [datetime.fromtimestamp(r, UTC).isoformat() for r in runs]
