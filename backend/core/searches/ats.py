"""ATS keyword check: which important job-description terms the resume is missing (no LLM)."""
from __future__ import annotations

import re
from collections import Counter

STOP = set("""
a about above across after again against all also am an and any are as at be because been before being below
between both but by can could did do does doing down during each either etc ever every few for from further get
had has have having he her here hers herself him himself his how i if in into is it its itself just least like
may me might more most must my myself no nor not now of off on once only or other our ours ourselves out over own
per please same she should so some such than that the their theirs them themselves then there these they this
those through to too under until up upon us very via was we well were what when where which while who whom why
will with within without would you your yours yourself yourselves
ability able apply applicant applicants applying candidate candidates company team teams role roles position
job jobs work working world experience experienced year years including include includes new strong excellent
good great plus preferred required requirements responsibilities qualifications skills skill knowledge using use
opportunity opportunities benefits salary equal employer employment status race color religion sex gender
national origin age disability veteran ensure across within day days help make based looking join part time
full us one two three four five etc e g ie eg will
""".split())
_TOKEN = re.compile(r"[a-z][a-z0-9+#./-]*[a-z0-9+#]|[a-z]")


def _tokens(text: str) -> list[str]:
    return [t.strip("./-") for t in _TOKEN.findall((text or "").lower())]


def keywords(text: str, n: int = 25) -> list[str]:
    toks = _tokens(text)
    uni = Counter(t for t in toks if t not in STOP and len(t) > 1 and not t.isdigit())
    bi = Counter(f"{a} {b}" for a, b in zip(toks, toks[1:])
                 if a not in STOP and b not in STOP and len(a) > 1 and len(b) > 1)
    scored: Counter = Counter()
    for k, v in uni.items():
        scored[k] = v
    for k, v in bi.items():
        if v >= 2:
            scored[k] = v * 2  # repeated phrases are strong signals ("project management")
    out: list[str] = []
    for k, _ in scored.most_common():
        if any(k in o.split() for o in out if " " in o) and " " not in k:
            continue
        out.append(k)
        if len(out) >= n:
            break
    return out


def check(resume_text: str, job_text: str, n: int = 25) -> dict:
    if not (job_text or "").strip():
        return {"score": 0, "matched": [], "missing": [], "error": "No job description to compare against"}
    hay = " " + " ".join(_tokens(resume_text)) + " "
    kws = keywords(job_text, n)
    matched = [k for k in kws if f" {k} " in hay]
    missing = [k for k in kws if k not in matched]
    return {"score": round(100 * len(matched) / len(kws)) if kws else 0, "matched": matched, "missing": missing}
