"""Optional starter packs. Fictional examples (persona: Alex Example) to load and then edit.

Nothing here is a default: ApplyQueue applies only what the user loads, and every value can be
changed or deleted afterwards. Answers are generic policy answers, never claims about the user.
"""
from __future__ import annotations

try:
    from core.searches import store
except ImportError:  # pragma: no cover
    from backend.core.searches import store

PACKS: list[dict] = [
    {
        "id": "software",
        "name": "Software engineering",
        "description": "Backend, frontend and data roles; remote or two example cities.",
        "searches": [
            {"name": "Backend (remote)", "titles": ["Backend Engineer", "Software Engineer"], "locations": ["Remote"]},
            {"name": "Frontend", "titles": ["Frontend Engineer", "React Developer"], "locations": ["Springfield", "Remote"]},
        ],
        "categories": [
            {"name": "Backend", "keywords": ["backend", "api", "python", "go", "java", "distributed"]},
            {"name": "Frontend", "keywords": ["frontend", "react", "typescript", "ui"]},
            {"name": "Data", "keywords": ["data engineer", "analytics", "sql", "etl"]},
        ],
        "bans": {"companies": ["Example Staffing Agency"], "title_keywords": ["principal", "director", "unpaid"],
                 "description_keywords": []},
        "answers": [{"question": "How did you hear about this position?", "answer": "Online job board"}],
    },
    {
        "id": "healthcare",
        "name": "Healthcare",
        "description": "Nursing and patient-care roles near an example city.",
        "searches": [{"name": "Registered nurse", "titles": ["Registered Nurse", "RN"], "locations": ["Springfield"]}],
        "categories": [
            {"name": "Acute care", "keywords": ["icu", "emergency", "med-surg", "acute"]},
            {"name": "Clinic", "keywords": ["clinic", "outpatient", "ambulatory"]},
        ],
        "bans": {"companies": [], "title_keywords": ["travel"], "description_keywords": []},
        "answers": [{"question": "How did you hear about this position?", "answer": "Online job board"}],
    },
    {
        "id": "retail",
        "name": "Retail and hospitality",
        "description": "Store, cafe and front-desk roles, part-time or full-time.",
        "searches": [{"name": "Store associate", "titles": ["Retail Associate", "Barista", "Front Desk Agent"],
                      "locations": ["Springfield"]}],
        "categories": [
            {"name": "Retail", "keywords": ["retail", "store", "cashier", "sales associate"]},
            {"name": "Hospitality", "keywords": ["hotel", "barista", "cafe", "front desk"]},
        ],
        "bans": {"companies": [], "title_keywords": ["commission only"], "description_keywords": []},
        "answers": [],
    },
    {
        "id": "new-grad",
        "name": "New graduate / internships",
        "description": "Entry-level and internship searches with senior roles filtered out.",
        "searches": [{"name": "Entry level", "titles": ["Graduate Analyst", "Junior Developer", "Intern"],
                      "locations": ["Remote"]}],
        "categories": [{"name": "Internship", "keywords": ["intern", "internship", "co-op"]}],
        "bans": {"companies": [], "title_keywords": ["senior", "staff", "lead", "manager"], "description_keywords": []},
        "answers": [],
    },
]


def public() -> list[dict]:
    return [{k: p[k] for k in ("id", "name", "description")} |
            {"searches": len(p["searches"]), "categories": [c["name"] for c in p["categories"]]} for p in PACKS]


def apply(pack_id: str) -> dict:
    """Merge a pack into the user's config (skips duplicates by name). Searches start in review-first mode."""
    pack = next((p for p in PACKS if p["id"] == pack_id), None)
    if not pack:
        raise store.ValidationError("Unknown starter pack")
    cfg = store.load()
    names = {s["name"].lower() for s in cfg["searches"]}
    added = 0
    for s in pack["searches"]:
        if s["name"].lower() not in names:
            store.upsert_search({**s, "mode": "review"})
            added += 1
    rules = store.load()["rules"]
    cat_names = {c["name"].lower() for c in rules["categories"]}
    cats = rules["categories"] + [c for c in pack["categories"] if c["name"].lower() not in cat_names]
    bans = {k: rules["bans"].get(k, []) + [x for x in pack["bans"].get(k, []) if x not in rules["bans"].get(k, [])]
            for k in ("companies", "title_keywords", "description_keywords")}
    qs = {a["question"].lower() for a in rules["answers"]}
    answers = rules["answers"] + [a for a in pack["answers"] if a["question"].lower() not in qs]
    store.set_rules({"categories": cats, "bans": bans, "answers": answers})
    return {"searches_added": added, "categories": len(cats)}
