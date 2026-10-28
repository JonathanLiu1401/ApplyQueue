# jobq.py: queue, claims and ledger with file locks

Save the code block below as `{{STATE_DIR}}/bin/jobq.py` and `chmod +x` it. It needs a
recent Python 3 and nothing outside the standard library. Every agent on the box runs the same copy.

## Why it looks like this

- The whole team runs on one Grok Bot box, so there is nothing to sync: one folder, many readers,
  writes serialized by an exclusive `flock` on `{{STATE_DIR}}/.jobq.lock`.
- `queue.jsonl` and `ledger.jsonl` are **append-only**. Each write is one JSON line appended with
  `O_APPEND` and `fsync`ed under the lock. Nothing is rewritten in place, so there is no merge,
  no conflict and no lost row. A torn final line after a crash is skipped by readers.
- The latest ledger event per `job_id` is that job's status.
- `next` claims atomically: two workers asking at the same moment always get different jobs.
- `check` is the pre-submit gate: under the lock, the job must still be yours, not terminal, and no
  job with the same posting may already be submitted.
- `finish` refuses to record a submission without the confirmation email subject, and refuses
  subjects that look like a code, verification or password-reset email.

Do **not** replace any of this with a git repo plus pull/push. And never "refresh" files with
`git pull --rebase --autostash`: autostash silently stashes and re-applies local edits, so a
half-written ledger or a stale resume can be put back on top of the real one without anyone
noticing. If the optional local git repo exists, it is only for history: commit, never stash.

## Target format (captain, `jobq.py add`)

One JSON object per line on stdin. `company`, `title`, `url` are required.

```json
{"company": "Example Robotics Co", "title": "Embedded Software Intern", "url": "https://careers.example.com/jobs/123", "location": "Springfield, ST", "role_family": "embedded", "tier": 1, "req_id": "R-123", "posted": "2026-10-20", "source": "company site", "notes": "Summer only; no sponsorship language"}
```

Dedupe keys (any match is a duplicate): company + `req_id`; canonical URL (lowercased host
without `www.`, no trailing slash, tracking parameters such as `utm_*`, `src`, `ref`, `trk`,
`gh_src` removed); company + title + location. The same requisition on LinkedIn, Indeed,
Handshake and the company site therefore dedupes once a `req_id` is known. Search agents should
always send the `req_id` when the posting shows one.

## Event format (`ledger.jsonl`, written only by jobq.py)

```json
{"at": "2026-10-27T10:12:03-07:00", "job_id": "q-00042", "event": "submitted", "agent": "Apply 2", "confirmation_subject": "Thank you for applying to Example Robotics Co"}
```

Events: `claimed`, `released` (back to queue), `submitted`, `skipped` (policy or knockout, with
`reason`), `failed` (portal broke, captcha, account locked, with `reason`), `duplicate`.
Terminal: `submitted`, `skipped`, `failed`, `duplicate`. The captain can reopen a `failed` job
with `requeue`.

## Command summary

| Command | Who | Exit codes |
|---|---|---|
| `jobq.py add < targets.jsonl` | captain | 0 |
| `jobq.py next --agent "Apply 1" [--tier 1]` | apply agent | 0 card printed; 3 paused, empty or daily cap |
| `jobq.py resume-file <role_family>` | apply agent, before every upload | 0 path + sha256 + mtime; error if missing or not a PDF |
| `jobq.py check <job_id> --agent "Apply 1"` | apply agent, right before Submit | 0 safe; 1 do not submit; 3 paused |
| `jobq.py finish <job_id> --agent "Apply 1" --subject "<exact subject>"` | apply agent | 0; 1 refused |
| `jobq.py skip <job_id> --agent A --reason "<quoted posting line>"` | apply agent | 0; 1 |
| `jobq.py fail <job_id> --agent A --reason "<what broke>"` | apply agent | 0; 1 |
| `jobq.py release <job_id> --agent A` | apply agent on pause or hand-back | 0; 1 |
| `jobq.py requeue <job_id> --reason R` | captain | 0 |
| `jobq.py status` | anyone | 0 |
| `jobq.py pause` / `unpause` | captain only, on the user's word | 0 |
| `jobq.py export-csv > ledger.csv` | captain, for the user | 0 |

