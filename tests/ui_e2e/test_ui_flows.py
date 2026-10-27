"""UI end to end (M8): the production frontend build driving a real backend sidecar.

Run against the packaged PyInstaller sidecar by setting APPLYQUEUE_SIDECAR (CI does,
on Linux, Windows, Intel Mac and Apple-silicon Mac); locally it falls back to
`python backend/main.py`. See harness.py for what is and is not real.
"""
from __future__ import annotations

import os
import re
import shutil
import sys
import urllib.request
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests" / "fixtures"))

pw = pytest.importorskip("playwright.sync_api")

from tests.fixtures.persona import PROFILE, write_resume_pdf  # noqa: E402
from tests.ui_e2e.harness import DIST, PORT, FakeOpenAI, Stack  # noqa: E402
from mock_github import MockGitHub  # noqa: E402
from mock_mail_server import MockMail  # noqa: E402

pytestmark = pytest.mark.skipif(not (DIST / "index.html").exists(), reason="needs `npm run build` (dist/)")

RTL_NAME = "Alex Example \u05d0\u05dc\u05db\u05e1 \U0001F680"   # Hebrew + emoji in a real name field


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    base = tmp_path_factory.mktemp("ui") / "Alex Example ü dätä dir"
    base.mkdir()
    resume = write_resume_pdf(base / "Alex Example résumé.pdf")
    mail, gh = MockMail(), MockGitHub()
    llm = FakeOpenAI({"name": PROFILE["name"], "email": PROFILE["email"], "phone": PROFILE["phone"],
                      "current_role": PROFILE["current_role"], "address": PROFILE["address"]},
                     must_contain="Example Labs")
    env = {"APPLYQUEUE_E2E_MOCK_MAIL": mail.base, "APPLYQUEUE_E2E_MOCK_GITHUB": gh.url,
           # the xAI provider's /v1/models and chat calls go to the loopback fake, never api.x.ai
           "APPLYQUEUE_E2E_MOCK_XAI": llm.base.removesuffix("/v1")}
    # CI sets APPLYQUEUE_E2E_ARTIFACTS: a Playwright trace + the sidecar log land there and are
    # uploaded when the step fails (only fake test data: the persona, a fake key, the test token).
    artifacts = os.environ.get("APPLYQUEUE_E2E_ARTIFACTS")
    with Stack(base / "data", pick=resume, env=env) as st, pw.sync_playwright() as p:
        browser = p.chromium.launch()
        context = browser.new_context(viewport={"width": 1440, "height": 1000})
        if artifacts:
            context.tracing.start(screenshots=True, snapshots=True, sources=False)
        page = context.new_page()
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.add_init_script(st.init_script())
        try:
            yield {"st": st, "page": page, "resume": resume, "mail": mail, "gh": gh, "llm": llm,
                   "errors": errors, "tmp": base}
        finally:
            if artifacts:
                out = Path(artifacts)
                out.mkdir(parents=True, exist_ok=True)
                try:
                    context.tracing.stop(path=str(out / "ui-flows-trace.zip"))
                except Exception as e:  # never mask the real test failure
                    print(f"trace not saved: {e}")
            browser.close()
    if artifacts and st.log.exists():
        shutil.copyfile(st.log, Path(artifacts) / "ui-flows-sidecar.log")
    mail.close()
    gh.close()
    llm.close()


def goto(page, st, route: str):
    page.goto(st.url + "#" + route)
    page.wait_for_load_state("networkidle")


def _until(cond, timeout=15.0):
    import time
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return
        time.sleep(0.25)
    assert cond()


