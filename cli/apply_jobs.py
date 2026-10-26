
"""
Script 2: Apply to collected jobs with multiple concurrent workers.
One agent per job. Tracks status in jobs.json.

Usage:
  uv run python apply_jobs.py                          # 1 worker, easy apply
  uv run python apply_jobs.py --workers 3              # 3 concurrent workers
  uv run python apply_jobs.py --workers 2 --no-easy-apply  # non-easy apply
  uv run python apply_jobs.py --limit 10               # apply to max 10 jobs
"""
import argparse
import asyncio
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

if not getattr(sys, 'frozen', False):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


try:
    import core.shared_config as config
    from core.shared_config import (
        BASE_DIR, BROWSER_PROFILE_DIR,
        JOBS_FILE, QA_FILE, CANDIDATE_PROFILE, LOGS_DIR, RESUME_PATH, SENSITIVE_DATA, BLOCKED_DOMAINS,
        AWS_PROFILE, AWS_REGION, MODEL_ID,
        load_json, save_json, refresh_credentials, credential_refresh_loop,
        build_memory_context, extract_from_history, normalize_question,
        read_jobs, claim_job, update_job, get_memory_store,
    )
    from memory import extract_learnings_from_markers, extract_learnings_via_llm, store_learnings
    from memory.metrics import MetricsStore
    from core.agent_logger import on_step as _agent_on_step, on_done as _agent_on_done, log_run_start as _agent_log_start
    from core import job_status as js
    from core.apply_policy import apply_policy_text
    from core.llm_factory import agent_timeouts, is_llm_auth_error
    from core.credential_vault import CredentialVault
    from core.outcome import decide_outcome
except ImportError:
    import backend.core.shared_config as config
    from backend.core.shared_config import (
        BASE_DIR, BROWSER_PROFILE_DIR,
        JOBS_FILE, QA_FILE, CANDIDATE_PROFILE, LOGS_DIR, RESUME_PATH, SENSITIVE_DATA, BLOCKED_DOMAINS,
        AWS_PROFILE, AWS_REGION, MODEL_ID,
        load_json, save_json, refresh_credentials, credential_refresh_loop,
        build_memory_context, extract_from_history, normalize_question,
        read_jobs, claim_job, update_job, get_memory_store,
    )
    from backend.memory import extract_learnings_from_markers, extract_learnings_via_llm, store_learnings
    from backend.memory.metrics import MetricsStore
    from backend.core.agent_logger import on_step as _agent_on_step, on_done as _agent_on_done, log_run_start as _agent_log_start
    from backend.core import job_status as js
    from backend.core.apply_policy import apply_policy_text
    from backend.core.llm_factory import agent_timeouts, is_llm_auth_error
    from backend.core.credential_vault import CredentialVault
    from backend.core.outcome import decide_outcome

# Imported after `core` so core/privacy_env.py has disabled browser-use telemetry first.
from browser_use import Agent, BrowserSession  # noqa: E402

try:  # noqa: E402 - needs browser_use, so imported after the privacy env is applied
    from core.apply_tools import RunLedger, build_apply_tools, page_load_watchdog, read_page_evidence
except ImportError:
    from backend.core.apply_tools import RunLedger, build_apply_tools, page_load_watchdog, read_page_evidence

# Lock for thread-safe QA file writes
_qa_lock = asyncio.Lock()


_ERROR_MAP = [
    ("'NoneType' object is not subscriptable", "Browser agent encountered an unexpected page state. The page may have changed or timed out."),
    ("'NoneType' object has no attribute", "Browser agent lost track of a page element. The site may have redirected or loaded slowly."),
    ("net::ERR_", "Network error — the page failed to load. Check your internet connection."),
    ("Timeout", "Operation timed out. The page took too long to respond."),
    ("ERR_CONNECTION_REFUSED", "Could not connect to the website. It may be temporarily down."),
    ("security token", "AWS credentials expired. They will be refreshed automatically on retry."),
    ("rate limit", "API rate limit reached. Wait a moment and try again."),
    ("context was destroyed", "Browser page closed unexpectedly during the application."),
    ("Target page, context or browser has been closed", "Browser closed unexpectedly during the application."),
]


