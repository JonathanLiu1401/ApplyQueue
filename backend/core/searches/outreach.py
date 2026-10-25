"""Recruiter outreach: OFF by default and drafts only. ApplyQueue never sends these messages."""
from __future__ import annotations

import re
import time
import uuid

try:
    from core.searches import store
except ImportError:  # pragma: no cover
    from backend.core.searches import store


def _fill(template: str, values: dict) -> str:
    # Plain {name} substitution only (no str.format: user templates must not reach attributes).
    return re.sub(r"\{(\w+)\}", lambda m: str(values.get(m.group(1), m.group(0))), template)


def draft(job: dict, profile: dict) -> dict:
    cfg = store.load()["outreach"]
    if not cfg.get("enabled"):
        raise store.ValidationError("Recruiter outreach is off. Turn it on to create drafts.")
    name = (profile.get("name") or " ".join(x for x in (profile.get("first_name"), profile.get("last_name")) if x)
            or "Your name")
    skills = ", ".join((profile.get("skills") or [])[:3])
    pitch = f"My background in {skills} looks like a good fit." if skills else "I think my background is a good fit."
    body = _fill(cfg["template"], dict(
        recruiter=job.get("recruiter_name") or "there", title=job.get("title") or "open",
        company=job.get("company") or "your company", pitch=pitch, name=name))
    d = {"id": uuid.uuid4().hex[:12], "job_url": job.get("url"), "company": job.get("company"),
         "title": job.get("title"), "subject": f"{job.get('title') or 'Application'} - {name}",
         "body": body, "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "sent": False}
    drafts = store.list_drafts()
    drafts.append(d)
    store.save_drafts(drafts)
    return d


def delete(draft_id: str) -> bool:
    drafts = store.list_drafts()
    keep = [d for d in drafts if d.get("id") != draft_id]
    store.save_drafts(keep)
    return len(keep) < len(drafts)
