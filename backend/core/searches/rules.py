"""Pure helpers: bans, categories and resume routing (no I/O)."""
from __future__ import annotations

import re


def _has(hay: str, needle: str) -> bool:
    needle = needle.strip().lower()
    if not needle:
        return False
    return re.search(r"(?<![a-z0-9])" + re.escape(needle) + r"(?![a-z0-9])", hay.lower()) is not None


def ban_reason(job: dict, rules: dict, search: dict | None = None) -> str | None:
    """Why this job must be skipped, or None. Global bans plus the search's own bans."""
    bans = (rules or {}).get("bans") or {}
    sbans = (search or {}).get("bans") or {}
    company = job.get("company") or ""
    title = job.get("title") or ""
    desc = job.get("description") or ""
    for c in list(bans.get("companies") or []) + list(sbans.get("companies") or []):
        if c and (company.strip().lower() == c.strip().lower() or _has(company, c)):
            return f"Banned company: {c}"
    for k in list(bans.get("title_keywords") or []) + list(sbans.get("keywords") or []):
        if _has(title, k):
            return f"Banned title keyword: {k}"
    for k in list(bans.get("description_keywords") or []) + list(sbans.get("keywords") or []):
        if desc and _has(desc, k):
            return f"Banned keyword in description: {k}"
    return None


def category_for(job: dict, rules: dict) -> dict | None:
    """First user-defined category whose keyword is in the title (then the description)."""
    cats = (rules or {}).get("categories") or []
    title, desc = job.get("title") or "", job.get("description") or ""
    for field in (title, desc):
        for c in cats:
            if any(_has(field, k) for k in c.get("keywords") or []):
                return c
    return None


def route_resume(job: dict, cfg: dict) -> tuple[dict | None, str]:
    """Pick a resume: the search's, then the category's, then best keyword overlap, then default."""
    resumes = {r["id"]: r for r in cfg.get("resumes") or []}
    if not resumes:
        return None, "none"
    search = next((s for s in cfg.get("searches") or [] if s["id"] == job.get("search_id")), None)
    if search and search.get("resume_id") in resumes:
        return resumes[search["resume_id"]], "search"
    cat = category_for(job, cfg.get("rules") or {})
    if cat and cat.get("resume_id") in resumes:
        return resumes[cat["resume_id"]], "category"
    hay = f"{job.get('title') or ''} {job.get('description') or ''}"
    scored = sorted(((sum(1 for k in r.get("keywords") or [] if _has(hay, k)), r) for r in resumes.values()),
                    key=lambda x: -x[0])
    if scored and scored[0][0] > 0:
        return scored[0][1], "keywords"
    default = next((r for r in resumes.values() if r.get("default")), next(iter(resumes.values())))
    return default, "default"