def test_deep_setup_wizard(world):
    """Welcome → AI provider (typed) → resume + AI auto-fill → profile → finish."""
    st, page = world["st"], world["page"]
    page.goto(st.url)
    page.get_by_text("Welcome to ApplyQueue!").wait_for(timeout=30000)
    page.get_by_role("button", name="Let's Go").click()
    page.get_by_test_id("ai-mode-cloud").click()
    page.get_by_text("OpenAI-Compatible", exact=True).click()
    page.get_by_placeholder("https://api.together.xyz/v1").fill(world["llm"].base)
    page.get_by_placeholder("sk-...").fill("test-not-a-key")
    page.get_by_placeholder("meta-llama/Llama-3-70b-chat-hf").fill("fake-model")
    page.wait_for_timeout(1500)  # autosave debounce
    llm = st.api("GET", "/settings/llm")
    assert llm["provider"] == "openai_compatible"
    assert llm["openai_compatible"]["base_url"] == world["llm"].base
    page.get_by_role("button", name="Next").click()
    # Resume step: Browse goes through the dialog plugin, then AI auto-fill reads the PDF
    page.get_by_role("button", name=re.compile("Browse")).first.click()
    page.wait_for_timeout(1500)
    assert st.api("GET", "/settings")["resume_path"] == world["resume"]
    page.get_by_role("button", name=re.compile("Auto-Fill|Auto-fill")).click()
    page.get_by_text(re.compile("Next", re.I)).last.wait_for()
    page.wait_for_function("() => !document.body.innerText.includes('Parsing')", timeout=60000)
    # a cold PyInstaller sidecar on a slow runner (Intel Mac) can still be parsing here
    try:
        _until(lambda: st.api("GET", "/profile")["name"] == PROFILE["name"], timeout=120)
    except AssertionError:
        raise AssertionError("auto-fill never saved the profile; sidecar log tail:\n"
                             + st.log.read_text(errors="replace")[-4000:]
                             + "\n--- page ---\n" + page.inner_text("body")[:2000])
    prof = st.api("GET", "/profile")
    assert prof["email"] == PROFILE["email"], prof
    assert world["llm"].prompts and "Example Labs" in world["llm"].prompts[-1]
    page.get_by_role("button", name="Next").click()
    page.get_by_role("button", name="Next").click()
    page.get_by_role("button", name=re.compile("Finish Setup")).click()
    _until(lambda: st.api("GET", "/setup/status")["onboarding_completed"] is True)
    page.reload()
    page.wait_for_load_state("networkidle")
    assert not page.get_by_text("Welcome to ApplyQueue!").is_visible()


def test_profile_unicode_rtl_emoji_round_trip(world):
    st, page = world["st"], world["page"]
    goto(page, st, "/profile")
    name = page.get_by_label(re.compile("Full Name", re.I))
    name.fill(RTL_NAME)
    page.get_by_role("button", name=re.compile("^Save")).first.click()
    page.wait_for_timeout(800)
    assert st.api("GET", "/profile")["name"] == RTL_NAME
    page.reload()
    page.wait_for_load_state("networkidle")
    assert page.get_by_label(re.compile("Full Name", re.I)).input_value() == RTL_NAME
    huge = "x" * 20000
    page.get_by_label(re.compile("Current Role", re.I)).fill(huge)
    page.get_by_role("button", name=re.compile("^Save")).first.click()
    page.wait_for_timeout(800)
    assert len(st.api("GET", "/profile")["current_role"]) == 20000
    st.api("PUT", "/profile", {**st.api("GET", "/profile"), "name": PROFILE["name"], "current_role": "Data Engineer"})