def _friendly_error(raw: str) -> str:
    """Translate raw Python/browser errors into user-readable messages."""
    for pattern, friendly in _ERROR_MAP:
        if pattern.lower() in raw.lower():
            return friendly
    if len(raw) > 200 and ("Traceback" in raw or "Error:" in raw):
        return "Application failed due to an unexpected error. Check the Logs page for details."
    return raw


async def save_job_status(url: str, status: str, error: str | None = None, **extra):
    """Persist a status. ``submitted_at`` is set only for page-evidenced submissions."""
    fields: dict = {"status": status, **extra}
    fields["error"] = _friendly_error(error) if error else None
    if status in js.SUBMITTED_STATUSES:
        fields.setdefault("submitted_at", datetime.now(timezone.utc).isoformat())
    update_job(url, **fields)


def submitted_labels(jobs: dict) -> list[str]:
    """'Title at Company' for every job already submitted (incl. legacy 'applied')."""
    return [
        f"{j.get('title','')} at {j.get('company','')}"
        for j in jobs.values() if js.is_submitted(j.get("status"))
    ]


async def save_new_qa(new_questions: dict, source_domain: str = ""):
    if not new_questions:
        return
    async with _qa_lock:
        store = get_memory_store()
        if store:
            for q, a in new_questions.items():
                store.qa_add(question=q, answer=a or "", source_domain=source_domain)
        else:
            qa = load_json(QA_FILE, {})
            existing_norms = {normalize_question(k) for k in qa}
            for q, a in new_questions.items():
                if normalize_question(q) not in existing_norms:
                    qa[q] = a
                    existing_norms.add(normalize_question(q))
            save_json(QA_FILE, qa)


def _safe_log_name(company: str, title: str) -> str:
    """One path segment on every OS: no separators, no Windows-reserved characters, bounded length."""
    import re as _re
    raw = f"apply_{company}_{title}"
    name = _re.sub(r"[^\w.-]+", "_", raw, flags=_re.UNICODE).strip("._") or "apply"
    return name[:80]


def _memory_store_or_none(worker_id=0):
    try:
        return get_memory_store()
    except Exception as e:
        print(f"    ⚠️  [W{worker_id}] Memory store unavailable (non-fatal): {e}")
        return None


def _is_linkedin(url: str) -> bool:
    from urllib.parse import urlparse
    host = (urlparse(url).hostname or "").lower()
    return host == "linkedin.com" or host.endswith(".linkedin.com")


LLM_AUTH_STATUS = "llm_auth"
LLM_AUTH_REASON = "llm_key_rejected"
LLM_AUTH_MESSAGE = ("The AI provider rejected the API key (revoked, expired, wrong or out of credit). "
                    "Stopping this run; fix the key in Settings and start again.")


async def apply_to_job(job: dict, profile: dict, qa: dict, applied_labels: list[str], easy_apply: bool, worker_id: int, resume_path_override: str | None = None) -> str:
    """Apply to a single job. Returns final status."""
    url = job["url"]
    title = job.get("title", "Unknown")
    company = job.get("company", "Unknown")
    print(f"  🚀 [W{worker_id}] Starting: {title} at {company}")

    if not config.validate_job_url(url):
        await save_job_status(url, "blocked", "Invalid or internal URL")
        print(f"  🚫 [W{worker_id}] Blocked (invalid URL): {title} at {company}")
        return "blocked"

    if any(domain in url for domain in BLOCKED_DOMAINS):
        await save_job_status(url, "blocked", "Blocked domain")
        print(f"  🚫 [W{worker_id}] Blocked: {title} at {company}")
        return "blocked"

    if not claim_job(url):
        print(f"  ⏭️  [W{worker_id}] Skipped (already claimed): {title} at {company}")
        return "skipped"

    try:
        return await _apply_claimed(job, profile, qa, applied_labels, easy_apply, worker_id,
                                    resume_path_override, url, title, company)
    except Exception as e:  # anything before the agent's own error handling: never leave it in_progress
        await save_job_status(url, js.FAILED, f"Could not start the application: {e}"[:2000])
        print(f"  ❌ [W{worker_id}] Error before start: {title} at {company} — {str(e)[:100]}")
        return "failed"


