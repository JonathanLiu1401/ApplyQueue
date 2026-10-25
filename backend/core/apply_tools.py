"""Custom browser-use actions that enforce ApplyQueue's apply policy.

- scan_form / autofill_known_fields / verify_form_complete / submit_form_guarded:
  answer-every-question with retry strategies (core.form_guard).
- report_needs_human: the ledger entry used instead of leaving a field blank.
- report_confirmation: reads the page ITSELF (never the agent's claim).
- fill_site_password: types a per-site vault password without the LLM seeing it.
- get_email_code: reads a verification code from the linked mailbox API (M4), no webmail browsing.
"""

import asyncio
import json
from dataclasses import dataclass, field
from typing import Literal

from browser_use import BrowserSession, Tools
from browser_use.agent.views import ActionResult
from pydantic import BaseModel, Field as PField

try:
    from core import form_guard as fg
    from core.credential_vault import CredentialVault, normalize_host
    from core.outcome import PageEvidence, detect_confirmation
except ImportError:  # pragma: no cover
    from backend.core import form_guard as fg
    from backend.core.credential_vault import CredentialVault, normalize_host
    from backend.core.outcome import PageEvidence, detect_confirmation

PAGE_TEXT_JS = "() => (document.body ? document.body.innerText : '').slice(0, 20000)"


@dataclass
class RunLedger:
    """Everything our own code observed during one application run."""
    needs_human: list[dict] = field(default_factory=list)
    confirmations: list[dict] = field(default_factory=list)
    unanswered_last: list[dict] = field(default_factory=list)
    blocked_submits: int = 0
    passwords: list[dict] = field(default_factory=list)  # {host, mode, created} - never the password
    otp_message_ids: list[str] = field(default_factory=list)

    def add_needs_human(self, label: str, reason: str) -> None:
        if not any(e["field_label"] == label for e in self.needs_human):
            self.needs_human.append({"field_label": label[:200], "reason": reason[:500]})

    def best_confirmation(self) -> PageEvidence | None:
        for c in reversed(self.confirmations):
            if c.get("confirmed"):
                return PageEvidence(True, c.get("matched", ""), c.get("url", ""), c.get("source", ""))
        return None

    def to_dict(self) -> dict:
        return {"needs_human": self.needs_human, "confirmations": self.confirmations,
                "unanswered_last": self.unanswered_last, "blocked_submits": self.blocked_submits,
                "passwords": self.passwords}


def session_evaluator(browser_session: BrowserSession):
    async def evaluate(js: str, *args):
        page = await browser_session.must_get_current_page()
        return await page.evaluate(js, *args)
    return evaluate


async def read_page_evidence(browser_session: BrowserSession) -> PageEvidence:
    evaluate = session_evaluator(browser_session)
    text = await evaluate(PAGE_TEXT_JS)
    url = await browser_session.get_current_page_url()
    return detect_confirmation(text or "", url or "")


def _remaining_after_ledger(fields: list[fg.Field], ledger: RunLedger) -> list[fg.Field]:
    reported = {e["field_label"].strip().lower() for e in ledger.needs_human}
    return [f for f in fg.unanswered(fields) if f.label.strip().lower() not in reported]


def _fmt(fields: list[fg.Field]) -> str:
    return json.dumps([f.brief() for f in fields], ensure_ascii=False)


class NeedsHumanParams(BaseModel):
    field_label: str = PField(description="The question/field label exactly as shown")
    reason: str = PField(description="Why it is impossible after trying every retry strategy")


class IndexParams(BaseModel):
    index: int = PField(description="Element index of the button to click")


class PasswordParams(BaseModel):
    index: int = PField(description="Element index of the password input")
    mode: Literal["new_account", "sign_in"] = "sign_in"


