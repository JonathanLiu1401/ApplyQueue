"""End-to-end check of the answer-every-question rule on a mock ATS form.

Runs the real in-page scan/fill scripts in headless Chromium against
tests/fixtures/forms/mock_application.html (fictional persona: Alex Example).
Skips when Playwright's Chromium is not installed.
"""
import json
from pathlib import Path

import pytest

from core import form_guard as fg
from core import job_status as js
from core.outcome import decide_outcome, detect_confirmation

pw = pytest.importorskip("playwright.async_api")

FORM = Path(__file__).resolve().parents[2] / "fixtures" / "forms" / "mock_application.html"

PROFILE = {
    "name": "Alex Example",
    "email": "alex@example.com",
    "phone": "5550100",
    "phone_country_code": "+1",
    "linkedin": "https://www.linkedin.com/in/alex-example",
    "years_of_experience": "6",
    "visa_sponsorship_needed": False,
    "cover_letter": "Dear Example Corp team, I build reliable data tools and would like to bring that to your platform team.",
    "eeo": {},  # no EEO answers saved -> must pick the decline option
}
QA = {"Which work arrangements would you consider?": "Remote"}
RESUME_FACTS = "Six years building data pipelines in Python at Example Labs."


async def compose(field):
    # Stand-in for the agent's own writing: essays only, truthful, from resume facts.
    # Factual questions the profile cannot answer stay unanswered (-> needs_human).
    if field.category in ("essay", "cover_letter"):
        return f"{RESUME_FACTS} This role matches that experience."
    return None


@pytest.fixture
async def page():
    try:
        async with pw.async_playwright() as p:
            browser = await p.chromium.launch()
            pg = await browser.new_page()
            yield pg
            await browser.close()
    except Exception as e:  # pragma: no cover - environment without Chromium
        if "Executable doesn't exist" in str(e) or "playwright install" in str(e):
            pytest.skip("Playwright Chromium not installed")
        raise


def evaluator(page):
    async def evaluate(js_src, *args):
        if not args:
            return await page.evaluate(js_src)
        return await page.evaluate(f"(a) => ({js_src})(...a)", list(args))
    return evaluate


async def _fill_everything(page, url):
    await page.goto(url)
    # The agent's upload action (in-memory file, nothing written to the repo).
    await page.set_input_files("#cv", {"name": "alex-example-resume.pdf", "mimeType": "application/pdf",
                                       "buffer": b"%PDF-1.4\n% Alex Example resume\n"})
    ev = evaluator(page)
    result = await fg.autofill(ev, PROFILE, QA, {"source": "LinkedIn"}, compose=compose)
    fields = await fg.scan(ev)
    return ev, result, fields


async def test_every_question_answered_including_optional(page):
    ev, result, fields = await _fill_everything(page, FORM.as_uri())
    left = fg.unanswered(fields)
    assert [f.label for f in left] == [], json.dumps(result, indent=1)
    by_name = {f.name: f for f in fields}
    # Optional questions are filled, not skipped.
    for name in ("linkedin", "cover_letter", "why_us", "extra", "yoe", "phone"):
        assert by_name[name].value.strip(), name
    assert "Example Labs" in by_name["why_us"].value
    assert by_name["cover_letter"].value.startswith("Dear Example Corp")
    assert by_name["arrangement"].value == "Remote"
    assert by_name["sponsor"].value == "No"
    assert by_name["source"].value == "LinkedIn"
    # EEO with no saved answer -> decline option, still selected.
    assert by_name["gender"].value == "I decline to self-identify"
    assert by_name["veteran"].value == "I don't wish to answer"
    # The stubborn field needed a fallback strategy rather than being skipped.
    yoe = next(r for r in result["filled"] if r["label"].startswith("Years of experience"))
    assert yoe["strategy"] != "direct"
    assert await page.locator("input[name=certify]").is_checked()

    # Submit, then decide the outcome from the page itself.
    await page.click("#submit")
    text = await page.inner_text("body")
    out = decide_outcome(page=detect_confirmation(text, page.url), agent_claimed_success=True)
    assert out.status == js.SUBMITTED_UNCONFIRMED  # not "confirmed" without an email


async def test_impossible_field_becomes_needs_human_not_blank(page):
    ev, result, fields = await _fill_everything(page, FORM.as_uri() + "?clearance=1")
    left = fg.unanswered(fields)
    assert [f.label for f in left] == ["Active security clearance ID *"]
    assert any(r["label"].startswith("Active security clearance") for r in result["remaining"]) or \
        any(r["label"].startswith("Active security clearance") for r in result["failed"])
    # The guard would refuse to submit; the run records needs-human instead.
    out = decide_outcome(page=detect_confirmation(await page.inner_text("body"), page.url),
                         agent_claimed_success=False,
                         unanswered=[f.brief() for f in left])
    assert out.status == js.NEEDS_HUMAN and "security clearance" in out.reason.lower()
    assert await page.locator("#submit").count() == 1  # never submitted