Claims expire after 60 minutes (`JOBQ_CLAIM_MINUTES`); the claiming agent can still `finish`
after expiry. Set `JOBQ_DIR` if the script does not live in `{{STATE_DIR}}/bin/`.

## Self-test (run once after installing)

```bash
cd "$(mktemp -d)" && mkdir -p bin resumes && cp {{STATE_DIR}}/bin/jobq.py bin/ && printf '%%PDF-1.4 test' > resumes/r.pdf
echo '{"documents":{"resume_map":{"default":"resumes/r.pdf"}}}' > profile.json
export JOBQ_DIR=$PWD
for i in $(seq 1 10); do echo "{\"company\":\"Example Co\",\"title\":\"Intern $i\",\"url\":\"https://careers.example.com/j/$i\"}"; done | python3 bin/jobq.py add
for i in $(seq 1 15); do python3 bin/jobq.py next --agent "W$i" > out$i.txt & done; wait
grep -h '"job_id"' out*.txt | sort | uniq -d    # must print nothing: no job claimed twice
grep -h '"job_id"' out*.txt | wc -l             # must print 10
unset JOBQ_DIR
```

## jobq.py

```python
#!/usr/bin/env python3
"""jobq: a tiny box-local job queue and ledger for a Grok Bot job-application team.

All state lives in one folder on the shared box ({{STATE_DIR}}). Every agent on the box
runs this same script. Writes happen under an exclusive flock on STATE_DIR/.jobq.lock and are
append-only JSONL lines, so concurrent workers can never claim the same job or lose a row.

Files (created on first use):
  queue.jsonl    one line per target added by the captain (never edited)
  ledger.jsonl   one line per event: claimed, released, submitted, skipped, failed, duplicate
  PAUSED         present = team is paused; next refuses to hand out work
  profile.json   read for resume_map (role family -> resume path) and daily cap

Usage (STATE_DIR defaults to $JOBQ_DIR, else the folder above this script):
  jobq.py add < targets.jsonl              captain: dedupe + append targets, prints counts
  jobq.py next --agent "Apply 1"           worker or captain: claim one job, print the card
  jobq.py check <job_id> --agent "Apply 1" before Submit: still yours, not applied elsewhere
  jobq.py finish <job_id> --agent A --subject "Thank you for applying to X" [--snippet ...]
  jobq.py skip <job_id> --agent A --reason "explicit no-OPT line in posting"
  jobq.py fail <job_id> --agent A --reason "portal error after 3 tries"
  jobq.py release <job_id> --agent A       give the job back to the queue (pause, crash)
  jobq.py requeue <job_id> --reason R      captain: reopen a failed or skipped job
  jobq.py resume-file <role_family>        verify the resume for a role (exists, PDF, sha256)
  jobq.py status                           counts, live claims, today's submits
  jobq.py pause | unpause                  create or remove PAUSED
  jobq.py export-csv > ledger.csv          human-readable latest status per job
Exit codes: 0 ok, 1 refused (duplicate, not yours, bad input), 3 paused or nothing to do.
"""
from __future__ import annotations

import argparse
import contextlib
import csv
import fcntl
import hashlib
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

STATE_DIR = Path(os.environ.get("JOBQ_DIR") or Path(__file__).resolve().parent.parent)
QUEUE = STATE_DIR / "queue.jsonl"
LEDGER = STATE_DIR / "ledger.jsonl"
LOCK = STATE_DIR / ".jobq.lock"
PAUSED = STATE_DIR / "PAUSED"
PROFILE = STATE_DIR / "profile.json"

CLAIM_TTL = timedelta(minutes=int(os.environ.get("JOBQ_CLAIM_MINUTES", "60")))
TERMINAL = {"submitted", "skipped", "duplicate", "failed"}
TRACKING = re.compile(r"^(utm_.*|src|source|ref|refid|trk|trackingid|gh_src|lever-source|iis|iisn|from|feedid)$", re.I)
NOT_CONFIRMATION = re.compile(
    r"security code|verification code|verify your|one[- ]time|passcode|\bcode\b|reset your password|"
    r"password reset|confirm your email|activate your account|complete your (profile|account)", re.I)


def now() -> datetime:
    return datetime.now(timezone.utc).astimezone()


@contextlib.contextmanager
def locked():
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    with open(LOCK, "a+") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue  # a torn line from a crash is ignored, never fatal
        if isinstance(row, dict):
            rows.append(row)
    return rows


def append(path: Path, row: dict) -> None:
    data = (json.dumps(row, ensure_ascii=False) + "\n").encode("utf-8")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        os.write(fd, data)
        os.fsync(fd)
    finally:
        os.close(fd)


def canon_url(url: str) -> str:
    p = urlsplit((url or "").strip())
    q = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True) if not TRACKING.match(k)]
    path = p.path.rstrip("/")
    return urlunsplit((p.scheme.lower() or "https", p.netloc.lower().removeprefix("www."), path, urlencode(sorted(q)), ""))


def norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def dedupe_keys(t: dict) -> set[str]:
    keys = set()
    if t.get("req_id") and t.get("company"):
        keys.add(f"req:{norm(t['company'])}:{norm(str(t['req_id']))}")
    if t.get("url"):
        keys.add(f"url:{canon_url(t['url'])}")
    if t.get("company") and t.get("title") and t.get("location"):
        keys.add(f"ctl:{norm(t['company'])}:{norm(t['title'])}:{norm(t['location'])}")
    return keys


def state() -> tuple[dict[str, dict], dict[str, dict]]:
    """Return (jobs by id, latest event by id)."""
    jobs = {r["job_id"]: r for r in read_jsonl(QUEUE) if r.get("job_id")}
    latest: dict[str, dict] = {}
    for ev in read_jsonl(LEDGER):
        if ev.get("job_id"):
            latest[ev["job_id"]] = ev
    return jobs, latest


def live_claim(ev: dict | None) -> bool:
    if not ev or ev.get("event") != "claimed":
        return False
    try:
        at = datetime.fromisoformat(ev["at"])
    except (KeyError, ValueError):
        return False
    return now() - at < CLAIM_TTL


def load_profile() -> dict:
    try:
        return json.loads(PROFILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def resume_info(role_family: str) -> dict:
    """Pick the resume for a role family from profile.json resume_map and verify the file."""
    rmap = (load_profile().get("documents") or {}).get("resume_map") or {}
    rel = rmap.get(role_family) or rmap.get("default")
    if not rel:
        raise SystemExit(f"error: no resume_map entry for {role_family!r} and no default in profile.json")
    path = (STATE_DIR / rel).resolve()
    if not path.is_file() or path.stat().st_size == 0:
        raise SystemExit(f"error: resume missing or empty: {path}")
    with open(path, "rb") as fh:
        if fh.read(5) != b"%PDF-":
            raise SystemExit(f"error: resume is not a PDF: {path}")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    mtime = datetime.fromtimestamp(path.stat().st_mtime).astimezone().isoformat(timespec="seconds")
    return {"path": str(path), "sha256": digest, "mtime": mtime}


def event(job_id: str, ev: str, agent: str, **extra) -> dict:
    row = {"at": now().isoformat(timespec="seconds"), "job_id": job_id, "event": ev, "agent": agent}
    row.update({k: v for k, v in extra.items() if v not in (None, "")})
    append(LEDGER, row)
    return row


def applied_keys(jobs: dict, latest: dict, exclude: str = "") -> set[str]:
    keys: set[str] = set()
    for jid, ev in latest.items():
        if jid != exclude and ev.get("event") in ("submitted", "duplicate") and jid in jobs:
            keys |= dedupe_keys(jobs[jid])
    return keys


def cmd_add(args) -> int:
    added = dup = bad = 0
    with locked():
        jobs, latest = state()
        seen: set[str] = set()
        for t in jobs.values():
            seen |= dedupe_keys(t)
        n = len(jobs)
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                t = json.loads(line)
            except json.JSONDecodeError:
                bad += 1
                continue
            if not t.get("url") or not t.get("company") or not t.get("title"):
                bad += 1
                continue
            keys = dedupe_keys(t)
            if keys & seen:
                dup += 1
                continue
            n += 1
            t.setdefault("tier", 1)
            t["job_id"] = f"q-{n:05d}"
            t["queued_at"] = now().isoformat(timespec="seconds")
            append(QUEUE, t)
            seen |= keys
            added += 1
    print(json.dumps({"queued": added, "duplicate": dup, "invalid": bad}))
    return 0


def cmd_next(args) -> int:
    with locked():
        if PAUSED.exists():
            print("PAUSED: do not start a new job")
            return 3
        jobs, latest = state()
        # An agent that already holds a live claim gets the same job back.
        for jid, ev in latest.items():
            if live_claim(ev) and ev.get("agent") == args.agent and jid in jobs:
                return print_card(jobs[jid], resumed=True)
        cap = int((load_profile().get("team") or {}).get("daily_cap") or 0)
        if cap and today_submits(latest) >= cap:
            print(f"DAILY CAP {cap} reached")
            return 3
        done = applied_keys(jobs, latest)
        open_jobs = []
        for jid, t in jobs.items():
            ev = latest.get(jid)
            if ev and (ev.get("event") in TERMINAL or live_claim(ev)):
                continue
            if args.tier and int(t.get("tier", 1)) > args.tier:
                continue
            if dedupe_keys(t) & done:
                event(jid, "duplicate", args.agent, reason="same posting already submitted")
                continue
            open_jobs.append(t)
        if not open_jobs:
            print("EMPTY: no queued job")
            return 3
        open_jobs.sort(key=lambda t: (int(t.get("tier", 1)), t.get("queued_at", ""), t["job_id"]))
        job = open_jobs[0]
        event(job["job_id"], "claimed", args.agent)
        return print_card(job)


def print_card(job: dict, resumed: bool = False) -> int:
    try:
        resume = resume_info(job.get("role_family") or "default")
    except SystemExit as e:
        resume = {"error": str(e)}
    card = {k: job.get(k) for k in ("job_id", "company", "title", "url", "location", "role_family", "tier", "req_id", "notes")}
    card["resume"] = resume
    card["resumed_claim"] = resumed
    print(json.dumps(card, indent=2, ensure_ascii=False))
    return 0


def today_submits(latest: dict) -> int:
    today = now().date().isoformat()
    return sum(1 for ev in latest.values() if ev.get("event") == "submitted" and str(ev.get("at", "")).startswith(today))


def owned(jid: str, agent: str, latest: dict) -> str:
    ev = latest.get(jid)
    if not ev:
        return f"{jid} was never claimed"
    if ev.get("event") in TERMINAL:
        return f"{jid} is already {ev['event']} by {ev.get('agent')}"
    if ev.get("event") != "claimed" or ev.get("agent") != agent:
        return f"{jid} is not claimed by {agent} (last: {ev.get('event')} by {ev.get('agent')})"
    return ""


def cmd_check(args) -> int:
    with locked():
        if PAUSED.exists():
            print("PAUSED: do not submit")
            return 3
        jobs, latest = state()
        why = owned(args.job_id, args.agent, latest)
        if not why and dedupe_keys(jobs.get(args.job_id, {})) & applied_keys(jobs, latest, exclude=args.job_id):
            why = "DUPLICATE: the same posting is already submitted under another job_id"
        if why:
            print(why)
            return 1
        if not live_claim(latest.get(args.job_id)):
            event(args.job_id, "claimed", args.agent, note="claim refreshed by check")
        print("OK: yours, not applied anywhere, safe to submit")
        return 0


def cmd_finish(args) -> int:
    subject = (args.subject or "").strip()
    if not subject:
        print("refused: finish needs the confirmation email subject")
        return 1
    if NOT_CONFIRMATION.search(subject):
        print("refused: that subject looks like a code, verification or password email, not a confirmation")
        return 1
    with locked():
        jobs, latest = state()
        ev = latest.get(args.job_id)
        # A claim that expired while the agent waited for the email is still finishable by that agent.
        if ev and ev.get("event") == "claimed" and ev.get("agent") == args.agent:
            why = ""
        else:
            why = owned(args.job_id, args.agent, latest)
        if why:
            print(why)
            return 1
        event(args.job_id, "submitted", args.agent, confirmation_subject=subject, snippet=args.snippet)
    print(f"submitted {args.job_id}")
    return 0


def _close(args, ev_name: str) -> int:
    if not (args.reason or "").strip():
        print(f"refused: {ev_name} needs --reason")
        return 1
    with locked():
        _, latest = state()
        ev = latest.get(args.job_id)
        if not (ev and ev.get("event") == "claimed" and ev.get("agent") == args.agent):
            print(owned(args.job_id, args.agent, latest) or "not yours")
            return 1
        event(args.job_id, ev_name, args.agent, reason=args.reason)
    print(f"{ev_name} {args.job_id}")
    return 0


def cmd_release(args) -> int:
    with locked():
        _, latest = state()
        ev = latest.get(args.job_id)
        if not (ev and ev.get("event") == "claimed" and ev.get("agent") == args.agent):
            print(owned(args.job_id, args.agent, latest) or "not yours")
            return 1
        event(args.job_id, "released", args.agent, reason=args.reason)
    print(f"released {args.job_id}")
    return 0


def cmd_requeue(args) -> int:
    with locked():
        jobs, _ = state()
        if args.job_id not in jobs:
            print("unknown job_id")
            return 1
        event(args.job_id, "released", "captain", reason=args.reason or "requeued")
    print(f"requeued {args.job_id}")
    return 0


def cmd_status(args) -> int:
    with locked():
        jobs, latest = state()
    counts: dict[str, int] = {}
    claims = []
    for jid in jobs:
        ev = latest.get(jid)
        name = "queued" if not ev or ev.get("event") == "released" else ev["event"]
        if name == "claimed" and not live_claim(ev):
            name = "queued (claim expired)"
        counts[name] = counts.get(name, 0) + 1
        if live_claim(ev):
            claims.append({"job_id": jid, "agent": ev.get("agent"), "since": ev.get("at"), "company": jobs[jid].get("company")})
    print(json.dumps({"paused": PAUSED.exists(), "counts": counts, "live_claims": claims,
                      "submitted_today": today_submits(latest)}, indent=2, ensure_ascii=False))
    return 0


def cmd_resume_file(args) -> int:
    print(json.dumps(resume_info(args.role_family), indent=2))
    return 0


def cmd_export(args) -> int:
    with locked():
        jobs, latest = state()
    w = csv.writer(sys.stdout)
    w.writerow(["job_id", "status", "at", "agent", "company", "title", "url", "tier", "role_family", "confirmation_subject", "reason"])
    for jid, t in jobs.items():
        ev = latest.get(jid) or {}
        w.writerow([jid, ev.get("event", "queued"), ev.get("at", ""), ev.get("agent", ""), t.get("company"), t.get("title"),
                    t.get("url"), t.get("tier"), t.get("role_family", ""), ev.get("confirmation_subject", ""), ev.get("reason", "")])
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("add").set_defaults(fn=cmd_add)
    p = sub.add_parser("next"); p.add_argument("--agent", required=True); p.add_argument("--tier", type=int, default=0); p.set_defaults(fn=cmd_next)
    p = sub.add_parser("check"); p.add_argument("job_id"); p.add_argument("--agent", required=True); p.set_defaults(fn=cmd_check)
    p = sub.add_parser("finish"); p.add_argument("job_id"); p.add_argument("--agent", required=True)
    p.add_argument("--subject", required=True); p.add_argument("--snippet", default=""); p.set_defaults(fn=cmd_finish)
    for name in ("skip", "fail"):
        p = sub.add_parser(name); p.add_argument("job_id"); p.add_argument("--agent", required=True); p.add_argument("--reason", required=True)
        p.set_defaults(fn=lambda a, n={"skip": "skipped", "fail": "failed"}[name]: _close(a, n))
    p = sub.add_parser("release"); p.add_argument("job_id"); p.add_argument("--agent", required=True); p.add_argument("--reason", default=""); p.set_defaults(fn=cmd_release)
    p = sub.add_parser("requeue"); p.add_argument("job_id"); p.add_argument("--reason", default=""); p.set_defaults(fn=cmd_requeue)
    p = sub.add_parser("resume-file"); p.add_argument("role_family"); p.set_defaults(fn=cmd_resume_file)
    sub.add_parser("status").set_defaults(fn=cmd_status)
    sub.add_parser("pause").set_defaults(fn=lambda a: (PAUSED.touch(), print("paused"), 0)[2])
    sub.add_parser("unpause").set_defaults(fn=lambda a: (PAUSED.unlink(missing_ok=True), print("unpaused"), 0)[2])
    sub.add_parser("export-csv").set_defaults(fn=cmd_export)
    args = ap.parse_args()
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
```