def build_apply_tools(*, ledger: RunLedger, profile: dict, qa: dict, context: dict | None = None,
                      vault: CredentialVault | None = None, account_email: str = "",
                      sensitive_data: dict | None = None) -> Tools:
    tools = Tools()
    context = context or {}

    @tools.action("Scan the current page's application form and list every question that is still unanswered, "
                  "INCLUDING optional ones, with its category (eeo/essay/cover_letter/resume/generic).")
    async def scan_form(browser_session: BrowserSession) -> ActionResult:
        fields = await fg.scan(session_evaluator(browser_session))
        remaining = fg.unanswered(fields)
        msg = (f"{len(fields)} fields, {len(remaining)} unanswered: {_fmt(remaining)}" if fields
               else "No form fields found in the main page (if a form is visible it may be in an iframe).")
        return ActionResult(extracted_content=msg, long_term_memory=f"scan_form: {len(remaining)} unanswered")

    @tools.action("Fill every unanswered field that the candidate profile or saved answers cover (EEO gets the "
                  "decline option when the profile has no answer). Retries direct-set, dropdown search and typing. "
                  "Returns the fields you must still answer yourself (e.g. essays from the resume).")
    async def autofill_known_fields(browser_session: BrowserSession) -> ActionResult:
        res = await fg.autofill(session_evaluator(browser_session), profile, qa, context)
        summary = {"filled": [f["label"] for f in res["filled"]],
                   "failed_all_strategies": [f["label"] for f in res["failed"]],
                   "answer_yourself": res["remaining"]}
        return ActionResult(extracted_content=json.dumps(summary, ensure_ascii=False)[:6000],
                            long_term_memory=f"autofill: {len(res['filled'])} filled, "
                                             f"{len(res['failed']) + len(res['remaining'])} left")

    @tools.action("Verify that every question on the current form page is answered (including optional ones) "
                  "before clicking Next/Continue/Submit.")
    async def verify_form_complete(browser_session: BrowserSession) -> ActionResult:
        remaining = _remaining_after_ledger(await fg.scan(session_evaluator(browser_session)), ledger)
        ledger.unanswered_last = [f.brief() for f in remaining]
        if remaining:
            return ActionResult(error="NOT COMPLETE - answer these first (use the retry strategies; report_needs_human "
                                      f"only if truly impossible): {_fmt(remaining)}")
        return ActionResult(extracted_content="Form page complete: every question is answered.")

    @tools.action("Click a Next/Continue/Review/Submit button by index, but ONLY if every question on the page is "
                  "answered; otherwise returns the unanswered questions.", param_model=IndexParams)
    async def submit_form_guarded(params: IndexParams, browser_session: BrowserSession) -> ActionResult:
        remaining = _remaining_after_ledger(await fg.scan(session_evaluator(browser_session)), ledger)
        ledger.unanswered_last = [f.brief() for f in remaining]
        if remaining:
            ledger.blocked_submits += 1
            return ActionResult(error=f"Refused to click: unanswered questions {_fmt(remaining)}")
        from browser_use.browser.events import ClickElementEvent
        node = await browser_session.get_element_by_index(params.index)
        if node is None:
            return ActionResult(error=f"Element {params.index} not found - refresh the page state and retry")
        event = browser_session.event_bus.dispatch(ClickElementEvent(node=node))
        await event
        await event.event_result(raise_if_any=True, raise_if_none=False)
        await asyncio.sleep(2)
        evidence = await read_page_evidence(browser_session)
        ledger.confirmations.append(evidence.to_dict())
        note = " Confirmation detected on the page." if evidence.confirmed else ""
        return ActionResult(extracted_content=f"Clicked element {params.index}.{note}")

    @tools.action("Record a field that is truly impossible to answer after every retry strategy. The job is then "
                  "flagged for a person instead of being submitted with a blank answer.",
                  param_model=NeedsHumanParams)
    async def report_needs_human(params: NeedsHumanParams) -> ActionResult:
        ledger.add_needs_human(params.field_label, params.reason)
        return ActionResult(extracted_content=f"Recorded needs-human: {params.field_label}",
                            long_term_memory=f"needs_human: {params.field_label}")

    @tools.action("After the final submit: read the current page and record whether it shows an application "
                  "confirmation. ApplyQueue checks the page itself.")
    async def report_confirmation(browser_session: BrowserSession) -> ActionResult:
        evidence = await read_page_evidence(browser_session)
        ledger.confirmations.append(evidence.to_dict())
        if evidence.confirmed:
            return ActionResult(extracted_content=f"Confirmation found on the page: '{evidence.matched}'")
        return ActionResult(extracted_content="No confirmation text found on the current page.")

    @tools.action("Type this site's stored password into a password input. mode='new_account' generates and "
                  "saves a unique password for this site (use it for password AND confirm-password boxes); "
                  "mode='sign_in' uses the saved one. You never see the password.", param_model=PasswordParams)
    async def fill_site_password(params: PasswordParams, browser_session: BrowserSession) -> ActionResult:
        if vault is None:
            return ActionResult(error="Credential vault unavailable")
        try:
            host = normalize_host(await browser_session.get_current_page_url())
        except ValueError:
            return ActionResult(error="Could not determine the current site")
        created = False
        if params.mode == "new_account":
            entry, created = vault.get_or_create(host, account_email)
            password = entry["password"]
        else:
            password = vault.password_for_sign_in(host)
            if not password:
                return ActionResult(error=f"No saved password for {host}. Create an account (mode='new_account') "
                                          "or use 'Forgot password'.")
        node = await browser_session.get_element_by_index(params.index)
        if node is None:
            return ActionResult(error=f"Element {params.index} not found")
        if sensitive_data is not None:
            # Mask the value from anything that is sent back to the LLM.
            sensitive_data[f"site_password_{host}"] = password
        from browser_use.browser.events import TypeTextEvent
        event = browser_session.event_bus.dispatch(TypeTextEvent(
            node=node, text=password, clear=True, is_sensitive=True, sensitive_key_name="site_password"))
        await event
        await event.event_result(raise_if_any=True, raise_if_none=False)
        ledger.passwords.append({"host": host, "mode": params.mode, "created": created})
        return ActionResult(extracted_content=f"Typed the {'new' if created else 'saved'} password for {host} "
                                              f"into element {params.index}.")

    class EmailCodeParams(BaseModel):
        sender_hint: str = PField(default="", description="Company or site name that sent the code")
        wait_seconds: int = PField(default=90, ge=0, le=300)

    @tools.action("Get the newest verification / one-time code (or verify link) from the user's linked mailbox "
                  "through the Gmail/Outlook API. Never open webmail.", param_model=EmailCodeParams)
    async def get_email_code(params: EmailCodeParams) -> ActionResult:
        try:
            from core.mail import service as mail
        except ImportError:  # pragma: no cover
            from backend.core.mail import service as mail
        if not mail.has_linked_account():
            return ActionResult(error="No mailbox is linked in ApplyQueue. Use report_needs_human for the code.")
        ctx = context or {}
        got = await asyncio.to_thread(mail.get_otp, hint=params.sender_hint or ctx.get("company", ""),
                                      since_minutes=15, wait_seconds=params.wait_seconds,
                                      job_url=ctx.get("url", ""), exclude_ids=list(ledger.otp_message_ids))
        if not got:
            return ActionResult(error="No verification email arrived yet. Request a new code, then call again.")
        ledger.otp_message_ids.append(got["message_id"])
        if got.get("code"):
            return ActionResult(extracted_content=f"Verification code: {got['code']}")
        return ActionResult(extracted_content=f"No code in the email; open this verification link: {got['link']}")

    return tools