def test_unanswered_preferences_are_flagged_not_preset(world):
    """Sponsorship, relocation, work mode and salary start as "not answered": nothing is preselected,
    the page and the dashboard flag them, and choosing an explicit "No" saves false (not a default)."""
    st, page = world["st"], world["page"]
    prof = st.api("GET", "/profile")
    for k in ("visa_sponsorship_needed", "willing_to_relocate"):
        assert prof[k] is None, prof
    assert prof["salary_expectation"]["min"] is None and prof["preferred_work_mode"] == ""
    blocking = [u["field"] for u in st.api("GET", "/setup/status")["profile_unanswered"] if u["blocks_apply"]]
    assert "visa_sponsorship_needed" in blocking

    goto(page, st, "/")
    page.get_by_test_id("setup-profile-unanswered").wait_for(timeout=15000)
    goto(page, st, "/profile")
    banner = page.get_by_test_id("profile-unanswered")
    banner.wait_for(timeout=15000)
    visa = page.get_by_label("Visa Sponsorship Needed", exact=True)
    assert visa.input_value() == ""
    assert page.get_by_label("Willing to Relocate", exact=True).input_value() == ""
    assert page.get_by_label("Work Mode", exact=True).input_value() == ""
    visa.select_option("no")
    page.get_by_role("button", name=re.compile("^Save")).first.click()
    _until(lambda: st.api("GET", "/profile")["visa_sponsorship_needed"] is False)
    after = st.api("GET", "/profile")
    assert after["willing_to_relocate"] is None and after["salary_expectation"]["min"] is None
    blocking = [u["field"] for u in st.api("GET", "/setup/status")["profile_unanswered"] if u["blocks_apply"]]
    assert "visa_sponsorship_needed" not in blocking and "willing_to_relocate" in blocking
    page.reload()
    page.wait_for_load_state("networkidle")
    assert page.get_by_label("Visa Sponsorship Needed", exact=True).input_value() == "no"


def test_quick_start_schedule_and_mode(world):
    """3-step quick start (CV, titles + locations, Start), then a schedule and review→auto."""
    st, page = world["st"], world["page"]
    goto(page, st, "/searches")
    qs = page.get_by_test_id("quick-start")
    qs.get_by_role("button", name="Browse").click()
    page.wait_for_timeout(500)
    assert page.get_by_label("CV path").input_value() == world["resume"]
    page.get_by_label("Job titles").fill("Data Engineer, Analytics Engineer")
    page.get_by_label("Locations").fill("Remote, Denver, CO; Zürich")
    page.get_by_test_id("quick-start-go").click()
    page.wait_for_timeout(2000)
    searches = st.api("GET", "/searches")["searches"]
    assert len(searches) == 1, searches
    s = searches[0]
    assert s["titles"] == ["Data Engineer", "Analytics Engineer"]
    assert s["mode"] == "review"
    sid = s["id"]
    card = page.get_by_test_id(f"search-{sid}")
    card.wait_for()
    page.get_by_label(f"Schedule for {s['name']}").select_option("daily")
    page.wait_for_timeout(800)
    s = next(x for x in st.api("GET", "/searches")["searches"] if x["id"] == sid)
    assert s["schedule"]["kind"] == "daily" and s["schedule"]["time"] == "09:00" and s["schedule"].get("tz")
    assert s["schedule_state"]["next_run_at"]
    page.get_by_label(f"Schedule for {s['name']}").select_option("custom")
    page.get_by_label(f"Cron for {s['name']}").fill("not a cron")
    page.get_by_test_id(f"schedule-{sid}").get_by_role("button", name="Save").click()
    page.wait_for_timeout(800)
    page.get_by_text(re.compile("needs 5 fields")).first.wait_for(timeout=10000)
    s = next(x for x in st.api("GET", "/searches")["searches"] if x["id"] == sid)
    assert s["schedule"].get("cron") != "not a cron"
    page.get_by_label(f"Mode for {s['name']}").select_option("auto")
    page.wait_for_timeout(800)
    assert next(x for x in st.api("GET", "/searches")["searches"] if x["id"] == sid)["mode"] == "auto"
    page.get_by_label(f"Mode for {s['name']}").select_option("review")
    page.wait_for_timeout(800)
    assert next(x for x in st.api("GET", "/searches")["searches"] if x["id"] == sid)["mode"] == "review"


def _callback(st, mail, opened_url: str) -> None:
    """Act as the user's browser: approve at the provider, follow the redirect to the sidecar."""
    state = mail.authorize(opened_url)
    redirect = parse_qs(urlparse(opened_url).query)["redirect_uri"][0]
    assert redirect.startswith(f"http://127.0.0.1:{PORT}/") or redirect.startswith("http://localhost:")
    with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/email/oauth/callback?code=code-1&state={state}",
                                timeout=20) as r:
        assert r.status == 200


