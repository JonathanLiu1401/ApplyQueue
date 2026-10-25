"""Keep the candidate's personal data on the job's own application site (M8 break-it).

Two independent checks, both pure (no browser code here, so they are unit-testable):

* ``ProtectedData`` knows the personal values in the profile (email, phone, full name, street
  address, date of birth, ID numbers, profile handles and any other free-text value that is not a
  generic category) plus values learned during the run (emailed one-time codes). ``leaks(text)``
  says whether a URL, typed text or script carries one of them, after URL-decoding (up to 3x),
  Unicode normalisation and ignoring separators/case.
* ``ApplyOrigins`` decides whether a page/frame/form target belongs to the application flow:
  the job URL's host and registrable domain, known ATS platforms, the exact SSO hosts candidates
  sign in with, the employer's own domain (matched against the company name) and any domain the
  user added in Settings. Everything else is "unrelated": the agent may browse it, but it may not
  type personal data there, upload the resume there, or submit a form there.
"""
from __future__ import annotations

import ipaddress
import re
import unicodedata
from urllib.parse import unquote_plus, urlparse

# Values under these keys are categories or public facts (they also appear in job URLs and job
# titles), so matching them would refuse legitimate navigation. Not protected.
PUBLIC_KEYS = re.compile(
    r"(role|title|position|company|employer|school|universit|college|degree|major|field_of|country|state|"
    r"province|region|city|location|notice|years|experience|authori[sz]|visa|sponsor|relocat|salary|currency|"
    r"eeo|gender|race|ethnic|veteran|disab|pronoun|language|skill|availab|start|remote|onsite|hybrid|"
    r"travel|clearance|level|seniority|industr|timezone|time_zone|preferred_work)", re.I)
NAME_KEYS = re.compile(r"^(name|full_?name|legal_?name|first_?name|last_?name|surname|given_?name|"
                       r"family_?name|middle_?name|preferred_?name)$", re.I)
STREET_KEYS = re.compile(r"(street|line_?1|line_?2|address_?line|address1|address2|^address$)", re.I)
DOB_KEYS = re.compile(r"(birth|dob)", re.I)
ID_KEYS = re.compile(r"(ssn|social_?security|passport|national_?id|tax_?id|driver|licen[cs]e_?number|"
                     r"sin_?number|nin\b|aadhaar|personnummer)", re.I)
PHONE_KEYS = re.compile(r"(phone|mobile|cell|tel)", re.I)
ZIP_KEYS = re.compile(r"(zip|postal|postcode)", re.I)


def _norm(text: str) -> str:
    s = str(text or "")
    for _ in range(3):
        d = unquote_plus(s)
        if d == s:
            break
        s = d
    return unicodedata.normalize("NFKC", s).casefold()


def _squash(text: str) -> str:
    return "".join(ch for ch in text if ch.isalnum() or ch == "@")


def _walk(obj, path=""):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield from _walk(v, f"{path}.{k}" if path else str(k))
    elif isinstance(obj, (list, tuple)):
        for i, v in enumerate(obj):
            yield from _walk(v, f"{path}[{i}]")
    elif isinstance(obj, str):
        yield path, obj


