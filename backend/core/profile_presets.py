"""Profile answers the user never gave: a real "not answered" state.

Before schema 2 the profile shipped with preset values (sponsorship No, relocation No, work mode
hybrid, salary 50000-100000, 0 years, English, US / +1). An untouched preset could not be told apart
from a choice, so ApplyQueue submitted invented answers. Now:

- a preset field starts **unset** (``None`` / ``""`` / ``[]``) until the user answers it;
- ``migrate`` upgrades an old profile file (no ``_schema``): a field that is missing, or that sits
  in a profile the user never filled in at all, becomes unset; a value that differs from the old
  preset is the user's and stays; a value equal to the old preset is ambiguous, so it is **kept**
  and listed in ``_confirm`` until the user confirms or changes it;
- ``for_apply`` is the view the apply engine uses: unset and unconfirmed fields are absent, so they
  are never typed into a form. Preference questions (sponsorship, relocation, salary, work mode)
  cannot be answered from a résumé either, so a form that asks one goes to a person.
"""
from __future__ import annotations

import copy
from typing import Any

SCHEMA = 2
SCHEMA_KEY = "_schema"
CONFIRM_KEY = "_confirm"

# dotted path -> (value of the old preset, unset value, label)
PRESETS: dict[str, tuple[Any, Any, str]] = {
    "visa_sponsorship_needed": (False, None, "Visa sponsorship needed"),
    "willing_to_relocate": (False, None, "Willing to relocate"),
    "preferred_work_mode": ("hybrid", "", "Preferred work mode"),
    "salary_expectation.min": (50000, None, "Minimum salary"),
    "salary_expectation.max": (100000, None, "Maximum salary"),
    "years_of_experience": (0, None, "Years of experience"),
    "languages": (["English"], [], "Languages"),
    "country": ("US", "", "Country"),
    "address.country": ("USA", "", "Address country"),
    "phone_country_code": ("+1", "", "Phone country code"),
}
# Questions about these can only come from the user (not from the résumé): unset means a person.
PREFERENCES = ("visa_sponsorship_needed", "willing_to_relocate", "preferred_work_mode", "salary_expectation.min")

# Anything the user typed about themselves. A profile with none of it was never filled in, so its
# preset values were never chosen either.
_USER_CONTENT = ("name", "email", "phone", "current_role", "current_company", "work_authorization",
                 "notice_period", "nationality", "date_of_birth", "cover_letter", "notes", "linkedin",
                 "github", "website", "skills", "target_job_titles", "target_locations",
                 "address.street", "address.city", "address.state", "address.zip",
                 "education.degree", "education.school", "education.graduation")

_MISSING = object()


def get_path(d: Any, path: str, default: Any = None) -> Any:
    cur = d
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return default
        cur = cur[part]
    return cur


def _has(d: Any, path: str) -> bool:
    return get_path(d, path, _MISSING) is not _MISSING


def set_path(d: dict, path: str, value: Any) -> None:
    parts = path.split(".")
    cur = d
    for part in parts[:-1]:
        if not isinstance(cur.get(part), dict):
            cur[part] = {}
        cur = cur[part]
    cur[parts[-1]] = value


def is_unset_value(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, (list, tuple, dict)):
        return not value
    return False


def _same(a: Any, b: Any) -> bool:
    if isinstance(b, bool) or isinstance(a, bool):
        return a is b
    if isinstance(b, (int, float)):
        try:
            return not isinstance(a, bool) and float(str(a).replace(",", "").strip()) == float(b)
        except (TypeError, ValueError):
            return False
    if isinstance(b, str):
        return isinstance(a, str) and a.strip().lower() == b.lower()
    if isinstance(b, list):
        return isinstance(a, list) and [str(x).strip().lower() for x in a] == [str(x).lower() for x in b]
    return a == b


def _blank(profile: dict) -> bool:
    return all(is_unset_value(get_path(profile, p)) for p in _USER_CONTENT)


def confirm_list(profile: Any) -> list[str]:
    raw = profile.get(CONFIRM_KEY) if isinstance(profile, dict) else None
    if not isinstance(raw, list):
        return []
    out = []
    for p in raw:
        if isinstance(p, str) and p in PRESETS and p not in out and not is_unset_value(get_path(profile, p)):
            out.append(p)
    return out


def migrate(profile: Any) -> tuple[dict, bool]:
    """Upgrade a profile to schema 2. Returns (profile, changed). Never mutates the argument."""
    if not isinstance(profile, dict):
        profile = {}
    p = copy.deepcopy(profile)
    try:
        version = int(p.get(SCHEMA_KEY) or 0)
    except (TypeError, ValueError):
        version = 0
    if version >= SCHEMA:
        cleaned = confirm_list(p)
        changed = p.get(CONFIRM_KEY, []) != cleaned and (CONFIRM_KEY in p or cleaned)
        if cleaned:
            p[CONFIRM_KEY] = cleaned
        else:
            p.pop(CONFIRM_KEY, None)
        return p, bool(changed)
    blank = _blank(p)
    confirm: list[str] = []
    for path, (old, unset, _label) in PRESETS.items():
        if not _has(p, path):
            set_path(p, path, copy.deepcopy(unset))  # never written: never answered
            continue
        value = get_path(p, path)
        if is_unset_value(value) or not _same(value, old):
            continue  # already empty, or a value the user chose
        if blank or path == "years_of_experience":
            # Nothing else was ever filled in, so the preset was never chosen. 0 years has meant
            # "not answered" since M9 (an experienced person must not claim 0).
            set_path(p, path, copy.deepcopy(unset))
        else:
            confirm.append(path)  # could be a real choice: keep it, but ask the user to confirm
    p[SCHEMA_KEY] = SCHEMA
    if confirm:
        p[CONFIRM_KEY] = confirm
    else:
        p.pop(CONFIRM_KEY, None)
    return p, True


def for_apply(profile: Any) -> dict:
    """The profile the apply engine may use: migrated, without unset or unconfirmed answers."""
    p, _ = migrate(profile)
    for path in confirm_list(p):
        set_path(p, path, copy.deepcopy(PRESETS[path][1]))
    p.pop(CONFIRM_KEY, None)
    return p  # keeps _schema, so applying for_apply twice changes nothing


def unanswered(profile: Any) -> list[dict]:
    """Preset fields still waiting for the user: unset, or kept from an old profile unconfirmed."""
    p, _ = migrate(profile)
    pending = set(confirm_list(p))
    out = []
    for path, (_old, _unset, label) in PRESETS.items():
        if path == "salary_expectation.max" and "salary_expectation.min" not in pending \
                and not is_unset_value(get_path(p, "salary_expectation.min")):
            if path not in pending:
                continue  # a minimum alone is a complete salary answer
        state = "unconfirmed" if path in pending else ("unset" if is_unset_value(get_path(p, path)) else "")
        if state:
            out.append({"field": path, "label": label, "state": state, "blocks_apply": path in PREFERENCES})
    return out


# Résumé parsing may fill facts (years, languages, country) but never the user's preferences.
NOT_FROM_RESUME = ("visa_sponsorship_needed", "willing_to_relocate", "preferred_work_mode", "salary_expectation")


def typed_template(profile: Any) -> dict:
    """``profile`` with each unset preset replaced by a value of its type, for type coercion only."""
    p = copy.deepcopy(profile) if isinstance(profile, dict) else {}
    for path, (old, _unset, _label) in PRESETS.items():
        if get_path(p, path) is None:
            set_path(p, path, copy.deepcopy(old))
    return p