def _opened(page) -> str:
    page.wait_for_function("() => (window.__AQ_OPENED || []).length > 0", timeout=15000)
    url = page.evaluate("() => window.__AQ_OPENED.pop()")
    return url


@pytest.mark.parametrize("provider", ["outlook", "gmail"])
def test_mail_link_and_reauth(world, provider):
    st, page, mail = world["st"], world["page"], world["mail"]
    goto(page, st, "/settings")
    card = page.get_by_test_id(f"mail-{provider}")
    card.scroll_into_view_if_needed()
    name = "Outlook" if provider == "outlook" else "Gmail"
    card.get_by_label(re.compile(f"{name}.*client ID", re.I)).fill(f"client-{provider}.apps.example.com")
    if provider == "gmail":
        card.get_by_label(re.compile("client secret", re.I)).fill("test-not-a-secret")
    card.get_by_role("button", name="Save client").click()
    card.get_by_role("button", name="Connect").click()
    url = _opened(page)
    assert url.startswith(mail.base)
    _callback(st, mail, url)
    card.get_by_role("button", name=re.compile("finished signing in")).click()
    card.get_by_text("Connected", exact=True).wait_for(timeout=15000)
    # The provider revokes the sign-in: the next mail check flags it and offers Reconnect
    mail.revoked.update(mail.refresh_tokens.keys())
    mail.access.clear()
    assert "reconnect needed" in " ".join(st.api("POST", "/email/sweep", {})["errors"])
    page.reload()
    page.wait_for_load_state("networkidle")
    card = page.get_by_test_id(f"mail-{provider}")
    card.get_by_text("Reconnect needed").wait_for(timeout=15000)
    card.get_by_role("button", name="Reconnect").click()
    _callback(st, mail, _opened(page))
    page.reload()
    page.wait_for_load_state("networkidle")
    page.get_by_test_id(f"mail-{provider}").get_by_text("Connected", exact=True).wait_for(timeout=15000)


def test_sync_sign_in_private_repo_and_public_refused(world):
    st, page, gh = world["st"], world["page"], world["gh"]
    goto(page, st, "/settings")
    sec = page.get_by_test_id("sync-section")
    sec.scroll_into_view_if_needed()
    sec.get_by_placeholder("Ov23li…").fill("Iv1.example")
    sec.get_by_role("button", name="Sign in with GitHub").click()
    page.get_by_test_id("sync-user-code").wait_for(timeout=15000)
    assert "ABCD-1234" in page.get_by_test_id("sync-user-code").inner_text()
    sec.get_by_text("I understand this repo must stay private.").wait_for(timeout=30000)
    sec.get_by_text("I understand this repo must stay private.").click()
    sec.get_by_role("button", name="Create / connect private repo").click()
    page.wait_for_timeout(3000)
    repos = [r for r in gh.repos.values()]
    assert repos and all(r["private"] for r in repos), repos
    full = repos[0]["full_name"]
    # Point the git transport at a local bare repo (the mock speaks the REST API, not git).
    import subprocess
    bare = st.data_dir.parent / "state remote.git"
    subprocess.run(["git", "init", "--bare", "-q", "-b", "main", str(bare)], check=True)
    st.api("PUT", "/sync/config", {"remote_url": str(bare)})
    sec.get_by_role("button", name=re.compile("Sync now")).click()
    page.get_by_text("Synced.").wait_for(timeout=30000)
    heads = subprocess.run(["git", "--git-dir", str(bare), "log", "--oneline", "main"],
                           capture_output=True, text=True).stdout
    assert heads.strip(), "first sync pushed nothing"
    # Someone flips the repo public: the next sync must refuse to push and say so.
    gh.set_visibility(full, "public")
    st.api("POST", "/searches", {"name": "after flip", "titles": ["Data Engineer"]})
    sec.get_by_role("button", name=re.compile("Sync now")).click()
    page.get_by_text(re.compile("Sync paused")).wait_for(timeout=30000)
    after = subprocess.run(["git", "--git-dir", str(bare), "log", "--oneline", "main"],
                           capture_output=True, text=True).stdout
    assert after == heads, "pushed to a public repo"
    gh.set_visibility(full, "private")


