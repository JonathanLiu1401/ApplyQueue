"""Classify application emails and match them to jobs. Pure functions (no I/O)."""
from __future__ import annotations

import re
from datetime import datetime
from urllib.parse import urlparse

CONFIRM_RE = re.compile(
    r"thanks?(?: you)? for (?:applying|your application|your interest in|submitting)"
    r"|(?:we(?:'ve| have)|we) received your (?:job )?application"
    r"|application (?:received|submitted|confirmation|has been (?:received|submitted))"
    r"|your application (?:to|for|was|has been|is)"
    r"|successfully (?:applied|submitted your application)"
    r"|we got your application", re.I)
VERIFY_RE = re.compile(
    r"verification code|verify your (?:email|account|identity)|security code|one[- ]time (?:pass)?(?:code|password)"
    r"|\botp\b|sign[- ]?in code|log[- ]?in code|passcode|confirm your (?:email|account)|activate your account"
    r"|access code|authentication code|your code is|confirmation code", re.I)
REJECT_RE = re.compile(r"unfortunately|not (?:be )?moving forward|other candidates|decided not to", re.I)
NEWSLETTER_RE = re.compile(r"job alert|jobs? (?:you may|for you)|recommended jobs|newsletter|unsubscribe from (?:job )?alerts", re.I)

_KEYWORD_CODE = re.compile(
    r"(?:code|passcode|pin|otp|password)\b[^A-Za-z0-9]{0,6}(?:is|:)?[^A-Za-z0-9]{0,6}([A-Z0-9]{4,10})\b", re.I)
_SIX = re.compile(r"(?<![\d-])(\d{6})(?![\d-])")
_FOUR_EIGHT = re.compile(r"(?<![\d-])(\d{4,8})(?![\d-])")
_LINK = re.compile(r"https://[^\s<>\"')\]]+", re.I)
_VERIFY_LINK = re.compile(r"verif|confirm|activate|magic|sign[-_]?in|login", re.I)

ATS_DOMAINS = (
    "greenhouse.io", "greenhouse-mail.io", "lever.co", "hire.lever.co", "myworkday.com", "myworkdayjobs.com",
    "workday.com", "ashbyhq.com", "smartrecruiters.com", "icims.com", "workablemail.com", "workable.com",
    "jobvite.com", "bamboohr.com", "taleo.net", "successfactors.com", "breezy.hr", "recruitee.com",
    "jazzhr.com", "applytojob.com", "rippling.com", "dover.com", "teamtailor.com", "linkedin.com",
    "indeed.com", "indeedemail.com", "wellfound.com",
)
_STOP = {"inc", "llc", "ltd", "corp", "corporation", "co", "company", "the", "group", "technologies",
         "technology", "labs", "gmbh", "plc", "limited", "holdings", "careers", "jobs", "hq"}


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


def company_tokens(company: str) -> list[str]:
    words = [w for w in _norm(company).split() if w not in _STOP]
    return words


def _company_hit(company: str, hay: str, squashed: str) -> bool:
    words = company_tokens(company)
    if not words:
        return False
    phrase = " ".join(words)
    if len(phrase) < 3:
        return bool(re.search(rf"\b{re.escape(phrase)}\b", hay))
    return f" {phrase} " in f" {hay} " or phrase.replace(" ", "") in squashed


def kind(msg: dict) -> str | None:
    """'confirmation' | 'verification' | None."""
    subj, text = msg.get("subject") or "", (msg.get("text") or "")[:4000]
    if NEWSLETTER_RE.search(subj):
        return None
    if VERIFY_RE.search(subj) or (VERIFY_RE.search(text[:1500]) and extract_code(subj + "\n" + text)):
        return "verification"
    if REJECT_RE.search(subj + " " + text[:1500]):
        return None
    if CONFIRM_RE.search(subj) or CONFIRM_RE.search(text[:1500]):
        return "confirmation"
    return None


def extract_code(text: str) -> str | None:
    text = text or ""
    for m in _KEYWORD_CODE.finditer(text):
        c = m.group(1)
        if any(ch.isdigit() for ch in c) and not re.fullmatch(r"(?:19|20)\d\d", c):
            return c
    m = _SIX.search(text)
    if m:
        return m.group(1)
    for m in _FOUR_EIGHT.finditer(text):
        c = m.group(1)
        if not re.fullmatch(r"(?:19|20)\d\d", c):
            return c
    return None


def extract_verify_link(text: str) -> str | None:
    for m in _LINK.finditer(text or ""):
        url = m.group(0).rstrip(".,;")
        if _VERIFY_LINK.search(url) and "unsubscribe" not in url.lower():
            return url
    return None


def _host(url: str) -> str:
    try:
        return (urlparse(url).hostname or "").lower()
    except ValueError:
        return ""


def _root(host: str) -> str:
    parts = host.split(".")
    return ".".join(parts[-2:]) if len(parts) >= 2 else host


def _ts(v) -> float:
    if isinstance(v, (int, float)):
        return float(v)
    try:
        return datetime.fromisoformat(str(v).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def job_score(msg: dict, job: dict) -> int:
    """0 = not this job. Needs the company (or the job's own site) to appear in the email."""
    subject, text = msg.get("subject") or "", (msg.get("text") or "")[:6000]
    hay = _norm(" ".join([subject, msg.get("from_name") or "", msg.get("from_domain") or "", text]))
    squashed = hay.replace(" ", "")
    dom = msg.get("from_domain") or ""
    job_host = _host(job.get("url") or "")
    score = 0
    if job.get("company") and _company_hit(job["company"], hay, squashed):
        score += 5
    if job_host and dom and _root(dom) == _root(job_host) and not dom.endswith(ATS_DOMAINS):
        score += 5  # the employer's own domain
    if not score:
        return 0
    title = _norm(job.get("title") or "")
    if title and len(title) > 3 and title in hay:
        score += 3
    if dom.endswith(ATS_DOMAINS) and job_host.endswith(_root(dom)):
        score += 1
    return score


def match_job(msg: dict, jobs: list[dict], *, slack_s: float = 300) -> dict | None:
    """Best job for this email, or None when nothing (or more than one job equally) fits."""
    received = float(msg.get("received_at") or 0)
    scored = []
    for job in jobs:
        s = job_score(msg, job)
        if not s:
            continue
        started = _ts(job.get("submitted_at") or job.get("applied_at") or job.get("claimed_at") or 0)
        if started and received and received + slack_s < started:
            continue  # the email predates this application
        scored.append((s, started, job))
    if not scored:
        return None
    scored.sort(key=lambda t: (t[0], t[1]), reverse=True)
    if len(scored) > 1 and scored[0][0] == scored[1][0] and not scored[0][1] and not scored[1][1]:
        return None  # ambiguous
    return scored[0][2]


def hint_matches(msg: dict, hint: str, job_url: str = "") -> bool:
    hay = _norm(" ".join([msg.get("subject") or "", msg.get("from_name") or "", msg.get("from_domain") or "",
                          (msg.get("text") or "")[:3000]]))
    if hint and _company_hit(hint, hay, hay.replace(" ", "")):
        return True
    host = _host(job_url)
    dom = msg.get("from_domain") or ""
    return bool(host and dom and _root(dom) == _root(host))
