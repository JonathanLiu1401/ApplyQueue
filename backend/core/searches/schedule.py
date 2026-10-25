"""Per-search scheduled reruns.

The schedule *spec* lives on the saved search (``search["schedule"]`` in searches.json); the
runtime *state* (next run, running, backoff, pause, re-auth notice, recent runs) lives in
``schedule_state.json`` next to it, guarded by a file lock so the desktop app and the headless
runner (``main.py --run-schedules-once``) never run the same search at once on one device.
Across devices, :mod:`core.sync` named locks (``locks/search-<id>.json``) give one runner per
search, and runs recorded in the sync repo stop other devices from repeating them.

Policies
- Catch-up: after sleep or downtime, missed occurrences collapse into ONE run; the next run is
  computed from "now", never from the missed slot, so there is no burst.
- Minimum gap: runs are at least ``MIN_GAP_S`` apart, whatever the cron says.
- Clock changes: a stored next run later than the cron's next slot from "now" (clock moved
  back, or the schedule changed) is pulled in; a backoff ending implausibly far ahead is clamped.
- Rate limits: exponential backoff (15 min doubling, capped at 24 h), reset by a clean run.
- Expired logins: the search is paused with a one-click re-auth notice; resuming clears it.
"""
from __future__ import annotations

import json
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from filelock import FileLock

try:
    from core.config import get_data_dir
except ImportError:  # pragma: no cover
    from backend.core.config import get_data_dir

from . import store

KINDS = ("off", "hourly", "daily", "custom")
MIN_GAP_S = 15 * 60
BACKOFF_BASE_S = 15 * 60
BACKOFF_MAX_S = 24 * 3600
RUN_TTL_S = 30 * 60           # lock / "running" mark TTL; a live run renews it every HEARTBEAT_S
HEARTBEAT_S = 10 * 60         # so only a crashed or slept-through run goes stale
AGENT_LOCK_TTL_S = 3 * 3600   # always-on agents cannot heartbeat, so their locks live longer
MAX_RUN_S = 6 * 3600          # the headless runner gives up waiting on one run after this
MAX_ERRORS_BEFORE_PAUSE = 5
HISTORY = 20

_RATE_RE = re.compile(r"\b429\b|rate.?limit|too many requests|quota|resource.?exhausted|overloaded|try again later",
                      re.I)
_AUTH_RE = re.compile(r"\b401\b|unauthori[sz]ed|invalid (api )?key|authentication (failed|error)|"
                      r"log(ged)?.?in (required|expired)|not (logged|signed) in|session (has )?expired|"
                      r"token (has )?expired|re-?auth|please (log|sign) in|sign-?in required|login required", re.I)
_PORTAL_RE = re.compile(r"linkedin|indeed|glassdoor|workday|greenhouse|lever|portal|captcha", re.I)


# ── cron ────────────────────────────────────────────────────────────────────
class CronError(store.ValidationError):
    pass


_ALIASES = {"@hourly": "0 * * * *", "@daily": "0 0 * * *", "@midnight": "0 0 * * *",
            "@weekly": "0 0 * * 0", "@monthly": "0 0 1 * *"}
_RANGES = ((0, 59), (0, 23), (1, 31), (1, 12), (0, 7))
_NAMES = ("minute", "hour", "day of month", "month", "day of week")


def _field(text: str, lo: int, hi: int, name: str) -> set[int]:
    out: set[int] = set()
    for part in text.split(","):
        m = re.fullmatch(r"(\*|\d+(?:-\d+)?)(?:/(\d+))?", part.strip())
        if not m:
            raise CronError(f"Bad {name} field: {part!r}")
        base, step = m.group(1), int(m.group(2) or 1)
        if step < 1:
            raise CronError(f"Bad step in {name}")
        if base == "*":
            a, b = lo, hi
        elif "-" in base:
            a, b = (int(x) for x in base.split("-"))
        else:
            a = b = int(base)
            if m.group(2):
                b = hi
        if a < lo or b > hi or a > b:
            raise CronError(f"{name} must be between {lo} and {hi}")
        out.update(range(a, b + 1, step))
    return out


