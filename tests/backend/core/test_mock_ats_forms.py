"""Answer-every-question on five mock ATS styles, in real headless Chromium (M8).

Drives tests/fixtures/mock_ats with the production scan/fill scripts (core.form_guard):
autofill from the Alex Example profile, essays composed from resume facts, the resume
uploaded, then the page's own Next/Submit button. The mock ATS records what it
received, so every assertion is on what the employer would actually get.
"""
import pytest

from core import form_guard as fg
from core.apply_tools import PAGE_TEXT_JS
from core.outcome import detect_confirmation
from tests.fixtures.mock_ats import MockATS
from tests.fixtures.persona import PROFILE, QA, essay_for, write_resume_pdf

pw = pytest.importorskip("playwright.async_api")
DECLINE = "I don't wish to answer"


@pytest.fixture
def ats():
    srv = MockATS()
    yield srv
    srv.close()


@pytest.fixture
def resume(tmp_path):
    return write_resume_pdf(tmp_path / "Alex Example résumé.pdf")


@pytest.fixture
async def page():
    try:
        async with pw.async_playwright() as p:
            browser = await p.chromium.launch()
            pg = await browser.new_page()
            yield pg
            await browser.close()
    except Exception as e:  # pragma: no cover
        if "Executable doesn't exist" in str(e) or "playwright install" in str(e):
            pytest.skip("Playwright Chromium not installed")
        raise


def evaluator(page):
    async def evaluate(js_src, *args):
        if not args:
            return await page.evaluate(js_src)
        return await page.evaluate(f"([a,b,c]) => ({js_src})(a,b,c)", list(args) + [None] * (3 - len(args)))
    return evaluate


async def compose(f):
    if f.category in ("essay", "cover_letter"):
        return essay_for(f.label)
    if "pronoun" in f.label.lower():
        return "I prefer not to say"
    return None


def form_frame(page):
    for fr in page.frames:
        if "in_iframe" in fr.url:
            return fr
    return page.main_frame


async def fill_page(page, resume) -> dict:
    ev = evaluator(page)
    res = await fg.autofill(ev, PROFILE, QA, {"source": "LinkedIn"}, compose=compose)
    # Conditional questions appear after an answer: fill again until nothing new shows up.
    for _ in range(3):
        more = await fg.autofill(ev, PROFILE, QA, {"source": "LinkedIn"}, compose=compose)
        if not more["filled"]:
            break
        res["filled"] += more["filled"]
    fr = form_frame(page)
    for inp in await fr.query_selector_all("input[type=file]"):
        await inp.set_input_files(resume)
    return res


async def submit(page):
    fr = form_frame(page)
    await fr.click("#submit")
    await page.wait_for_timeout(700)


async def drive(page, ats, site, resume, **q) -> list[dict]:
    reports = []
    await page.goto(ats.job_url(site, **q))
    await page.wait_for_timeout(600)
    if site == "wd":
        await page.fill("#email", PROFILE["email"])
        await page.fill("#password", "Example-Passw0rd!")
        await page.fill("#password2", "Example-Passw0rd!")
        await page.click("button[type=submit]")
        await page.wait_for_timeout(300)
    for _ in range(8):
        text = await page.evaluate(PAGE_TEXT_JS)
        if detect_confirmation(text, page.url).confirmed:
            break
        res = await fill_page(page, resume)
        left = fg.unanswered(await fg.scan(evaluator(page)))
        reports.append({"filled": [f["label"] for f in res["filled"]], "failed": res["failed"],
                        "left": [f.label for f in left]})
        assert not left, f"unanswered on {site}: {[f.brief() for f in left]}"
        await submit(page)
    return reports


def assert_truthful(sub: dict):
    a = sub["answers"]
    for k, want in {"first_name": "Alex", "last_name": "Example", "email": "alex@example.com",
                    "name": "Alex Example", "phone": "+15550100", "linkedin": PROFILE["linkedin"],
                    "github": PROFILE["github"], "city": "Springfield", "state": "Illinois", "zip": "62701",
                    "country": "United States of America", "authorized": "Yes", "sponsorship": "No",
                    "org": "Example Labs", "years": "6", "school": "Example State University",
                    "degree": "B.S. Computer Science", "start": "Two weeks", "salary": "150000",
                    "relocate": "Yes"}.items():
        if k in a:
            assert a[k] == want, (k, a[k], want)
    for k in ("gender", "hispanic", "race", "veteran", "disability"):
        if k in a:
            assert a[k] == DECLINE, (k, a[k])
    for k in ("why", "interest", "excites", "summary", "comments", "relocate_notes"):
        if k in a:
            assert "Example Labs" in a[k], (k, a[k])
    for k, v in a.items():
        assert v not in ("", [], None), f"blank answer submitted: {k}"
    assert sub["files"]["resume"]["head"].startswith("%PDF"), sub["files"]


@pytest.mark.parametrize("site", ["gh", "lever", "ashby", "wd", "icims"])
async def test_every_question_answered_truthfully(page, ats, resume, site):
    reports = await drive(page, ats, site, resume)
    subs = ats.submissions_for(site)
    assert len(subs) == 1, reports
    assert_truthful(subs[0])
    text = await page.evaluate(PAGE_TEXT_JS)
    ev = detect_confirmation(text, page.url)
    assert ev.confirmed, text[:400]


async def test_conditional_question_appears_and_is_answered(page, ats, resume):
    await drive(page, ats, "ashby", resume)
    a = ats.submissions_for("ashby")[0]["answers"]
    assert a["relocate"] == "Yes" and "Example Labs" in a["relocate_notes"]
    assert a["location"] in ("Springfield, IL",), a["location"]


async def test_server_rejects_missing_required_upload(page, ats):
    await page.goto(ats.job_url("gh"))
    await fg.autofill(evaluator(page), PROFILE, QA, {"source": "LinkedIn"}, compose=compose)
    left = fg.unanswered(await fg.scan(evaluator(page)))
    assert [f.category for f in left] == ["resume"]  # the guard sees the missing upload too
    await page.evaluate("() => document.querySelectorAll('[required]').forEach(e => e.removeAttribute('required'))")
    await submit(page)
    assert "Please complete: Resume/CV" in await page.content()
    assert ats.submissions_for("gh") == []