class ProtectedData:
    def __init__(self, profile: dict | None, sensitive_data: dict | None = None):
        self.squashed: dict[str, str] = {}      # needle -> kind (substring of the squashed text)
        self.regexes: list[tuple[str, re.Pattern]] = []
        self.own_urls: list[str] = []          # the candidate's own LinkedIn/GitHub/site: visiting is fine
        profile = profile or {}
        for key, val in _walk(profile):
            self._add_profile_value(key, val)
        first = str(profile.get("first_name") or "").strip()
        last = str(profile.get("last_name") or "").strip()
        if first and last:
            self._add_name(f"{first} {last}")
        for v in (sensitive_data or {}).values():
            if isinstance(v, str) and v.strip():
                self.add(v, "email" if "@" in v else "secret")

    def __bool__(self) -> bool:
        return bool(self.squashed or self.regexes)

    # ── building ──
    def add(self, value: str, kind: str = "value", min_len: int = 4) -> None:
        sq = _squash(_norm(value))
        if len(sq) >= min_len:
            self.squashed.setdefault(sq[:48], kind)

    def _add_name(self, value: str) -> None:
        toks = [_squash(t) for t in re.split(r"\s+", _norm(value)) if _squash(t)]
        if len(toks) >= 2:
            for seq in (toks, toks[::-1], [toks[0], toks[-1]], [toks[-1], toks[0]]):
                if len("".join(seq)) >= 6:
                    self.squashed.setdefault("".join(seq), "name")

    def _add_phone(self, value: str) -> None:
        digits = re.sub(r"\D", "", value)
        if len(digits) >= 7:
            self.regexes.append(("phone", re.compile(r"\D{0,3}".join(digits[-7:]))))

    def _add_dob(self, value: str) -> None:
        m = re.search(r"(\d{4})\D(\d{1,2})\D(\d{1,2})", value)
        if m:
            y, mo, d = m.group(1), int(m.group(2)), int(m.group(3))
        else:
            m = re.search(r"(\d{1,2})\D(\d{1,2})\D(\d{4})", value)
            if not m:
                return
            mo, d, y = int(m.group(1)), int(m.group(2)), m.group(3)
        sep = r"[-/. ]?"
        M, D = rf"0?{mo}", rf"0?{d}"
        for order in ((y, M, D), (M, D, y), (D, M, y)):
            self.regexes.append(("date_of_birth", re.compile(r"(?<!\d)" + sep.join(order) + r"(?!\d)")))

    def _add_profile_value(self, key: str, val: str) -> None:
        val = val.strip()
        if not val:
            return None
        leaf = key.rsplit(".", 1)[-1]
        if "@" in val and "." in val and " " not in val:
            return self.add(val, "email")
        if PHONE_KEYS.search(leaf) and not leaf.endswith("country_code"):
            return self._add_phone(val)
        if NAME_KEYS.match(leaf):
            if leaf.lower() in ("name", "full_name", "fullname", "legal_name", "legalname"):
                self._add_name(val)
            return None
        if DOB_KEYS.search(leaf):
            return self._add_dob(val)
        if ID_KEYS.search(leaf):
            return self.add(val, "id_number")
        if STREET_KEYS.search(leaf):
            return self.add(val, "address", min_len=6)
        if ZIP_KEYS.search(leaf) or PUBLIC_KEYS.search(key):
            return None
        if re.match(r"https?://", val, re.I):
            self.own_urls.append(val.rstrip("/").lower())
            handle = _squash(_norm(urlparse(val).path))
            if len(handle) >= 4:
                self.squashed.setdefault(handle[:48], "profile_link")
            return None
        sq = _squash(_norm(val))
        if len(sq) > 48:
            # Long free text (cover letter, summary): any 48-character excerpt contains one of
            # these 24-character chunks.
            for i in range(0, min(len(sq), 24 * 200) - 23, 24):
                self.squashed.setdefault(sq[i:i + 24], "profile_text")
        elif len(sq) >= 8:
            self.add(val, "profile_text", min_len=8)
        return None

    # ── checking ──
    def leaks(self, text: str) -> list[str]:
        """Kinds of personal data found in ``text`` (empty list: nothing personal)."""
        if not text:
            return []
        n = _norm(text)
        sq = _squash(n)
        kinds = {k for needle, k in self.squashed.items() if needle in sq}
        kinds |= {k for k, rx in self.regexes if rx.search(n)}
        return sorted(kinds)

    def url_leaks(self, url: str) -> list[str]:
        u = (url or "").strip().rstrip("/").lower()
        for own in self.own_urls:
            if u == own or u.startswith(own + "/") or u.startswith(own + "?"):
                # Opening the candidate's own public profile is fine; a query riding on it is not.
                return self.leaks(u.split("?", 1)[1] if "?" in u else "")
        return self.leaks(url)