def _submit_was_clicked(url: str) -> bool:
    try:
        try:
            from core import jobs_store
        except ImportError:  # pragma: no cover
            from backend.core import jobs_store
        return int((jobs_store.get_job(url) or {}).get("submit_clicks") or 0) > 0
    except Exception:
        return True  # unknown: be safe, do not auto-retry


async def _apply_claimed(job, profile, qa, applied_labels, easy_apply, worker_id, resume_path_override,
                         url, title, company) -> str:
    # Always refresh credentials before each job to avoid mid-run expiry
    refresh_credentials()

    llm = config.get_llm()
    # Use the shared browser profile in OS data dir (same as login endpoint)
    browser = BrowserSession(user_data_dir=str(BROWSER_PROFILE_DIR), chromium_sandbox=(sys.platform != "linux"))
    # memory_store.db is optional context: locked by another process past the busy timeout, or
    # unreadable, must not stop (or, after the run, misreport) an application.
    mem_store = _memory_store_or_none(worker_id)
    domain = ""
    memories_injected_count = 0
    try:
        domain = mem_store.extract_domain(url) if mem_store else ""
        memories_before = mem_store.get_domain_memories(url, limit=50) if mem_store else None
        memories_injected_count = len(memories_before) if memories_before else 0
        memory = build_memory_context(profile, qa, applied_labels, job_url=url)
    except Exception as mem_err:
        print(f"    ⚠️  [W{worker_id}] Memory store unavailable (non-fatal): {mem_err}")
        mem_store = None
        memory = build_memory_context(profile, qa, applied_labels, job_url=None)
    run_started_at = datetime.now(timezone.utc)

    resume_path = resume_path_override or RESUME_PATH

    # Use tailored resume if available for this job
    try:
        from resume.tailor import get_tailored_resume_path
        tailored_path = get_tailored_resume_path(url)
        if tailored_path:
            resume_path = tailored_path
            print(f"  📄 [W{worker_id}] Using tailored resume: {tailored_path}")
    except ImportError:
        pass

    # Profile email is for application forms; account_email is for ATS logins.
    # No password is ever given to the agent: fill_site_password types a
    # per-site password from the local vault (core/credential_vault.py).
    agent_sensitive_data = {
        "email": profile.get("email", "").strip(),
        "account_email": SENSITIVE_DATA.get("email", "").strip(),
    }
    if not agent_sensitive_data["email"]:
        agent_sensitive_data["email"] = agent_sensitive_data["account_email"]
    if not agent_sensitive_data["account_email"]:
        # No separate login email configured: sign up with the profile email rather than an empty one.
        agent_sensitive_data["account_email"] = agent_sensitive_data["email"]
    vault = CredentialVault.default()
    legacy_pw = (SENSITIVE_DATA.get("password") or "").strip()
    if legacy_pw:  # LangHire's shared password: kept for signing in to existing accounts only
        vault.import_legacy_shared(legacy_pw, agent_sensitive_data["account_email"])
    ledger = RunLedger()
    apply_tools = build_apply_tools(
        ledger=ledger, profile=profile, qa=qa,
        context={"source": "LinkedIn" if _is_linkedin(url) else (job.get("source") or "Company website"), "company": company, "title": title, "url": url,
                 # Extra domains this job's application flow may use (personal data is only typed there).
                 "apply_domains": list(job.get("apply_domains") or []) + [d for d in os.environ.get(
                     "APPLYQUEUE_APPLY_DOMAINS", "").split(",") if d.strip()]},
        vault=vault, account_email=agent_sensitive_data["account_email"],
        sensitive_data=agent_sensitive_data,
    )

    try:
        from core.mail.service import has_linked_account as _mail_linked
        mail_linked = _mail_linked()
    except Exception:
        mail_linked = False

    on_linkedin = _is_linkedin(url)
    checks: list[str] = []
    if on_linkedin:
        checks.append(
            "Go to https://www.linkedin.com/feed/ to check if you're logged into LinkedIn.\n"
            "   - If you see the feed/home page → logged in ✓\n"
            "   - If you see a login page → WAIT for user to log in manually. Check every 15 seconds (refresh). Wait up to 5 minutes."
        )
    if mail_linked:
        otp_instructions = (
            "\n\nOTP/VERIFICATION CODES: If ANY site asks for a verification code, OTP, or 2FA token:\n"
            "1. Choose the EMAIL option if given a choice\n"
            "2. Call get_email_code (it reads the linked mailbox through its API and waits for the email)\n"
            "3. Enter the code (or open the verify link it returns) in the application tab\n"
            "4. NEVER open webmail (mail.google.com, outlook.com) in the browser"
        )
    else:
        # No mailbox linked: no up-front Gmail wait (it stalled every unattended run for 5 minutes);
        # Gmail is only opened if a site actually asks for a code.
        otp_instructions = (
            "\n\nOTP/VERIFICATION CODES: If ANY site asks for a verification code, OTP, or 2FA token:\n"
            "1. Choose the EMAIL option if given a choice\n"
            "2. Open a new tab and go to https://mail.google.com\n"
            "3. Find the most recent email with the verification/OTP code\n"
            "4. Copy the code, switch back to the application tab, and enter it\n"
            "5. This is NOT a blocker — always attempt to retrieve the code from Gmail before giving up.\n"
            "6. If Gmail shows a sign-in page, do not wait: call report_needs_human for the verification code"
        )
    login_preamble = ""
    if checks:
        login_preamble = "FIRST — LOGIN CHECK:\n" + "\n".join(
            f"{i}. {c}" for i, c in enumerate(checks, 1)) + "\n\n"
    goto = f"Go to {url} on LinkedIn." if on_linkedin else f"Go to {url} (the employer's application page)."

    if easy_apply and on_linkedin:
        apply_instructions = (
            f"{login_preamble}"
            f"THEN: {goto} Click Easy Apply and complete the application. "
            f"Use resume at {resume_path}. Answer every question from the candidate profile (rules below). "
            f"Use submit_form_guarded for the Next/Review/Submit buttons."
            f"{otp_instructions}"
        )
    else:
        landing = (
            "Click Apply and follow through to the external application page. "
            if on_linkedin else "Complete the application on this site. "
        )
        apply_instructions = (
            f"{login_preamble}"
            f"THEN: {goto} {landing}"
            f"Use resume at {resume_path}. Answer every question from the candidate profile (rules below). "
            f"Use submit_form_guarded for the Next/Review/Submit buttons.\n\n"
            f"NAVIGATING EXTERNAL SITES:\n"
            f"- The LinkedIn 'Apply' button often opens a company careers page, NOT the application form directly.\n"
            f"- You MUST explore the landing page: look for 'Apply Now', 'Submit Application', or similar buttons.\n"
            f"- Scroll down — the apply button is often below the job description.\n"
            f"- If you see a job listing page, click on the specific job title first, then look for the apply button.\n"
            f"- Some sites require you to click through 2-3 pages before reaching the actual form.\n"
            f"- If the page looks blank or is loading, wait 3-5 seconds and try scrolling.\n"
            f"- NEVER give up just because the form isn't immediately visible — always explore the page first.\n\n"
            f"EMAIL USAGE:\n"
            f"- For APPLICATION FORM fields (contact email, email address, etc.): use <secret>email</secret>\n"
            f"- For LOGGING IN or CREATING ACCOUNTS on external ATS sites: use <secret>account_email</secret> "
            f"and fill_site_password for password boxes (see ACCOUNTS below)\n"
            f"If it's a video funnel or recruitment pitch, report failure and stop. "
            f"If the external form is broken after 3 attempts, report failure and stop.\n\n"
            f"BLOCKED SITES — if redirected to any of these, immediately call done with success=false: {', '.join(BLOCKED_DOMAINS)}"
            f"{otp_instructions}"
        )

    _agent_log_start("apply", f"{title} at {company}")

    MAX_STEPS = 70
    _step_count = {"n": 0}

    def _on_step_with_limit(browser_state, agent_output, step_num):
        _agent_on_step(browser_state, agent_output, step_num)
        _step_count["n"] += 1  # the hard stop is agent.run(max_steps=MAX_STEPS); raising here only
        # produced repeated "failures" and up to 10 extra paid LLM calls

    agent = Agent(
        # browser_use's built-in judge is one more paid LLM call per run (with the whole trace);
        # ApplyQueue decides the outcome from page evidence instead.
        use_judge=False,
        task=(
            f"{apply_instructions}\n\n"
            f"{apply_policy_text()}\n"
            f"PERSISTENCE & EFFICIENCY:\n"
            f"- Do NOT repeat the same failing action more than 3 times — switch to the next retry strategy.\n"
            f"- You have a maximum of {MAX_STEPS} steps total. Budget your steps wisely.\n\n"
            f"TRACKING: Include in memory field after submission:\n"
            f'@@JOB_APPLIED: {{"title": "{title}", "company": "{company}", "location": "{job.get("location", "")}"}}\n'
            f"For each form question: @@QUESTION: {{\"question\": \"...\", \"answer\": \"...\", \"type\": \"...\"}}"
        ),
        llm=llm,
        use_vision=True,
        **agent_timeouts(llm),  # llm_call_timeout was ignored by browser_use 0.13 (75 s default)
        max_failures=10,
        loop_detection_enabled=True,
        loop_detection_window=5,
        browser_session=browser,
        sensitive_data=agent_sensitive_data,
        tools=apply_tools,
        extend_system_message=memory,
        available_file_paths=[resume_path],
        save_conversation_path=str(LOGS_DIR / _safe_log_name(company, title)),
        calculate_cost=True,
        register_new_step_callback=_on_step_with_limit,
        register_done_callback=_agent_on_done,
    )

    try:
        # A page still loading after the budget is stopped (slow-loris), see page_load_watchdog.
        try:
            load_budget = float(os.environ.get("APPLYQUEUE_PAGE_LOAD_BUDGET_S") or 35)
        except ValueError:
            load_budget = 35.0
        watchdog = asyncio.create_task(page_load_watchdog(browser, budget=load_budget, ledger=ledger))
        try:
            result = await agent.run(max_steps=MAX_STEPS)
        finally:
            watchdog.cancel()

        # Extract Q&A from history
        _, new_questions = extract_from_history(result)
        domain = ""
        try:
            from urllib.parse import urlparse
            domain = urlparse(url).hostname or ""
            if domain.startswith("www."):
                domain = domain[4:]
        except Exception:
            pass
        await save_new_qa(new_questions, source_domain=domain)

        # Determine the outcome from our OWN page reading — never the agent's self-report (#89).
        agent_claimed = bool(result.is_successful())
        page_evidence = ledger.best_confirmation()
        if page_evidence is None:
            try:
                final_page = await read_page_evidence(browser)
                ledger.confirmations.append(final_page.to_dict())
                page_evidence = final_page
            except Exception as read_err:
                print(f"    ⚠️  [W{worker_id}] Could not read final page: {read_err}")
        outcome = decide_outcome(
            page=page_evidence, agent_claimed_success=agent_claimed,
            needs_human=ledger.needs_human, unanswered=ledger.unanswered_last,
        )
        success = outcome.status in js.SUBMITTED_STATUSES

        # ── Memory extraction (self-learning) ─────────────────────────────
        # Everything from here to the status write is best-effort: the outcome is already
        # decided, and a memory/metrics failure must never turn a submission into "failed".
        marker_learnings, llm_learnings = [], []
        try:
            mem_store = mem_store or _memory_store_or_none(worker_id)
            # 1. Marker-based: extract @@LEARNING tags the agent emitted
            marker_learnings = extract_learnings_from_markers(result)
            if marker_learnings and mem_store:
                store_learnings(mem_store, marker_learnings, url, success)
        except Exception as mem_err:
            print(f"    ⚠️  [W{worker_id}] Memory store failed (non-fatal): {mem_err}")
            mem_store = None
        # 2. LLM-based: use the configured LLM to summarise the run into procedural learnings
        try:
            if mem_store is None:
                raise RuntimeError("memory store unavailable")
            def _llm_call(prompt):
                """Use the user's configured LLM for memory extraction."""
                import asyncio
                from browser_use.llm.messages import UserMessage
                extraction_llm = config.get_llm()
                # Runs in a worker thread (asyncio.to_thread below), so it gets its own loop; calling
                # run_until_complete on the agent's thread failed every time ("another loop is running").
                resp = asyncio.run(asyncio.wait_for(
                    extraction_llm.ainvoke([UserMessage(content=prompt)]), timeout=30))
                return resp.completion if hasattr(resp, 'completion') else (resp.content if hasattr(resp, 'content') else str(resp))

            llm_learnings = await asyncio.to_thread(
                extract_learnings_via_llm, result, job_url=url, job_title=title, success=success,
                llm_call=_llm_call,
            )
            if llm_learnings:
                store_learnings(mem_store, llm_learnings, url, success)
        except Exception as mem_err:
            print(f"    ⚠️  [W{worker_id}] Memory extraction failed (non-fatal): {mem_err}")

        # ── Record metrics ────────────────────────────────────────────────
        run_finished_at = datetime.now(timezone.utc)
        memories_extracted_count = len(marker_learnings) + len(llm_learnings)
        try:
            MetricsStore().record_run(
                job_url=url, job_title=title, company=company,
                website_domain=domain, ats_platform=(mem_store.detect_ats_platform(domain) if mem_store else None),
                success=success, started_at=run_started_at, finished_at=run_finished_at,
                step_count=len(result.history),
                memories_injected=memories_injected_count,
                memories_extracted=memories_extracted_count,
                error_message=(next((e for e in reversed(result.errors()) if e), "")[:2000] or None)
                if not success else None,
            )
        except Exception as metrics_err:
            print(f"    ⚠️  [W{worker_id}] Metrics recording failed (non-fatal): {metrics_err}")

        evidence_fields = {
            "page_confirmation": (outcome.evidence.get("page_confirmation") if success else None),
            "needs_human_fields": ledger.needs_human,
            "agent_claimed_success": agent_claimed,
            "outcome_reason": outcome.reason,
            "blocked_exfil": ledger.blocked_exfil,
            "stopped_loads": len(ledger.stopped_loads),
        }
        if success:
            await save_job_status(url, outcome.status, **evidence_fields)
            print(f"  ✅ [W{worker_id}] Submitted (page-confirmed, awaiting email): {title} at {company}")
        elif outcome.status == js.NEEDS_HUMAN:
            await save_job_status(url, js.NEEDS_HUMAN, outcome.reason, **evidence_fields)
            print(f"  🙋 [W{worker_id}] Needs a person: {title} at {company} — {outcome.reason[:100]}")
        else:
            error_msg = next((e for e in reversed(result.errors()) if e), None)
            if error_msg and is_llm_auth_error(error_msg) and not _submit_was_clicked(url):
                # Not this job's fault: put it back in the queue and stop the run (see worker()).
                await save_job_status(url, "pending", LLM_AUTH_REASON)
                print(f"  🔑 [W{worker_id}] {LLM_AUTH_MESSAGE} ({title} at {company} stays in the queue)")
                return LLM_AUTH_STATUS
            error_msg = error_msg or ("Agent reported failure" if not agent_claimed else outcome.reason)
            await save_job_status(url, js.FAILED, error_msg[:2000], **evidence_fields)
            print(f"  ❌ [W{worker_id}] Failed: {title} at {company} — {error_msg[:100]}")
        return outcome.status

    except Exception as e:
        error_str = str(e)
        if is_llm_auth_error(error_str) and not _submit_was_clicked(url):
            await save_job_status(url, "pending", LLM_AUTH_REASON)
            print(f"  🔑 [W{worker_id}] {LLM_AUTH_MESSAGE} ({title} at {company} stays in the queue)")
            return LLM_AUTH_STATUS
        retriable = "security token" in error_str.lower() or "expired" in error_str.lower()
        if retriable and job.get("_retries", 0) < 1 and not _submit_was_clicked(url):
            job["_retries"] = job.get("_retries", 0) + 1
            refresh_credentials()
            await save_job_status(url, "pending", "credentials_expired_retry")
            print(f"  🔑 [W{worker_id}] Credentials expired on: {title} at {company} — refreshed, will retry")
            return "retry"
        await save_job_status(url, "failed", error_str[:2000])
        print(f"  ❌ [W{worker_id}] Error: {title} at {company} — {error_str[:100]}")
        return "failed"
    finally:
        try:
            await browser.close()
        except Exception as close_err:
            print(f"    ⚠️  [W{worker_id}] Browser cleanup error: {close_err}")


