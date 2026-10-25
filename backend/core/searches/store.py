"""Persistent config for saved searches, rules (categories, bans, answers), resumes and outreach.

One JSON file in the app data dir. All input is validated and size-capped here so the API layer
can pass request bodies straight through.
"""
from __future__ import annotations

import json
import re
import threading
import time
import uuid
from pathlib import Path

try:
    from core.config import get_data_dir
except ImportError:  # pragma: no cover
    from backend.core.config import get_data_dir

FILE = "searches.json"
DRAFTS_FILE = "outreach_drafts.json"
MODES = ("review", "auto")
_lock = threading.RLock()

DEFAULT_OUTREACH_TEMPLATE = (
    "Hi {recruiter},\n\nI just applied for the {title} role at {company} and wanted to say hello. "
    "{pitch}\n\nThanks for your time,\n{name}"
)


class ValidationError(ValueError):
    pass


def _path() -> Path:
    return get_data_dir() / FILE


def _empty() -> dict:
    return {
        "searches": [],
        "rules": {"categories": [], "bans": {"companies": [], "title_keywords": [], "description_keywords": []},
                  "answers": []},
        "resumes": [],
        "outreach": {"enabled": False, "template": DEFAULT_OUTREACH_TEMPLATE},
    }


def load() -> dict:
    with _lock:
        data = _empty()
        p = _path()
        if p.exists():
            try:
                raw = json.loads(p.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                raw = {}
            for k in data:
                if isinstance(raw.get(k), type(data[k])):
                    data[k] = raw[k]
            data["rules"] = {**_empty()["rules"], **data["rules"]}
            data["rules"]["bans"] = {**_empty()["rules"]["bans"], **(data["rules"].get("bans") or {})}
            data["outreach"] = {**_empty()["outreach"], **data["outreach"]}
        return data


def save(data: dict) -> None:
    with _lock:
        p = _path()
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8", newline="\n")
        tmp.replace(p)


# ── validation helpers ───────────────────────────────────────────────────
def _text(v, limit: int = 100, required: bool = False, field: str = "value") -> str:
    s = " ".join(str(v or "").split())[:limit]
    if required and not s:
        raise ValidationError(f"{field} is required")
    return s


def _list(v, limit: int = 30, item_limit: int = 100) -> list[str]:
    if isinstance(v, str):
        v = re.split(r"[,\n]", v)
    out: list[str] = []
    for x in v or []:
        s = _text(x, item_limit)
        if s and s.lower() not in {o.lower() for o in out}:
            out.append(s)
    return out[:limit]


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


# ── searches ─────────────────────────────────────────────────────────────
def clean_search(body: dict, existing: dict | None = None) -> dict:
    cur = dict(existing or {})
    merged = {**cur, **{k: v for k, v in (body or {}).items() if v is not None}}
    titles = _list(merged.get("titles"), 20)
    if not titles:
        raise ValidationError("Add at least one job title")
    mode = merged.get("mode") or "review"
    if mode not in MODES:
        raise ValidationError("mode must be 'review' or 'auto'")
    filters = merged.get("filters") or {}
    if not isinstance(filters, dict):
        raise ValidationError("filters must be an object")
    bans = merged.get("bans") or {}
    from .schedule import clean_schedule  # local import: schedule imports this module
    schedule = clean_schedule(merged.get("schedule"))
    return {
        "id": cur.get("id") or _new_id("s"),
        "name": _text(merged.get("name") or titles[0], 80, True, "name"),
        "titles": titles,
        "locations": _list(merged.get("locations"), 20),
        "mode": mode,
        "enabled": bool(merged.get("enabled", True)),
        "resume_id": _text(merged.get("resume_id"), 40) or None,
        "category_id": _text(merged.get("category_id"), 40) or None,
        "max_jobs": max(0, min(int(merged.get("max_jobs") or 0), 500)),
        "filters": {_text(k, 40): _text(v, 200) for k, v in list(filters.items())[:20] if _text(k, 40)},
        "bans": {"companies": _list(bans.get("companies")), "keywords": _list(bans.get("keywords"))},
        "created_at": cur.get("created_at") or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "last_run_at": cur.get("last_run_at"),
        "schedule": schedule,
    }


def list_searches() -> list[dict]:
    return load()["searches"]


def get_search(sid: str) -> dict | None:
    return next((s for s in list_searches() if s.get("id") == sid), None)


def upsert_search(body: dict) -> dict:
    with _lock:
        data = load()
        existing = next((s for s in data["searches"] if s["id"] == body.get("id")), None) if body.get("id") else None
        if body.get("id") and not existing:
            raise ValidationError("Unknown search")
        s = clean_search(body, existing)
        if s.get("resume_id") and not any(r["id"] == s["resume_id"] for r in data["resumes"]):
            raise ValidationError("Unknown resume")
        if existing:
            data["searches"] = [s if x["id"] == s["id"] else x for x in data["searches"]]
        else:
            if len(data["searches"]) >= 50:
                raise ValidationError("Up to 50 saved searches")
            data["searches"].append(s)
        save(data)
        return s


def touch_search(sid: str) -> None:
    with _lock:
        data = load()
        for s in data["searches"]:
            if s["id"] == sid:
                s["last_run_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        save(data)


def delete_search(sid: str) -> bool:
    with _lock:
        data = load()
        n = len(data["searches"])
        data["searches"] = [s for s in data["searches"] if s["id"] != sid]
        save(data)
        return len(data["searches"]) < n


# ── rules: categories, bans, answers ─────────────────────────────────────
def clean_rules(body: dict, resumes: list[dict]) -> dict:
    cur = load()["rules"]
    body = body or {}
    rids = {r["id"] for r in resumes}
    cats = []
    for c in (body.get("categories") if "categories" in body else cur["categories"]) or []:
        name = _text(c.get("name"), 60)
        if not name:
            continue
        cats.append({"id": _text(c.get("id"), 40) or _new_id("c"), "name": name,
                     "keywords": _list(c.get("keywords"), 40, 60),
                     "resume_id": c.get("resume_id") if c.get("resume_id") in rids else None})
    bans_in = body.get("bans") if "bans" in body else cur["bans"]
    bans = {k: _list((bans_in or {}).get(k), 200, 100) for k in ("companies", "title_keywords", "description_keywords")}
    answers = []
    for a in (body.get("answers") if "answers" in body else cur["answers"]) or []:
        q, ans = _text(a.get("question"), 300), str(a.get("answer") or "").strip()[:2000]
        if q and ans:
            answers.append({"question": q, "answer": ans})
    return {"categories": cats[:50], "bans": bans, "answers": answers[:300]}


def set_rules(body: dict) -> dict:
    with _lock:
        data = load()
        data["rules"] = clean_rules(body, data["resumes"])
        save(data)
        return data["rules"]


def answers_dict() -> dict[str, str]:
    return {a["question"]: a["answer"] for a in load()["rules"]["answers"]}


# ── resumes ──────────────────────────────────────────────────────────────
RESUME_EXT = (".pdf", ".docx", ".doc", ".txt", ".md")


def add_resume(body: dict) -> dict:
    path = str((body or {}).get("path") or "").strip()
    p = Path(path).expanduser()
    if not path or not p.is_file():
        raise ValidationError("Resume file not found")
    if p.suffix.lower() not in RESUME_EXT:
        raise ValidationError("Use a PDF, Word or text resume")
    with _lock:
        data = load()
        rid = body.get("id")
        existing = next((r for r in data["resumes"] if r["id"] == rid), None) if rid else None
        r = {
            "id": (existing or {}).get("id") or _new_id("r"),
            "name": _text(body.get("name") or p.stem, 80),
            "path": str(p),
            "keywords": _list(body.get("keywords"), 40, 60),
            "default": bool(body.get("default")) or not data["resumes"],
        }
        if r["default"]:
            for x in data["resumes"]:
                x["default"] = False
        if existing:
            data["resumes"] = [r if x["id"] == r["id"] else x for x in data["resumes"]]
        else:
            if len(data["resumes"]) >= 20:
                raise ValidationError("Up to 20 resumes")
            data["resumes"].append(r)
        save(data)
        return r


def delete_resume(rid: str) -> bool:
    with _lock:
        data = load()
        n = len(data["resumes"])
        data["resumes"] = [r for r in data["resumes"] if r["id"] != rid]
        if data["resumes"] and not any(r.get("default") for r in data["resumes"]):
            data["resumes"][0]["default"] = True
        for s in data["searches"]:
            if s.get("resume_id") == rid:
                s["resume_id"] = None
        for c in data["rules"]["categories"]:
            if c.get("resume_id") == rid:
                c["resume_id"] = None
        save(data)
        return len(data["resumes"]) < n


# ── outreach (off by default, drafts only, never sent) ───────────────────
def set_outreach(body: dict) -> dict:
    with _lock:
        data = load()
        o = data["outreach"]
        if "enabled" in body:
            o["enabled"] = bool(body["enabled"])
        if "template" in body:
            o["template"] = str(body["template"] or "").strip()[:3000] or DEFAULT_OUTREACH_TEMPLATE
        save(data)
        return o


def _drafts_path() -> Path:
    return get_data_dir() / DRAFTS_FILE


def list_drafts() -> list[dict]:
    p = _drafts_path()
    try:
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else []
    except (OSError, ValueError):
        return []


def save_drafts(drafts: list[dict]) -> None:
    p = _drafts_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(drafts[-200:], indent=2), encoding="utf-8", newline="\n")