def parse_cron(expr: str) -> tuple[set[int], set[int], set[int], set[int], set[int], bool, bool]:
    expr = _ALIASES.get((expr or "").strip().lower(), (expr or "").strip())
    parts = expr.split()
    if len(parts) != 5:
        raise CronError("A custom schedule needs 5 fields: minute hour day-of-month month day-of-week")
    sets = [_field(p, lo, hi, n) for p, (lo, hi), n in zip(parts, _RANGES, _NAMES)]
    dow = {d % 7 for d in sets[4]}
    return sets[0], sets[1], sets[2], sets[3], dow, parts[2] != "*", parts[4] != "*"


def _tz(name: str | None):
    if name and name.upper() in ("UTC", "ETC/UTC", "Z"):
        return timezone.utc
    if name and name != "local":
        try:
            from zoneinfo import ZoneInfo
            return ZoneInfo(name)
        except Exception:  # unknown zone or no tz database (some Windows installs): device-local time
            pass
    return None


def _repeated_hour_slot(ts, out, zone, mins, hours, doms, months, dows, dom_r, dow_r) -> float | None:
    """The wall-clock walk above sees a fall-back hour once (its first pass). Like cron, a
    schedule that runs every hour also runs in the repeated hour; fixed-hour schedules do not
    (a daily 01:30 runs once). Only real instants on the second pass (fold=1) are checked."""
    if len(hours) < 24 or out - ts > 3 * 3600:
        return None
    u = (int(ts) // 60 + 1) * 60
    while u < out:
        lt = datetime.fromtimestamp(u, zone) if zone else datetime.fromtimestamp(u)
        if lt.fold and lt.minute in mins and lt.month in months:
            dom_ok, dow_ok = lt.day in doms, (lt.isoweekday() % 7) in dows
            if (dom_ok or dow_ok) if (dom_r and dow_r) else (dom_ok and dow_ok):
                return float(u)
        u += 60
    return None


def next_after(expr: str, ts: float, tz: str | None = None) -> float:
    """First cron slot strictly after ``ts`` (epoch seconds), evaluated in ``tz`` wall-clock time."""
    mins, hours, doms, months, dows, dom_r, dow_r = parse_cron(expr)
    zone = _tz(tz)
    now = datetime.fromtimestamp(ts, zone) if zone else datetime.fromtimestamp(ts).astimezone()
    t = now.replace(second=0, microsecond=0, tzinfo=None) + timedelta(minutes=1)
    limit = t + timedelta(days=366 * 5)
    while t < limit:
        if t.month not in months:
            t = (t.replace(day=1, hour=0, minute=0) + timedelta(days=32)).replace(day=1)
            continue
        dom_ok, dow_ok = t.day in doms, (t.isoweekday() % 7) in dows
        day_ok = (dom_ok or dow_ok) if (dom_r and dow_r) else (dom_ok and dow_ok)
        if not day_ok:
            t = t.replace(hour=0, minute=0) + timedelta(days=1)
            continue
        if t.hour not in hours:
            t = t.replace(minute=0) + timedelta(hours=1)
            continue
        if t.minute not in mins:
            t += timedelta(minutes=1)
            continue
        aware = t.replace(tzinfo=zone) if zone else t.astimezone()
        out = aware.timestamp()
        if out > ts:
            return _repeated_hour_slot(ts, out, zone, mins, hours, doms, months, dows, dom_r, dow_r) or out
        t += timedelta(minutes=1)
    raise CronError("This schedule never runs")


# ── spec ────────────────────────────────────────────────────────────────────
def cron_for(spec: dict | None) -> str | None:
    spec = spec or {}
    kind = spec.get("kind") or "off"
    if kind == "hourly":
        return f"{int(spec.get('minute') or 0)} * * * *"
    if kind == "daily":
        h, m = (int(x) for x in (spec.get("time") or "09:00").split(":"))
        return f"{m} {h} * * *"
    if kind == "custom":
        return spec.get("cron") or None
    return None


def clean_schedule(raw) -> dict:
    if raw in (None, "", "off"):
        return {"kind": "off"}
    if not isinstance(raw, dict):
        raise store.ValidationError("schedule must be an object")
    kind = raw.get("kind") or "off"
    if kind not in KINDS:
        raise store.ValidationError("schedule kind must be off, hourly, daily or custom")
    tz = str(raw.get("tz") or "local")[:64]
    if not re.fullmatch(r"[A-Za-z0-9_+\-/]+", tz):
        raise store.ValidationError("Bad time zone")
    out: dict = {"kind": kind, "tz": tz}
    if kind == "hourly":
        minute = int(raw.get("minute") or 0)
        if not 0 <= minute <= 59:
            raise store.ValidationError("minute must be 0-59")
        out["minute"] = minute
    elif kind == "daily":
        t = str(raw.get("time") or "09:00")
        m = re.fullmatch(r"([01]?\d|2[0-3]):([0-5]\d)", t)
        if not m:
            raise store.ValidationError("time must be HH:MM")
        out["time"] = f"{int(m.group(1)):02d}:{m.group(2)}"
    elif kind == "custom":
        expr = _ALIASES.get(str(raw.get("cron") or "").strip().lower(), str(raw.get("cron") or "").strip())[:100]
        parse_cron(expr)
        # Refuse schedules that would fire more often than every MIN_GAP_S.
        ts = time.time()
        for _ in range(12):
            nxt = next_after(expr, ts, tz)
            if nxt - ts < MIN_GAP_S and _ > 0:
                raise store.ValidationError("A search can run at most every 15 minutes")
            ts = nxt
        out["cron"] = expr
    if kind == "off":
        return {"kind": "off"}
    return out


# ── state ───────────────────────────────────────────────────────────────────
def _path() -> Path:
    return get_data_dir() / "schedule_state.json"


def _flock() -> FileLock:
    p = _path()
    p.parent.mkdir(parents=True, exist_ok=True)
    return FileLock(str(p) + ".lock")


def _read() -> dict:
    try:
        d = json.loads(_path().read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _write(d: dict) -> None:
    p = _path()
    tmp = p.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        json.dump(d, f, indent=2)
    tmp.replace(p)


def load_state() -> dict:
    with _flock():
        return _read()


def _mutate(sid: str, fn):
    with _flock():
        d = _read()
        st = d.setdefault(sid, {})
        res = fn(st)
        _write(d)
        return res


def _iso(ts: float | None) -> str | None:
    return datetime.fromtimestamp(ts, timezone.utc).isoformat() if ts else None


def _schedule_next(st: dict, cron: str, tz: str | None, now: float) -> None:
    st["cron"], st["tz"] = cron, tz
    st["next_run_at"] = next_after(cron, max(now, (st.get("last_run_at") or 0) + MIN_GAP_S - 60), tz)


def _running(st: dict, now: float) -> bool:
    r = st.get("running")
    if not r:
        return False
    beat = max(float(r.get("since") or 0), float(r.get("beat") or 0))
    # a mark from the "future" means the clock was set back after the run started (or the app
    # was killed after a forward jump); without this the search would look busy until that date
    return -RUN_TTL_S < now - beat < RUN_TTL_S


def heartbeat(sid: str, device: str = "", now: float | None = None) -> bool:
    """Renew this device's "running" mark during a long run; False if the run is not ours any more."""
    now = time.time() if now is None else now

    def fn(st):
        r = st.get("running")
        if not r or (device and r.get("device") not in ("", device)):
            return False
        r["beat"] = now
        return True
    return _mutate(sid, fn)


def refresh(search: dict, st: dict, now: float) -> None:
    """Bring next_run_at in line with the spec and the clock (no I/O)."""
    cron = cron_for(search.get("schedule"))
    if not cron:
        st.pop("next_run_at", None)
        st.pop("cron", None)
        return
    tz = (search.get("schedule") or {}).get("tz")
    if st.get("cron") != cron or st.get("tz") != tz or not st.get("next_run_at"):
        if st.get("cron") != cron or st.get("tz") != tz:
            st["spec_at"] = now           # the shared list keeps the newest entry per search
        _schedule_next(st, cron, tz, now)
        return
    soonest = next_after(cron, now, tz)
    if st["next_run_at"] > soonest:           # clock moved back
        st["next_run_at"] = soonest
    if st.get("backoff_until") and st["backoff_until"] > now + BACKOFF_MAX_S:
        st["backoff_until"] = now + BACKOFF_MAX_S
    if st.get("running") and not _running(st, now):   # crashed or slept through: forget it
        st["interrupted"] = st.pop("running")


def due(searches: list[dict] | None = None, now: float | None = None) -> list[str]:
    """Ids of searches whose schedule is due now. Updates next-run bookkeeping."""
    now = time.time() if now is None else now
    searches = store.list_searches() if searches is None else searches
    out = []
    with _flock():
        d = _read()
        for s in searches:
            st = d.setdefault(s["id"], {})
            refresh(s, st, now)
            if not st.get("next_run_at") or st.get("paused") or not s.get("enabled", True):
                continue
            if _running(st, now) or (st.get("backoff_until") or 0) > now:
                continue
            if st["next_run_at"] <= now:
                out.append(s["id"])
        for sid in [k for k in d if not any(s["id"] == k for s in searches)]:
            d.pop(sid)
        _write(d)
    return out


def begin(sid: str, device: str = "", now: float | None = None) -> bool:
    """Mark a run as started; False if this search is already running on this device."""
    now = time.time() if now is None else now
    search = store.get_search(sid) or {}

    def fn(st):
        if _running(st, now):
            return False
        st["running"] = {"since": now, "device": device}
        st["last_run_at"] = now
        cron = cron_for(search.get("schedule"))
        if cron:
            _schedule_next(st, cron, (search.get("schedule") or {}).get("tz"), now)
        return True
    return _mutate(sid, fn)


def classify(texts) -> str:
    blob = "\n".join(t for t in (texts or []) if isinstance(t, str))[-20000:]
    if _AUTH_RE.search(blob):
        return "auth"
    if _RATE_RE.search(blob):
        return "rate_limit"
    return "error" if blob.strip() else "ok"


def _notice(texts) -> dict:
    blob = "\n".join(texts or [])
    portal = bool(_PORTAL_RE.search(blob))
    return {"kind": "reauth", "target": "portal" if portal else "ai",
            "message": ("A job site sign-in expired. Sign in again, then resume this search."
                        if portal else "Your AI sign-in or API key stopped working. Sign in again, then resume this search."),
            "action": "/settings" if portal else "/llm"}


def finish(sid: str, outcome: str, texts: list[str] | None = None, now: float | None = None,
           device: str = "") -> dict:
    """Record the end of a run and apply backoff / pause policies. Returns the new state."""
    now = time.time() if now is None else now

    def fn(st):
        st.pop("running", None)
        st["last_outcome"] = outcome
        st["last_finished_at"] = now
        hist = st.setdefault("history", [])
        hist.append({"started_at": _iso(st.get("last_run_at")), "finished_at": _iso(now),
                     "outcome": outcome, "device": device})
        del hist[:-HISTORY]
        if outcome == "cancelled":
            pass
        elif outcome == "ok":
            st["failures"] = 0
            st.pop("backoff_until", None)
        elif outcome == "auth":
            st.update(paused=True, paused_reason="login_expired", notice=_notice(texts))
        else:
            st["failures"] = int(st.get("failures") or 0) + 1
            if outcome == "rate_limit" or st["failures"] < MAX_ERRORS_BEFORE_PAUSE:
                wait = min(BACKOFF_BASE_S * 2 ** (st["failures"] - 1), BACKOFF_MAX_S)
                st["backoff_until"] = now + wait
                if st.get("next_run_at") and st["next_run_at"] < st["backoff_until"]:
                    st["next_run_at"] = st["backoff_until"]
            else:
                st.update(paused=True, paused_reason="errors",
                          notice={"kind": "errors", "message": f"Paused after {st['failures']} failed runs in a row. "
                                  "Check the log, then resume.", "action": "/logs"})
        return dict(st)
    return _mutate(sid, fn)


def defer(sid: str, now: float | None = None, delay_s: int = 60) -> None:
    """A run could not start after all (the collector got busy): retry shortly, once."""
    now = time.time() if now is None else now

    def fn(st):
        st.pop("running", None)
        st["next_run_at"] = now + delay_s
    _mutate(sid, fn)


def skip(sid: str, holder: str | None, now: float | None = None) -> None:
    """Another device holds this search's lock: count this slot as run elsewhere."""
    now = time.time() if now is None else now
    search = store.get_search(sid) or {}

    def fn(st):
        st["last_skip"] = {"at": now, "holder": holder}
        cron = cron_for(search.get("schedule"))
        if cron:
            st["next_run_at"] = next_after(cron, now, (search.get("schedule") or {}).get("tz"))
    _mutate(sid, fn)


def pause(sid: str, reason: str = "user") -> dict:
    def fn(st):
        st.update(paused=True, paused_reason=reason, spec_at=time.time())
        return dict(st)
    return _mutate(sid, fn)


def resume(sid: str, now: float | None = None) -> dict:
    """Resume after a pause: clears the notice and backoff; no catch-up burst (next slot from now)."""
    now = time.time() if now is None else now
    search = store.get_search(sid) or {}

    def fn(st):
        for k in ("paused", "paused_reason", "notice", "backoff_until", "interrupted"):
            st.pop(k, None)
        st["failures"] = 0
        st["spec_at"] = now
        cron = cron_for(search.get("schedule"))
        if cron:
            st["next_run_at"] = next_after(cron, now, (search.get("schedule") or {}).get("tz"))
        return dict(st)
    return _mutate(sid, fn)


def record_remote_runs(rows: list[dict], device: str) -> int:
    """Runs another device or bot recorded in the sync repo: advance our next run past them."""
    by_id: dict[str, float] = {}
    for r in rows:
        if not isinstance(r, dict) or r.get("device") == device or not r.get("search_id"):
            continue
        try:
            ts = datetime.fromisoformat(str(r.get("started_at"))).timestamp()
        except ValueError:
            continue
        by_id[r["search_id"]] = max(by_id.get(r["search_id"], 0), ts)
    n = 0
    searches = {s["id"]: s for s in store.list_searches()}
    with _flock():
        d = _read()
        for sid, ts in by_id.items():
            s = searches.get(sid)
            st = d.setdefault(sid, {})
            if not s or ts <= float(st.get("last_run_at") or 0):
                continue
            st["last_run_at"] = ts
            st["last_remote_run"] = _iso(ts)
            cron = cron_for(s.get("schedule"))
            if cron:
                _schedule_next(st, cron, (s.get("schedule") or {}).get("tz"), ts)
            n += 1
        _write(d)
    return n


def public_state(sid: str, st: dict | None = None) -> dict:
    st = load_state().get(sid, {}) if st is None else st
    return {"next_run_at": _iso(st.get("next_run_at")), "last_run_at": _iso(st.get("last_run_at")),
            "last_outcome": st.get("last_outcome"), "running": _running(st, time.time()),
            "paused": bool(st.get("paused")), "paused_reason": st.get("paused_reason"),
            "notice": st.get("notice"), "backoff_until": _iso(st.get("backoff_until")),
            "failures": int(st.get("failures") or 0), "history": st.get("history", [])[-5:]}


def export_for_sync(device: str, now: float | None = None) -> dict:
    """What always-on agents need, written to ``schedules/searches.json`` in the private sync repo.

    Each entry carries the device that owns it and ``updated_at``, so devices merge their lists
    (:func:`merge_exports`) instead of overwriting each other."""
    now = time.time() if now is None else now
    state = load_state()
    out = []
    for s in store.list_searches():
        cron = cron_for(s.get("schedule"))
        if not cron:
            continue
        st = state.get(s["id"], {})
        out.append({"id": s["id"], "name": s["name"], "titles": s["titles"], "locations": s["locations"],
                    "mode": s["mode"], "max_jobs": s.get("max_jobs") or 0, "cron": cron,
                    "tz": (s.get("schedule") or {}).get("tz") or "local", "paused": bool(st.get("paused")),
                    "next_run_at": _iso(st.get("next_run_at")), "last_run_at": _iso(st.get("last_run_at")),
                    "device": device, "updated_at": _iso(st.get("spec_at")) or _iso(now)})
    return {"version": 2, "updated_by": device, "lock_ttl_s": AGENT_LOCK_TTL_S, "min_gap_s": MIN_GAP_S,
            "searches": out}


def _entries(doc) -> list[dict]:
    if isinstance(doc, str):
        try:
            doc = json.loads(doc) if doc.strip() else {}
        except ValueError:
            doc = {}
    rows = (doc or {}).get("searches") if isinstance(doc, dict) else None
    return [r for r in (rows or []) if isinstance(r, dict) and isinstance(r.get("id"), str)]


def merge_exports(base, ours: dict, device: str) -> dict:
    """Merge this device's export into the shared list: other devices' entries are kept, this
    device's entries are replaced (and dropped when unscheduled here). Last-writer-wins only per
    search id, by ``updated_at`` then device id, so two devices never erase each other's lists."""
    merged: dict[str, dict] = {}
    for r in _entries(base):
        if r.get("device") == device:
            continue          # ours are re-added below; ones we no longer schedule go away
        merged[r["id"]] = r
    for r in _entries(ours):
        cur = merged.get(r["id"])
        if cur is None or (str(r.get("updated_at") or ""), str(r.get("device") or "")) >= \
                (str(cur.get("updated_at") or ""), str(cur.get("device") or "")):
            merged[r["id"]] = r
    out = dict(ours)
    out["searches"] = sorted(merged.values(), key=lambda r: r["id"])
    return out


def merge_conflict(ours_blob: str | None, theirs_blob: str | None) -> str:
    """Resolve a git conflict on ``schedules/searches.json``: union by id, newest entry wins."""
    merged: dict[str, dict] = {}
    for r in _entries(theirs_blob or "") + _entries(ours_blob or ""):
        cur = merged.get(r["id"])
        if cur is None or (str(r.get("updated_at") or ""), str(r.get("device") or "")) >= \
                (str(cur.get("updated_at") or ""), str(cur.get("device") or "")):
            merged[r["id"]] = r
    head = {}
    for blob in (theirs_blob, ours_blob):
        try:
            d = json.loads(blob or "{}")
            if isinstance(d, dict):
                head.update({k: v for k, v in d.items() if k != "searches"})
        except ValueError:
            pass
    head.setdefault("version", 2)
    head["searches"] = sorted(merged.values(), key=lambda r: r["id"])
    return json.dumps(head, indent=2, sort_keys=True) + "\n"


def runs_to_export(device: str, already: set[str]) -> list[dict]:
    rows = []
    for sid, st in load_state().items():
        for h in st.get("history", []):
            key = f"{sid}@{h.get('started_at')}"
            if h.get("started_at") and key not in already and h.get("device") in ("", device):
                rows.append({"search_id": sid, "device": device, "started_at": h["started_at"],
                             "finished_at": h.get("finished_at"), "outcome": h.get("outcome"), "_key": key})
    return rows