def _fake_claude(tmp: Path) -> Path:
    """A stand-in `claude` binary (tests/fixtures/fake_clis) so the opt-in terms gate can be shown."""
    fake = ROOT / "tests" / "fixtures" / "fake_clis" / "fake_cli.py"
    if sys.platform.startswith("win"):
        shim = tmp / "claude.cmd"
        shim.write_text(f'@echo off\r\n"{sys.executable}" "{fake}" --as claude --mode stream %*\r\n')
    else:
        shim = tmp / "claude"
        shim.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{fake}" --as claude --mode stream "$@"\n')
        shim.chmod(0o755)
    return shim


def test_grok_provider_and_ai_subscription_section(world):
    """xAI/Grok is the recommended provider (key, live model list, test connection through the
    sidecar to a loopback xAI) and Settings shows "Use your AI subscription" with the Claude gate."""
    st, page, llm = world["st"], world["page"], world["llm"]
    before = st.api("GET", "/settings/llm")
    goto(page, st, "/llm")
    grok = page.get_by_text("xAI Grok (Recommended)", exact=True)
    grok.wait_for(timeout=30000)
    assert page.get_by_role("radio").first.get_attribute("value") == "xai"
    grok.click()
    page.get_by_role("textbox", name="xAI API key").fill("xai-test-not-a-key")
    # the model list comes from GET /v1/models on the (mock) xAI endpoint, via the sidecar
    page.locator("#xai-model option[value='fake-model']").wait_for(state="attached", timeout=30000)
    page.get_by_test_id("xai-models-source").get_by_text(re.compile("Grok models available")).wait_for()
    page.select_option("#xai-model", "fake-model")
    _until(lambda: (st.api("GET", "/settings/llm").get("xai") or {}).get("model") == "fake-model")
    saved = st.api("GET", "/settings/llm")
    assert saved["provider"] == "xai" and saved["xai"]["api_key"] == "xai-test-not-a-key"
    assert st.api("GET", "/setup/status")["llm"] is True
    page.get_by_role("button", name="Test Connection").click()
    page.get_by_text(re.compile("Model responded")).wait_for(timeout=60000)
    assert "hello" in llm.prompts[-1]
    # the subscription section sits under the provider form and on Settings
    page.get_by_test_id("ai-subscription-section").wait_for()
    goto(page, st, "/settings")
    sec = page.get_by_test_id("ai-subscription-section")
    sec.wait_for(timeout=30000)
    page.get_by_text("Use your AI subscription", exact=True).wait_for()
    for cli in ("grok", "cursor", "codex", "claude"):
        sec.get_by_test_id(f"backend-{cli}").wait_for(timeout=30000)
    assert sec.get_by_role("radio", name="API key provider").is_checked()
    assert sec.get_by_role("button", name=re.compile(r"Copy: (curl|irm|npm) ")).count() >= 4
    # Claude Code is opt-in behind a terms warning (a fake `claude` stands in for the real CLI)
    shim = _fake_claude(world["tmp"])
    st.api("PUT", "/agents/backends/claude", {"path": str(shim)})
    try:
        page.reload()
        sec.get_by_test_id("installed-claude").get_by_text(re.compile("Installed")).wait_for(timeout=30000)
        sec.get_by_role("radio", name="Claude Code").click()
        dialog = page.get_by_role("dialog", name="Claude Code terms")
        dialog.get_by_text("Before you turn on Claude Code").wait_for()
        assert st.api("GET", "/agents/backends")["selected"] == "browser_use"
        dialog.get_by_role("button", name="Cancel").click()
        assert st.api("GET", "/agents/backends")["selected"] == "browser_use"
    finally:
        st.api("PUT", "/agents/backends/claude", {"path": ""})
        st.api("PUT", "/settings/llm", before)


def test_no_uncaught_page_errors(world):
    assert world["errors"] == []
