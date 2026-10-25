"""M8: schedules across DST transitions and device time-zone changes."""
import os
import time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from core.searches import schedule

LA = "America/Los_Angeles"


def _ts(y, mo, d, h, mi, tz=LA, fold=0):
    return datetime(y, mo, d, h, mi, tzinfo=ZoneInfo(tz), fold=fold).timestamp()


def _runs(expr, start, end, tz=LA):
    out, t = [], start
    while True:
        t = schedule.next_after(expr, t, tz)
        if t > end:
            return out
        out.append(t)


def _utc(t):
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%MZ")


def test_spring_forward_missing_time_runs_once_right_after_the_gap():
    # 2026-03-08 02:00 PST -> 03:00 PDT: 02:30 does not exist that day
    runs = _runs("30 2 * * *", _ts(2026, 3, 7, 12, 0), _ts(2026, 3, 9, 12, 0))
    assert [_utc(r) for r in runs] == ["2026-03-08T10:30Z", "2026-03-09T09:30Z"]
    # 2026-03-08T10:30Z == 03:30 PDT: once, not skipped and not doubled


def test_fall_back_repeated_hour_runs_daily_job_exactly_once():
    # 2026-11-01 02:00 PDT -> 01:00 PST: 01:30 happens twice
    runs = _runs("30 1 * * *", _ts(2026, 10, 31, 12, 0), _ts(2026, 11, 2, 12, 0))
    assert len(runs) == 2, [_utc(r) for r in runs]
    # starting inside the repeated hour (second pass) must not re-run the first 01:30
    inside = _ts(2026, 11, 1, 1, 40, fold=1)
    assert schedule.next_after("30 1 * * *", inside, LA) > _ts(2026, 11, 2, 0, 0)


def test_hourly_across_both_transitions_never_doubles_and_gaps_are_bounded():
    for a, b in ((_ts(2026, 3, 7, 20, 0), _ts(2026, 3, 8, 8, 0)), (_ts(2026, 10, 31, 20, 0), _ts(2026, 11, 1, 8, 0))):
        runs = _runs("0 * * * *", a, b)
        gaps = [y - x for x, y in zip(runs, runs[1:])]
        assert len(set(runs)) == len(runs)
        assert all(1800 <= g <= 2 * 3600 for g in gaps), gaps


@pytest.mark.skipif(not hasattr(time, "tzset"), reason="POSIX tzset")
def test_explicit_zone_ignores_device_time_zone_changes(monkeypatch):
    start = _ts(2026, 6, 1, 12, 0)
    want = schedule.next_after("5 7 * * *", start, "Europe/Berlin")
    for device_tz in ("UTC", "Asia/Tokyo", "America/New_York", "Pacific/Chatham"):
        monkeypatch.setenv("TZ", device_tz)
        time.tzset()
        assert schedule.next_after("5 7 * * *", start, "Europe/Berlin") == want
    monkeypatch.delenv("TZ")
    time.tzset()
    assert datetime.fromtimestamp(want, ZoneInfo("Europe/Berlin")).strftime("%H:%M") == "07:05"


@pytest.mark.skipif(not hasattr(time, "tzset"), reason="POSIX tzset")
def test_local_zone_follows_the_device_after_a_move(monkeypatch):
    start = _ts(2026, 6, 1, 12, 0, tz="UTC")
    monkeypatch.setenv("TZ", "America/New_York")
    time.tzset()
    ny = schedule.next_after("0 9 * * *", start, "local")
    monkeypatch.setenv("TZ", "Asia/Tokyo")
    time.tzset()
    tokyo = schedule.next_after("0 9 * * *", start, "local")
    monkeypatch.delenv("TZ")
    time.tzset()
    assert datetime.fromtimestamp(ny, ZoneInfo("America/New_York")).hour == 9
    assert datetime.fromtimestamp(tokyo, ZoneInfo("Asia/Tokyo")).hour == 9


def test_unknown_zone_falls_back_instead_of_crashing():
    assert schedule.next_after("0 9 * * *", time.time(), "Mars/Olympus_Mons") > time.time()
