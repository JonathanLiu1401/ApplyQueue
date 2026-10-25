"""Task prompt for CLI backends, and the Grok Bot setup prompt (M3). No secrets in either."""
from __future__ import annotations

try:
    from core.apply_policy import ACCOUNT_POLICY, FORM_POLICY
except ImportError:  # pragma: no cover
    from backend.core.apply_policy import ACCOUNT_POLICY, FORM_POLICY

# browser-use action names → our MCP tool names, so both backends share one policy text.
_RENAMES = [
    ("submit_form_guarded(index)", "form_submit(ref)"),
    ("submit_form_guarded", "form_submit"),
    ("autofill_known_fields", "form_autofill"),
    ("verify_form_complete", "form_scan"),
    ("scan_form", "form_scan"),
    ("call report_confirmation", "call finish_job (it re-reads the page for the confirmation)"),
    ("report_confirmation", "finish_job"),
    ("fill_site_password(index, mode) on each password box — mode=\"new_account\"",
     "fill_secret(ref, \"site_password_new\") on each password box"),
    ("mode=\"sign_in\" for an existing account", "\"site_password\" for an existing account"),
    ("fill_site_password with\n   mode=\"new_account\"", "fill_secret with\n   \"site_password_new\""),
    ("fill_site_password", "fill_secret"),
    ("<secret>account_email</secret>", "fill_secret(ref, \"account_email\")"),
    ("different element index", "different ref from a fresh browser_snapshot"),
    ("(input with clear=true)", "(browser_type with clear=true)"),
    ("(use the dropdown-options / select actions for native <select>)", "(browser_select for native <select>)"),
    ("Your own done(success=true)", "Your own finish_job(result=\"submitted\")"),
    ("success=false", "result=\"failed\""),
]

BROWSER_USE_ONLY = ("submit_form_guarded", "autofill_known_fields", "verify_form_complete", "scan_form",
                    "report_confirmation", "fill_site_password", "<secret>", "done(")


def mcp_policy_text() -> str:
    text = FORM_POLICY + "\n" + ACCOUNT_POLICY
    for a, b in _RENAMES:
        text = text.replace(a, b)
    return text


TASK_PROMPT = """\
You are ApplyQueue's job-application agent. You fill in and submit job applications for the user.

TOOLS: use ONLY the tools of the "applyqueue" MCP server. Do not run shell commands, read or write
files, or browse the web with any other tool — the applyqueue browser is the only browser you may use.

LOOP
1. Call next_job. If it returns done=true, stop: there is nothing left to do.
2. Call get_candidate_context once per job: these are the ONLY facts you may use in answers.
3. browser_navigate to the job URL. Use browser_snapshot to see the page (elements carry a ref such
   as "0:12"); browser_click / browser_type / browser_select / browser_press act on a ref.
   The Apply button often leads to a careers page; explore it (scroll with browser_press PageDown,
   click "Apply now") until you reach the form. If a page is blank, browser_wait and snapshot again.
4. Fill the form (rules below). Upload files with browser_upload_resume. Never type an email or
   password yourself for an account: use fill_secret. Contact-email fields: fill_secret(ref, "email").
5. Verification codes: if the site sends a code by email, call email_get_otp (never open a webmail site).
   If no mailbox is linked, call report_needs_human("verification code", ...).
6. Submit with form_submit(ref) — it refuses while questions are unanswered.
7. Call finish_job(result, summary): "submitted" only when the page shows a confirmation,
   "needs_human" when a person must finish it, "failed" otherwise. If the job is not a real
   application (video funnel, closed posting, recruiter pitch), call skip_job(reason) instead.
8. Go back to step 1.

Be honest: ApplyQueue checks the page and network evidence itself, so claiming success without a
confirmation only marks the job as unconfirmed.

{policy}
"""


def build_task_prompt() -> str:
    return TASK_PROMPT.format(policy=mcp_policy_text())


def grok_bot_setup_prompt(backends: list[dict] | None = None, schedules: dict | None = None) -> str:
    """Text the user pastes into Grok Bot to set up ApplyQueue with a subscription CLI.

    Built only from static text and public CLI facts: no API keys, tokens, passwords, emails,
    profile data or local paths, so it is safe to paste anywhere.
    """
    lines = [
        "Help me set up ApplyQueue (a local desktop job-application app) with a subscription coding CLI.",
        "",
        "Context:",
        "- ApplyQueue runs on my computer and starts the CLI I choose as a subprocess. The CLI connects to",
        "  ApplyQueue's local MCP server named \"applyqueue\" (browser, form, fill_secret, email, next_job,",
        "  skip_job, finish_job tools) and must not use its own shell or file tools for this.",
        "- I sign in to the CLI myself with its own login command. Never ask me for, read, copy or move a",
        "  CLI token or credential file, and never put a token into ApplyQueue or a sync repo.",
        "",
        "Supported CLIs and their login commands:",
    ]
    for b in backends or []:
        if b.get("runnable"):
            extra = " (opt-in; ApplyQueue shows Anthropic's terms first)" if b.get("requires_terms") else ""
            lines.append(f"- {b['name']}: `{b['login_command']}`{extra}")
        else:
            lines.append(f"- {b['name']}: API key only, entered in ApplyQueue Settings → AI model")
    lines += [
        "",
        "Please walk me through, one step at a time:",
        "1. Installing the CLI I pick (official installer only) and checking `<cli> --version`.",
        "2. Running its login command in my own terminal and confirming the login status command says I am signed in.",
        "3. In ApplyQueue: Settings → Agent backends → enable that CLI, click \"Check again\", then \"Use for applying\".",
        "4. Running one test application from the queue and reading the result together.",
        "Do not ask me to paste any key, token or password into this chat.",
    ]
    rows = [r for r in (schedules or {}).get("searches", []) if not r.get("paused")]
    if rows:
        lines += ["", "Scheduled searches (also in my private sync repo at schedules/searches.json):"]
        lines += [f"- {r['id']} \"{r['name']}\": cron `{r['cron']}` ({r.get('tz') or 'local'} time), mode {r['mode']}"
                  for r in rows]
        lines += ["", *_RUNNER_RULES]
    return "\n".join(lines) + "\n"