def _routed_resume(job: dict) -> str | None:
    """M7: pick the resume for this job (search > category > keywords > default) and record it for A/B stats."""
    try:
        try:
            from core.searches import store as search_store, rules as search_rules
        except ImportError:  # pragma: no cover
            from backend.core.searches import store as search_store, rules as search_rules
        resume, why = search_rules.route_resume(job, search_store.load())
        if not resume or not Path(resume["path"]).is_file():
            return None
        update_job(job["url"], resume_id=resume["id"], resume_route=why)
        print(f"  📄 Resume: {resume['name']} ({why})")
        return resume["path"]
    except Exception as e:  # never block an application on routing
        print(f"  ⚠️  Resume routing failed: {e}")
        return None


async def worker(name: str, worker_id: int, queue: asyncio.Queue, profile: dict, qa: dict, applied_labels: list, easy_apply: bool, stats: dict, cancel_flag: dict | None = None):
    """Worker that pulls jobs from queue and applies."""
    while True:
        if cancel_flag and cancel_flag.get("cancel_requested"):
            print(f"  🛑 [{name}] Stop requested — halting")
            break
        if stats.get(LLM_AUTH_STATUS):
            break  # the model key was rejected: every remaining job would fail the same way
        try:
            job = queue.get_nowait()
        except asyncio.QueueEmpty:
            break

        job_easy_apply = job.get("easy_apply", easy_apply) if job.get("easy_apply") is not None else easy_apply
        status = await apply_to_job(job, profile, qa, applied_labels, job_easy_apply, worker_id,
                                    resume_path_override=_routed_resume(job))
        stats[status] = stats.get(status, 0) + 1

        # On retry, put back in queue
        if status == "retry":
            queue.put_nowait(job)

        queue.task_done()


