"""Mailbox features used by the apply agents and the background sweep.

* get_otp: read a verification code through the Gmail/Graph API (no webmail browsing).
* sweep: confirmation email -> jobs_store.confirm_by_email (the /jobs/confirm-email logic), then label/category
  and archive it; verification/security-code emails for an application get their own label and are archived.
  Only emails matched to a job ApplyQueue applied to are touched. Email bodies are never stored.
"""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

try:
    from core import job_status as js
    from core import jobs_store
    from core.mail import classify, oauth, providers
except ImportError:  # pragma: no cover
    from backend.core import job_status as js
    from backend.core import jobs_store
    from backend.core.mail import classify, oauth, providers

DEFAULT_SETTINGS = {
    "confirm_label": "ApplyQueue/Applied",
    "verification_label": "ApplyQueue/Verification",
    "rejection_label": "ApplyQueue/Rejected",
    "interview_label": "ApplyQueue/Interview",
    "archive_confirmations": True,
    "archive_verification": True,
    "archive_rejections": False,
    "auto_sweep": True,
    "sweep_minutes": 10,
}
LOOKBACK_FIRST_S = 3 * 24 * 3600
VERIFY_WINDOW_S = 2 * 24 * 3600
POLL_S = 5.0
_MAX_PROCESSED = 3000
_lock = threading.Lock()

_CONFIRMABLE = {js.SUBMITTED_UNCONFIRMED, js.CONFIRMED}
_ACTIVE = {js.IN_PROGRESS, js.SUBMITTED_UNCONFIRMED, js.CONFIRMED, js.NEEDS_HUMAN, js.FAILED}


# ── settings / state ────────────────────────────────────────────────────
def _settings_all() -> dict:
    try:
        from core.config import load_settings
    except ImportError:  # pragma: no cover
        from backend.core.config import load_settings
    return load_settings() or {}


def get_settings() -> dict:
    cur = (_settings_all().get("email") or {})
    return {**DEFAULT_SETTINGS, **{k: v for k, v in cur.items() if k in DEFAULT_SETTINGS}}


def clean_settings(body: dict) -> dict:
    out = get_settings()
    for key in ("confirm_label", "verification_label", "rejection_label", "interview_label"):
        if key in body:
            val = " ".join(str(body[key] or "").split())[:100]
            out[key] = val or DEFAULT_SETTINGS[key]
    for key in ("archive_confirmations", "archive_verification", "archive_rejections", "auto_sweep"):
        if key in body:
            out[key] = bool(body[key])
    if "sweep_minutes" in body:
        out["sweep_minutes"] = max(2, min(int(body["sweep_minutes"] or 10), 240))
    return out


def _state_file() -> Path:
    return oauth._data_dir() / "mail_state.json"


