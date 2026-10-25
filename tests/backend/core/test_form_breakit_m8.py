"""M8 break-it on agent forms: required checkbox groups, date pickers, a file input that rejects the PDF's
size, a two-step form with Back, and a session that times out mid-form. Headless Chromium against
tests/fixtures/forms/multipage_m8.html (fictional candidate Alex Example)."""
from datetime import date, timedelta
from pathlib import Path

import pytest

from core import form_guard as fg
from core import job_status as js
from core.outcome import decide_outcome, detect_confirmation

pw = pytest.importorskip("playwright.async_api")

FORM = Path(__file__).resolve().parents[2] / "fixtures" / "forms" / "multipage_m8.html"
PROFILE = {"name": "Alex Example", "email": "alex@example.com", "notice_period": "2 weeks"}
QA = {"Which locations would you work from?": "Remote, Hybrid"}
PDF_SMALL = {"name": "alex-example-resume.pdf", "mimeType": "application/pdf", "buffer": b"%PDF-1.4\n% Alex\n"}
PDF_BIG = {"name": "alex-example-resume.pdf", "mimeType": "application/pdf",
           "buffer": b"%PDF-1.4\n" + b"0" * (1024 * 1024 + 10)}


async def compose(f):
    return "Six years building data pipelines in Python at Example Labs." if f.category == "essay" else None


@pytest.fixture
async def page():
    try:
        async with pw.async_playwright() as p:
            browser = await p.chromium.launch()
            pg = await browser.new_page()
            pg.on("dialog", lambda d: d.dismiss())
            yield pg
            await browser.close()
    except Exception as e:  # pragma: no cover
        if "Executable doesn't exist" in str(e) or "playwright install" in str(e):
            pytest.skip("Playwright Chromium not installed")
        raise


def ev(page):
    async def evaluate(src, *args):
        return await page.evaluate(src) if not args else await page.evaluate(f"(a) => ({src})(...a)", list(args))
    return evaluate


async def _step1(page, query=""):
    await page.goto(FORM.as_uri() + query)
    res = await fg.autofill(ev(page), PROFILE, QA, compose=compose)
    return res, {f.name: f for f in await fg.scan(ev(page))}


async def test_required_checkbox_group_and_date_pickers_are_answered(page):
    res, by = await _step1(page)
    assert fg.unanswered(list(by.values())) == [], res
    assert by["loc"].value == "Remote, Hybrid"            # both ticked, not just the first match
    want = date.today() + timedelta(days=14)
    assert by["start"].value == want.isoformat()          # "2 weeks" became a real date for <input type=date>
    assert by["avail"].value == want.strftime("%m/%d/%Y")  # and honours the MM/DD/YYYY placeholder


def test_date_answers_unit():
    today = date(2026, 10, 25)
    assert fg._as_date("Immediately", today) == today
    assert fg._as_date("2 weeks' notice", today) == date(2026, 11, 8)
    assert fg._as_date("one month", today) == date(2026, 11, 24)
    assert fg._as_date("2026-12-01", today) == date(2026, 12, 1)
    assert fg._as_date("December 1, 2026", today) == date(2026, 12, 1)
    assert fg._as_date("after my visa transfer", today) is None
    f = fg.Field(key="k", kind="text", label="Start date *", type="date")
    assert fg.plan_answer(f, {"notice_period": "when my project ends"}, {}) is None  # never types prose into a date
    f2 = fg.Field(key="k", kind="text", label="Start date", placeholder="DD.MM.YYYY")
    assert fg.plan_answer(f2, {"notice_period": "2026-12-01"}, {}) == "01.12.2026"
    f3 = fg.Field(key="k", kind="text", label="Notice period")
    assert fg.plan_answer(f3, {"notice_period": "2 weeks"}, {}) == "2 weeks"     # plain text stays text


