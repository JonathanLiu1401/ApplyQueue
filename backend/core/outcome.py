"""Decide a job's final status from independent evidence, never self-report.

The browser agent's ``done(success=True)`` is recorded but is not evidence. A
job is ``submitted_unconfirmed`` only when *our code* reads a submission
confirmation from the live page (text or URL) - and ``confirmed`` only once a
confirmation email is also matched (M4).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

try:
    from core import job_status as js
except ImportError:  # pragma: no cover - package-style import
    from backend.core import job_status as js

# Phrases ATSs show after a successful submission. Matched case-insensitively
# against the visible page text (whitespace-collapsed).
CONFIRMATION_TEXT_PATTERNS: tuple[str, ...] = (
    r"thank(s| you) for (applying|your application|submitting)",
    r"(your )?application (has been |was )(successfully )?(submitted|received|sent)",
    r"we('ve| have) received your application",
    r"your application (was|has been) sent",
    r"application submitted",
    r"successfully applied",
    r"you('ve| have) (successfully )?applied",
)
CONFIRMATION_URL_PATTERNS: tuple[str, ...] = (
    r"/(thank[-_]?you|thanks|confirmation|application[-_]?(submitted|complete|confirmation)|submitted|success)\b",
    r"[?&](submitted|applied|success)=(1|true)",
)
# Pages that look like a confirmation but are not one.
NEGATIVE_TEXT_PATTERNS: tuple[str, ...] = (
    r"(please )?(fix|correct) the (following )?errors?",
    r"(this|a) field is required",
    r"application (could not|couldn't|failed to) be (submitted|sent)",
    r"something went wrong",
    # A timed-out session often lands on a login page whose URL carries the confirmation path as the
    # redirect target ("/login?next=/application/confirmation"); that is not a submission.
    r"session (has |had )?(expired|timed out|ended)|your session (is no longer valid|has ended)",
    r"please (sign|log) ?in again|(sign|log) ?in again to (continue|finish|resume|complete)",
)

_text_re = [re.compile(p, re.I) for p in CONFIRMATION_TEXT_PATTERNS]
_url_re = [re.compile(p, re.I) for p in CONFIRMATION_URL_PATTERNS]
_neg_re = [re.compile(p, re.I) for p in NEGATIVE_TEXT_PATTERNS]


@dataclass
class PageEvidence:
    confirmed: bool
    matched: str = ""
    url: str = ""
    source: str = ""  # "text" | "url" | ""

    def to_dict(self) -> dict:
        return {"confirmed": self.confirmed, "matched": self.matched, "url": self.url, "source": self.source}


_REDIRECT_KEYS = re.compile(r"^(next|redirect|redirect_?ur[il]|return|return_?to|returnurl|continue|goto|target|"
                            r"callback|dest|destination|from|ref|state)$", re.I)


def _url_without_redirects(url: str) -> str:
    """The URL minus query parameters that only name where to go next (login redirects)."""
    from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
    try:
        parts = urlsplit(url)
    except ValueError:
        return url
    q = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if not _REDIRECT_KEYS.match(k)]
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(q), parts.fragment))


def detect_confirmation(page_text: str, url: str = "") -> PageEvidence:
    """Return page evidence of a submitted application, read by our own code."""
    text = re.sub(r"\s+", " ", page_text or "")
    for rx in _neg_re:
        if rx.search(text):
            return PageEvidence(False, url=url)
    for rx in _text_re:
        m = rx.search(text)
        if m:
            return PageEvidence(True, matched=m.group(0), url=url, source="text")
    for rx in _url_re:
        m = rx.search(_url_without_redirects(url or ""))
        if m:
            return PageEvidence(True, matched=m.group(0), url=url, source="url")
    return PageEvidence(False, url=url)


@dataclass
class Outcome:
    status: str
    reason: str
    evidence: dict = field(default_factory=dict)


def decide_outcome(
    *,
    page: PageEvidence | None,
    agent_claimed_success: bool,
    needs_human: list[dict] | None = None,
    unanswered: list[dict] | None = None,
    email_confirmed: bool = False,
) -> Outcome:
    """Combine independent signals into a final status.

    A page confirmation wins (the ATS accepted the form); any needs-human or
    unanswered entries are still kept in the evidence for review.
    """
    needs_human = needs_human or []
    unanswered = unanswered or []
    page_ok = bool(page and page.confirmed)
    evidence = {
        "page_confirmation": page.to_dict() if page else None,
        "agent_claimed_success": bool(agent_claimed_success),
        "needs_human": needs_human,
        "unanswered": unanswered,
    }
    if page_ok and email_confirmed:
        return Outcome(js.CONFIRMED, "Page confirmation and confirmation email", evidence)
    if page_ok:
        return Outcome(
            js.SUBMITTED_UNCONFIRMED,
            "Submission confirmed on the page; waiting for confirmation email",
            evidence,
        )
    if needs_human or unanswered:
        labels = [str(e.get("field_label") or e.get("label") or "?") for e in (needs_human + unanswered)]
        return Outcome(
            js.NEEDS_HUMAN,
            "Needs a person: could not answer " + ", ".join(labels[:5]) + ("…" if len(labels) > 5 else ""),
            evidence,
        )
    if agent_claimed_success:
        return Outcome(
            js.NEEDS_HUMAN,
            "Agent reported success but no confirmation was found on the page — verify manually before retrying",
            evidence,
        )
    return Outcome(js.FAILED, "Application was not submitted", evidence)