def _load_state() -> dict:
    try:
        data = json.loads(_state_file().read_text("utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_state(state: dict) -> None:
    p = _state_file()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(state), "utf-8")
    os.replace(tmp, p)


def linked_providers() -> list[str]:
    return [p for p in oauth.PROVIDERS if oauth.is_linked(p)]


def has_linked_account() -> bool:
    return bool(linked_providers())


def _evidence(msg: dict) -> dict:
    return {"message_id": msg["id"], "provider": msg["provider"], "from_domain": msg.get("from_domain", ""),
            "subject": (msg.get("subject") or "")[:200],
            "received_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(msg.get("received_at") or 0))}


def _record_response(url: str, kind: str, msg: dict) -> None:
    """Record an employer response (interview beats rejection; a later interview overrides)."""
    job = jobs_store.get_job(url) or {}
    if job.get("response") == "interview" and kind == "rejection":
        return
    jobs_store.update_job(url, response=kind, response_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(
        msg.get("received_at") or time.time())), response_email=_evidence(msg))


def _label(provider: str, client, msg: dict, label: str, archive: bool) -> str | None:
    try:
        return client.label_and_archive(msg["id"], label, archive=archive)
    except (providers.MailApiError, oauth.ReauthRequired):
        return None


# ── OTP ─────────────────────────────────────────────────────────────────
def get_otp(hint: str = "", since_minutes: int = 15, wait_seconds: int = 90, job_url: str = "",
            exclude_ids: list[str] | None = None) -> dict | None:
    """Newest verification code (or verify link) from the linked mailbox(es), waiting for it to arrive."""
    since = time.time() - max(1, int(since_minutes)) * 60
    deadline = time.monotonic() + max(0, int(wait_seconds))
    skip = set(exclude_ids or [])
    settings = get_settings()
    while True:
        best = None
        for p in linked_providers():
            client = providers.client_for(p)
            try:
                msgs = client.list_recent(since, limit=25)
            except (providers.MailApiError, oauth.ReauthRequired):
                continue
            for m in msgs:
                if m["id"] in skip or classify.kind(m) != "verification":
                    continue
                if (hint or job_url) and not classify.hint_matches(m, hint, job_url):
                    continue
                code = classify.extract_code(m["subject"] + "\n" + m["text"])
                link = None if code else classify.extract_verify_link(m["text"])
                if not (code or link):
                    continue
                if best is None or m["received_at"] > best[0]["received_at"]:
                    best = (m, code, link, client)
        if best:
            m, code, link, client = best
            if settings["archive_verification"] or settings["verification_label"]:
                new_id = _label(m["provider"], client, m, settings["verification_label"],
                                settings["archive_verification"])
                if new_id:
                    _remember(m["provider"], [m["id"], new_id], "verification", job_url)
            return {"code": code, "link": link, "message_id": m["id"], "from_domain": m["from_domain"],
                    "subject": m["subject"][:200], "provider": m["provider"]}
        if time.monotonic() >= deadline:
            return None
        time.sleep(min(POLL_S, max(0.1, deadline - time.monotonic())))


def _remember(provider: str, ids: list[str], what: str, job_url: str) -> None:
    with _lock:
        st = _load_state()
        done = st.setdefault("processed", {})
        for mid in ids:
            if mid:
                done[f"{provider}:{mid}"] = {"kind": what, "job": job_url, "at": time.time()}
        if len(done) > _MAX_PROCESSED:
            for k in sorted(done, key=lambda k: done[k].get("at", 0))[: len(done) - _MAX_PROCESSED]:
                done.pop(k, None)
        _save_state(st)


# ── confirmations ───────────────────────────────────────────────────────
def find_confirmations_for(job: dict, days: int = 14) -> list[dict]:
    out = []
    for p in linked_providers():
        try:
            msgs = providers.client_for(p).list_recent(time.time() - days * 86400, limit=50)
        except (providers.MailApiError, oauth.ReauthRequired):
            continue
        for m in msgs:
            if classify.kind(m) == "confirmation" and classify.job_score(m, job):
                out.append({"message_id": m["id"], "provider": p, "subject": m["subject"][:200],
                            "from_domain": m["from_domain"], "received_at": m["received_at"]})
    return sorted(out, key=lambda h: h["received_at"], reverse=True)


def sweep(lookback_s: float | None = None) -> dict:
    """One pass over each linked inbox. Idempotent (processed ids are remembered)."""
    settings = get_settings()
    summary = {"confirmed": [], "labeled": 0, "verification_archived": 0, "rejections": [], "interviews": [],
               "errors": [], "providers": []}
    jobs = [{**j, "url": j.get("url") or u} for u, j in jobs_store.read_jobs().items() if isinstance(j, dict)]
    with _lock:
        st = _load_state()
    for p in linked_providers():
        summary["providers"].append(p)
        last = float((st.get("last_sweep") or {}).get(p) or 0)
        if lookback_s:
            since = time.time() - lookback_s
        elif last:
            since = last - 600  # overlap: providers index new mail with a small delay
        else:
            since = time.time() - LOOKBACK_FIRST_S
        client = providers.client_for(p)
        started = time.time()
        try:
            msgs = client.list_recent(since, inbox_only=True, limit=100)
        except oauth.ReauthRequired:
            summary["errors"].append(f"{oauth.NAMES[p]}: reconnect needed")
            continue
        except providers.MailApiError as e:
            summary["errors"].append(str(e))
            continue
        done = st.get("processed") or {}
        for m in sorted(msgs, key=lambda m: m["received_at"]):
            if f"{p}:{m['id']}" in done:
                continue
            k = classify.kind(m)
            if k == "confirmation":
                job = classify.match_job(m, [j for j in jobs if j.get("status") in _CONFIRMABLE
                                             or j.get("status") == js.LEGACY_APPLIED])
                if not job:
                    continue
                if job.get("status") != js.CONFIRMED:
                    ok, _code = jobs_store.confirm_by_email(job["url"], _evidence(m))
                    if not ok:  # e.g. no page evidence: leave the email for the user to review
                        continue
                    summary["confirmed"].append(job["url"])
                    job["status"] = js.CONFIRMED
                new_id = _label(p, client, m, settings["confirm_label"], settings["archive_confirmations"])
                if new_id:
                    summary["labeled"] += 1
                    _remember(p, [m["id"], new_id], "confirmation", job["url"])
            elif k in ("rejection", "interview"):
                job = classify.match_job(m, [j for j in jobs if js.is_submitted(j.get("status"))])
                if not job:
                    continue
                _record_response(job["url"], k, m)
                summary["rejections" if k == "rejection" else "interviews"].append(job["url"])
                # Interview invitations are never archived: they need a reply.
                label = settings["rejection_label" if k == "rejection" else "interview_label"]
                new_id = _label(p, client, m, label, k == "rejection" and settings["archive_rejections"])
                _remember(p, [m["id"]] + ([new_id] if new_id else []), k, job["url"])
            elif k == "verification":
                recent = [j for j in jobs if j.get("status") in _ACTIVE and
                          classify._ts(j.get("claimed_at") or j.get("submitted_at") or 0) > time.time() - VERIFY_WINDOW_S]
                job = classify.match_job(m, recent)
                if not job:
                    continue
                new_id = _label(p, client, m, settings["verification_label"], settings["archive_verification"])
                if new_id:
                    summary["verification_archived"] += 1
                    _remember(p, [m["id"], new_id], "verification", job["url"])
        with _lock:
            st = _load_state()
            st.setdefault("last_sweep", {})[p] = started
            _save_state(st)
    return summary