# ── origins ──
KNOWN_ATS = frozenset({
    "greenhouse.io", "lever.co", "ashbyhq.com", "myworkdayjobs.com", "myworkday.com", "workday.com",
    "icims.com", "smartrecruiters.com", "jobvite.com", "taleo.net", "oraclecloud.com", "successfactors.com",
    "successfactors.eu", "sapsf.com", "sapsf.eu", "bamboohr.com", "workable.com", "breezy.hr", "recruitee.com",
    "jazzhr.com", "applytojob.com", "rippling.com", "rippling-ats.com", "paylocity.com", "ultipro.com",
    "ukg.com", "adp.com", "dayforcehcm.com", "teamtailor.com", "personio.de", "personio.com", "pinpointhq.com",
    "eightfold.ai", "phenompeople.com", "avature.net", "csod.com", "brassring.com", "trakstar.com",
    "comeet.co", "comeet.com", "freshteam.com", "zohorecruit.com", "jobscore.com",
    "linkedin.com", "indeed.com", "glassdoor.com", "wellfound.com", "workatastartup.com", "ycombinator.com",
    "dice.com", "ziprecruiter.com", "filestackapi.com", "filestackcontent.com",
})
# Sign-in providers: exact hosts only (sites.google.com or a form on docs.google.com is not sign-in).
SSO_HOSTS = frozenset({
    "accounts.google.com", "login.microsoftonline.com", "login.live.com", "login.microsoft.com",
    "appleid.apple.com", "github.com",
})
_SLD = {"co", "com", "org", "net", "ac", "gov", "edu", "ne", "or", "go", "gv", "nic"}
_CO_SUFFIX = re.compile(r"\b(inc|llc|ltd|limited|corp|corporation|co|company|group|gmbh|ag|sa|plc|bv|"
                        r"technologies|technology|holdings)\b\.?", re.I)


def host_of(url: str) -> str:
    try:
        return (urlparse(url).hostname or "").lower().rstrip(".")
    except ValueError:
        return ""


def registrable(host: str) -> str:
    host = (host or "").lower().rstrip(".")
    try:
        ipaddress.ip_address(host.strip("[]"))
        return host
    except ValueError:
        pass
    labels = [x for x in host.split(".") if x]
    if len(labels) <= 2:
        return ".".join(labels)
    if len(labels[-1]) == 2 and labels[-2] in _SLD:
        return ".".join(labels[-3:])
    return ".".join(labels[-2:])


class ApplyOrigins:
    def __init__(self, job_url: str, company: str = "", extra=()):
        self.job_host = host_of(job_url)
        self.domains = {registrable(self.job_host)} if self.job_host else set()
        for d in extra or ():
            d = str(d or "").strip().lower()
            d = host_of(d) if "://" in d else d.split("/")[0].split(":")[0]
            if d:
                self.domains.add(registrable(d.lstrip("*.")))
        base = _CO_SUFFIX.sub(" ", _norm(company or ""))
        words = [_squash(w) for w in base.split() if _squash(w)]
        self.company_slugs = {s for s in ("".join(words), words[0] if words else "") if len(s) >= 4}

    def allows(self, url: str) -> bool:
        if not self.job_host:  # no job URL to anchor on (direct tool use): nothing to compare against
            return bool(url) and bool(re.match(r"https?://", url, re.I))
        if not url or not re.match(r"https?://", url, re.I):
            return False
        host = host_of(url)
        if not host:
            return False
        if host == self.job_host or host in SSO_HOSTS:
            return True
        reg = registrable(host)
        if reg in self.domains or reg in KNOWN_ATS:
            return True
        label = _squash(reg.split(".")[0])
        return len(label) >= 4 and any(label == s or s.startswith(label) for s in self.company_slugs)

    def describe(self) -> str:
        return ", ".join(sorted(self.domains)) + " (plus known ATS and sign-in sites)"


# Scripts the agent runs must not send data anywhere or submit forms behind the guards' back.
NETWORK_JS = re.compile(
    r"\bfetch\s*\(|XMLHttpRequest|sendBeacon|\bWebSocket\b|\bEventSource\b|\bimport\s*\(|window\.open\s*\(|"
    r"location(\.href)?\s*=(?!=)|location\.(assign|replace)\s*\(|\.src\s*=(?!=)|\.action\s*=(?!=)|"
    r"postMessage\s*\(|\.submit\s*\(|requestSubmit|\.formAction\s*=(?!=)|document\.cookie\s*=(?!=)")