_RUNNER_RULES = [
    "Rules for running a scheduled search (always-on runner):",
    "- One runner per search: before a run, pull the sync repo and create locks/search-<id>.json with",
    "  {\"name\": \"search-<id>\", \"worker\": <your name>, \"device\": <your id>, \"claimed_at\": <UTC ISO>,",
    "  \"ttl_sec\": 10800}, commit and push. If the file already exists, belongs to someone else and",
    "  claimed_at + ttl_sec is in the future, or your push loses the race, skip this run.",
    "- Heartbeat: if a run is still going after 1 hour, rewrite claimed_at in your lock and push, so",
    "  nobody takes it over. If the lock now belongs to someone else, finish the job you are on and stop.",
    "- schedules/searches.json is shared by every device: only edit entries whose device is yours, and",
    "  keep everyone else's entries when you write it.",
    "- Catch-up: if you missed one or more slots (sleep, downtime), run ONCE now, then wait for the",
    "  next slot after now. Never replay missed slots. Keep runs at least 15 minutes apart.",
    "- Before running, read schedules/runs.jsonl; if another device already started this search after",
    "  the slot you are about to run, skip it.",
    "- Rate limits (HTTP 429, \"too many requests\", quota): stop this run and back off 15 minutes,",
    "  doubling each time up to 24 hours; a clean run resets the backoff.",
    "- Expired login (job site or AI sign-in): stop, pause this search, and tell me which sign-in to",
    "  renew. Never ask me for the password or token in chat; I sign in myself, then say \"resume\".",
    "- Results: append each new posting as one JSON line to queue/postings/<yyyy-mm>.jsonl with",
    "  job_key (sha1 of the canonical URL is fine), url, title, company, location, source, search_id,",
    "  added_at and device; ApplyQueue imports them (review-first unless the search mode is auto).",
    "- After the run: append {search_id, device, started_at, finished_at, outcome} to",
    "  schedules/runs.jsonl (outcome: ok, rate_limit, auth, error), delete your lock file, commit, push.",
    "- Never write keys, tokens, passwords, cookies or emails into the repo or this chat.",
]

_TARGETS = {
    "grok": "Grok Bot (always-on agent)",
    "chatgpt": "a ChatGPT scheduled task",
    "claude": "a Claude scheduled task in the cloud",
    "codex": "Codex on my computer",
    "cursor": "Cursor on my computer",
}


def schedule_runner_prompt(export: dict, target: str = "grok") -> str:
    """Prompt that makes an always-on agent run my saved-search schedules.

    Contains my own search terms and schedules from ApplyQueue; never keys, tokens, emails,
    profile data or local paths.
    """
    if target not in _TARGETS:
        raise ValueError("target must be one of: " + ", ".join(_TARGETS))
    rows = export.get("searches", [])
    lines = [f"Please run my ApplyQueue saved-search schedules as {_TARGETS[target]}.", ""]
    if not rows:
        lines.append("No searches have a schedule yet. Set one in ApplyQueue → Searches first.")
        return "\n".join(lines) + "\n"
    lines.append("Schedules (cron fields: minute hour day-of-month month day-of-week):")
    for r in rows:
        state = " [PAUSED, do not run]" if r.get("paused") else ""
        locs = ", ".join(r.get("locations") or []) or "any location"
        lines.append(f"- {r['id']} \"{r['name']}\"{state}: `{r['cron']}` in {r.get('tz') or 'local'} time; "
                     f"titles: {', '.join(r.get('titles') or [])}; locations: {locs}; mode: {r['mode']}"
                     + (f"; at most {r['max_jobs']} jobs" if r.get("max_jobs") else ""))
    lines.append("")
    if target in ("codex", "cursor"):
        lines += [
            "On this computer, do not reimplement the collector. Register an OS scheduler entry that runs the",
            "ApplyQueue backend headless every 15 minutes: `<ApplyQueue backend> --run-schedules-once`",
            "(macOS: a launchd LaunchAgent with StartInterval 900; Linux: a crontab line `*/15 * * * *`;",
            "Windows: Task Scheduler, repeat every 15 minutes). It runs only searches that are due, one",
            "at a time, holds the cross-device lock, catches up once after sleep, backs off on rate limits",
            "and pauses with a re-auth notice when a login expired. Show me the entry before adding it, and",
            "find the backend binary inside the installed ApplyQueue app (or `python backend/main.py` in a",
            "source checkout) instead of guessing a path.",
        ]
    else:
        lines += [
            "Create one recurring task per search at the times above (or one task every 15 minutes that",
            "checks which searches are due). Each run searches public job boards for the titles and",
            "locations and records new postings in my private ApplyQueue sync repo. If you cannot reach",
            "the sync repo, send me the new postings as a list instead and do not try to apply.",
        ]
    lines += ["", *_RUNNER_RULES]
    return "\n".join(lines) + "\n"
