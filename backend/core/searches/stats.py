"""Per-search stats, resume A/B response stats and the tracker dashboard (pure functions)."""
from __future__ import annotations

try:
    from core import job_status as js
except ImportError:  # pragma: no cover
    from backend.core import job_status as js


def _flags(job: dict) -> dict:
    status = js.normalize_status(job.get("status"))
    applied = status in js.SUBMITTED_STATUSES
    return {
        "found": True,
        "review": job.get("review") == "pending" and status == js.PENDING,
        "applied": applied,
        "skipped": status == js.BLOCKED or bool(job.get("skip_reason")),
        "confirmed": status == js.CONFIRMED,
        "interviews": applied and job.get("response") == "interview",
        "rejections": applied and job.get("response") == "rejection",
    }


KEYS = ("found", "review", "applied", "skipped", "confirmed", "interviews", "rejections")


def _rates(c: dict) -> dict:
    a = c["applied"] or 0
    responses = c["interviews"] + c["rejections"]
    return {**c, "responses": responses,
            "response_rate": round(responses / a, 3) if a else 0.0,
            "interview_rate": round(c["interviews"] / a, 3) if a else 0.0}


def count(jobs: list[dict]) -> dict:
    c = {k: 0 for k in KEYS}
    for j in jobs:
        for k, v in _flags(j).items():
            c[k] += int(v)
    return _rates(c)


def tracker(jobs: dict, cfg: dict) -> dict:
    rows = [{**j, "url": j.get("url") or u} for u, j in jobs.items() if isinstance(j, dict)]
    searches = []
    for s in cfg.get("searches") or []:
        searches.append({"id": s["id"], "name": s["name"], "mode": s["mode"],
                         **count([j for j in rows if j.get("search_id") == s["id"]])})
    resumes = []
    for r in cfg.get("resumes") or []:
        resumes.append({"id": r["id"], "name": r["name"], **count([j for j in rows if j.get("resume_id") == r["id"]])})
    responses = sorted((j for j in rows if j.get("response")), key=lambda j: j.get("response_at") or "", reverse=True)
    return {
        "totals": count(rows),
        "searches": searches,
        "resumes": resumes,
        "unassigned": count([j for j in rows if not j.get("search_id")]),
        "recent_responses": [{"url": j["url"], "title": j.get("title"), "company": j.get("company"),
                              "response": j["response"], "at": j.get("response_at")} for j in responses[:20]],
        "review_queue": [{"url": j["url"], "title": j.get("title"), "company": j.get("company"),
                          "search_id": j.get("search_id"), "location": j.get("location")}
                         for j in rows if _flags(j)["review"]][:200],
    }