async def main():
    parser = argparse.ArgumentParser(description="Apply to collected jobs")
    parser.add_argument("--workers", type=int, default=1, help="Number of concurrent workers")
    parser.add_argument("--limit", type=int, help="Max jobs to process")
    parser.add_argument("--easy-apply", dest="easy_apply", action="store_true", default=True)
    parser.add_argument("--no-easy-apply", dest="easy_apply", action="store_false")
    args = parser.parse_args()

    jobs = load_json(JOBS_FILE, {})
    profile = load_json(CANDIDATE_PROFILE, {})
    qa = load_json(QA_FILE, {})
    LOGS_DIR.mkdir(exist_ok=True)

    # Filter pending jobs by type
    pending = [
        j for j in jobs.values()
        if j.get("status") == "pending"
        and (j.get("easy_apply") is True) == args.easy_apply
    ]

    if args.limit:
        pending = pending[:args.limit]

    if not pending:
        print("No pending jobs to apply to. Run collect_jobs.py first.")
        return

    applied_labels = submitted_labels(jobs)

    mode = "Easy Apply" if args.easy_apply else "Non-Easy Apply"
    print(f"Applying to {len(pending)} {mode} jobs with {args.workers} worker(s)\n")

    queue = asyncio.Queue()
    for job in pending:
        queue.put_nowait(job)

    stats = {}
    num_workers = min(args.workers, len(pending))

    # Background credential refresh every 14 min — auto-cancelled when workers finish
    cred_task = asyncio.create_task(credential_refresh_loop(14))

    workers = []
    for i in range(num_workers):
        if i > 0:
            await asyncio.sleep(5)  # stagger browser launches
        workers.append(
            asyncio.create_task(worker(f"W{i+1}", i+1, queue, profile, qa, applied_labels, args.easy_apply, stats))
        )
    await asyncio.gather(*workers)
    cred_task.cancel()

    print(f"\n{'='*60}")
    print(f"Results: {stats}")
    total_submitted = sum(1 for j in load_json(JOBS_FILE, {}).values() if js.is_submitted(j.get("status")))
    print(f"Total submitted (page-confirmed) across all runs: {total_submitted}")

    # Memory stats
    mem_stats = get_memory_store().get_stats()
    print(f"🧠 Agent memory: {mem_stats['total_memories']} memories across {mem_stats['unique_domains']} domains")


if __name__ == "__main__":
    asyncio.run(main())