def test_checkbox_group_multi_answer_unit():
    opts = [{"label": x} for x in ("Remote", "Hybrid", "On-site")]
    g = fg.Field(key="g", kind="checkbox_group", label="Locations *", options=opts)
    assert fg.plan_answer(g, {}, {"Locations": "Remote and On-site"}) == ["Remote", "On-site"]
    assert fg.plan_answer(g, {}, {"Locations": "Hybrid"}) == "Hybrid"
    assert fg.plan_answer(g, {}, {"Locations": "Mars"}) is None
    lit = fg.Field(key="g", kind="checkbox_group", label="Mode", options=[{"label": "Remote / Hybrid"}, {"label": "On-site"}])
    assert fg.plan_answer(lit, {}, {"Mode": "Remote / Hybrid"}) == "Remote / Hybrid"


async def test_oversize_pdf_rejected_by_the_site_is_reported_not_submitted(page):
    await _step1(page)
    await page.click("#next")
    await page.set_input_files("#cv", PDF_BIG)
    assert "too large" in await page.inner_text("#cv-err")
    await fg.autofill(ev(page), PROFILE, QA, compose=compose)
    left = fg.unanswered(await fg.scan(ev(page)))
    assert [f.label for f in left] == ["Resume/CV *"]      # the upload did not stick -> still unanswered
    out = decide_outcome(page=detect_confirmation(await page.inner_text("body"), page.url),
                         agent_claimed_success=False, unanswered=[f.brief() for f in left])
    assert out.status == js.NEEDS_HUMAN and "Resume/CV" in out.reason
    await page.set_input_files("#cv", PDF_SMALL)          # a small enough file goes through
    assert fg.unanswered(await fg.scan(ev(page))) == []


async def test_back_navigation_keeps_answers_and_refills_what_the_site_lost(page):
    await _step1(page, "?reset=1")
    await page.click("#next")
    await page.set_input_files("#cv", PDF_SMALL)
    await fg.autofill(ev(page), PROFILE, QA, compose=compose)
    assert "Example Labs" in await page.input_value("#why")
    await page.click("#back")                             # step 1 re-rendered from state, start date lost
    by = {f.name: f for f in await fg.scan(ev(page))}
    assert by["first"].value == "Alex" and by["loc"].value == "Remote, Hybrid"
    assert [f.name for f in fg.unanswered(list(by.values()))] == ["start"]
    res = await fg.autofill(ev(page), PROFILE, QA, compose=compose)
    assert [r["label"] for r in res["filled"]] == ["Earliest start date *"]   # nothing else re-typed
    assert fg.unanswered(await fg.scan(ev(page))) == []
    await page.click("#next")                             # step 2 again: file inputs reset, so re-upload
    assert [f.label for f in fg.unanswered(await fg.scan(ev(page)))][0] == "Resume/CV *"
    await page.set_input_files("#cv", PDF_SMALL)
    await fg.autofill(ev(page), PROFILE, QA, compose=compose)
    await page.click("#submit")
    assert detect_confirmation(await page.inner_text("body"), page.url).confirmed


async def test_session_timeout_mid_form_is_not_a_submission(page):
    await _step1(page, "?timeout=1")
    await page.click("#next")
    text = await page.inner_text("body")
    assert "session has expired" in text
    ev_ = detect_confirmation(text, page.url)
    assert not ev_.confirmed
    out = decide_outcome(page=ev_, agent_claimed_success=True)
    assert out.status == js.NEEDS_HUMAN and "no confirmation" in out.reason


@pytest.mark.parametrize("url,text,confirmed", [
    # A timed-out session redirected to login, with the confirmation page as the redirect target.
    ("https://example.wd5.myworkdayjobs.com/login?redirect=/application/confirmation", "Sign in", False),
    ("https://jobs.example.com/auth?next=%2Fapply%2Fthank-you&returnTo=/success", "Sign in to continue", False),
    ("https://jobs.example.com/apply/thank-you", "", True),
    ("https://jobs.example.com/apply#/confirmation", "", True),                     # SPA hash route
    ("https://jobs.example.com/apply?submitted=1", "", True),
    ("https://jobs.example.com/apply", "Your session timed out. Thank you for applying!", False),
    ("https://jobs.example.com/apply", "Please log in again to finish. Application submitted", False),
])
def test_session_expiry_and_login_redirects_are_not_confirmations(url, text, confirmed):
    assert detect_confirmation(text, url).confirmed is confirmed
