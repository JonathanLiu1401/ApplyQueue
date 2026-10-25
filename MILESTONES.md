# MILESTONES: v1 plan

Assumes 1 to 2 developers plus agents; durations are rough. Each milestone ends with a demo
and a tagged pre-release. Public repo: github.com/JonathanLiu1401/ApplyQueue. Project
development never uses Cursor cloud agents. Each milestone M2-M13 has a tracking issue.

## M0. Decisions + public repo (week 0-1) - done
- Decided 2026-10-24 (DESIGN 16): public repo under `JonathanLiu1401`; MIT license; all
  backends supported, Grok Bot recommended, local models as the headline no-subscription path;
  private sync repo via the user's own GitHub login (GitHub App device flow); any job domain;
  Gmail + Outlook with 7-day Google re-auth accepted; safeStorage vault in v1.
- Name: **ApplyQueue** (domains/npm not registered).
- Portal credentials and browser sessions live in the user's private sync repo by default
  (DESIGN 6.1). Open (option only): encrypt repo credentials (DESIGN 6.3).
- Done: LICENSE, README, CONTRIBUTING, CODE_OF_CONDUCT, SECURITY, issue/PR templates, CI
  (lint, typecheck, tests, build, license allowlist, gitleaks + personal-data scan over full
  history, 3-OS packaging).

## M1. Core library + schema + app shell (weeks 1-3) - scaffold done
- Done: pnpm monorepo; Electron + TypeScript + Vite desktop shell with stub routes
  (Get started, Queues + queue detail, Tracker, Resumes, AI backends with a Local card,
  How it works, Settings); `packages/schema` with zod schemas for profile, preferences and
  categories, answers, accounts/resumes; fictional starter packs; schema tests.
- Remaining: queue and ledger-event schemas; `packages/core` (ranking with user categories,
  location gates, bans, term matching, dedupe by job key + requisition number, ledger
  compaction, confirmation classifier, resume routing); golden tests with the fictional
  "Alex Example" persona; `applyqueue import-legacy` converter (local only).
- Exit: `pnpm test` green on 3 OSes; legacy import reproduces the existing queue counts.

## M2. Sources (weeks 2-4)
- Greenhouse, Ashby, Lever, Workday CXS, SmartRecruiters adapters (public JSON); GitHub
  aggregator lists; coverage reporting per board.
- Public `catalog/companies.json` (names + ATS slugs only).
- Exit: a queue's titles + locations produce a ranked list + coverage report.

## M3. MCP tool server + apply loop (weeks 3-5)
- Shared tool layer (DESIGN 4.5): queue (`next_job`, `skip_job`, `finish_job`), answers
  (`get_answer`, `report_unanswered`), secrets (`fill_secret`, `save_credential`,
  `save_session`), email (`get_email_code`, `label_confirmation`), `log_search_coverage`,
  browser (`browser.*` over Playwright/CDP), forms (`form.fields`, `form.fill`).
- MCP transports: stdio + 127.0.0.1 streamable HTTP with per-client tokens.
- `finish_job` enforces email proof (`pending-proof`); claims via SQLite + sync locks.
- Generated agent prompt with the core rules (DESIGN 9).
- Exit: Claude Code / any MCP client fills a fixture Greenhouse form end to end in dry run.

## M4. Electron shell, secrets, browser (weeks 4-7)
- Typed IPC hardening, safeStorage vault as a mirror of repo credentials (plus opt-in
  local-only mode), persistent agent Chromium profile + CDP, storageState export/import.
- Architecture view with live counts; answer-bank editor; unanswered-questions inbox.
- Exit: app runs on macOS/Windows/Linux dev builds with a working agent browser.

## M5. Email linking: Gmail + Outlook (weeks 6-8)
- Gmail API (Testing-mode app or BYO client); Microsoft Graph; IMAP fallback.
- Confirmation poller: classify (confirmation, assessment, interview, rejection), label,
  archive; OTP reader.
- Re-auth: token-age tracking, day-6 and `invalid_grant` notifications, one-click re-consent,
  `pending-proof` backfill, `email_link` status in the sync repo.
- Exit: confirmations in a Gmail and an Outlook test mailbox promote ledger rows; forced
  `invalid_grant` recovers with one click.

## M6. Onboarding: 3-step quick start + progressive setup (weeks 7-9)
- Quick start (DESIGN 8.1): upload resume -> profile pre-fill with per-field confirmation;
  titles + locations -> first queue; Start with "Who does the work?" (Grok Bot recommended,
  Run 100% locally, other AI).
- Progressive checklist (DESIGN 8.2): answer bank, work authorization, EEO choices,
  categories/starter packs, email, portals, sync, dry run, each surfaced when first needed.
- Exit: a non-technical tester goes from install to first review-first matches in under
  10 minutes, without docs.

## M7. Queues, tracker, resumes (weeks 8-10)
- Queues (DESIGN 8.3): editor (titles, locations, filters, excluded companies, boards),
  per-queue review-first/automatic mode, review inbox with bulk approve/skip, stats page
  (found, applied, skipped, confirmed, interviews, response rate), pause, daily cap, coverage.
- Tracker (DESIGN 8.4): cross-queue board fed by the ledger + email classifier, manual
  corrections, CSV export.
- Resumes (DESIGN 8.5): variants, routing per category/queue, A/B response analytics with a
  minimum-sample guard, ATS keyword checker (suggest only).
- Recruiter outreach (DESIGN 8.6): off by default, drafts only, never auto-sent.
- Exit: two queues run in parallel with correct per-queue stats; a confirmation email moves
  a tracker card; A/B view shows per-resume response rates on fixture data.

## M8. Local models: runtime detection, model recommender, in-app runner (weeks 8-11)
- Runtime detection (DESIGN 4.6): Ollama :11434, LM Studio :1234, llama-server :9931/:8080,
  custom OpenAI-compatible URL; binaries on PATH; hardware probe (VRAM / unified memory).
- Model recommender: vision-capable model per memory tier (Qwen3.5 / Qwen3-VL / Qwen3.6,
  Gemma 4, UI-TARS-1.5-7B GGUF), with sizes and quantizations; vision capability check.
- One-click download with progress and disk check (Ollama pull, `lms get`, Hugging Face GGUF
  + mmproj, `hf.co/` repos via Ollama).
- In-app agent runner (DESIGN 4.5): OpenAI-compatible tool calling, DOM-first form filling,
  screenshot fallback, step budgets, checkpoints, review gates; shared with API backends.
- Honest-expectations screen; overnight mode (powerSaveBlocker, quiet hours).
- Exit: on a 12-16 GB GPU and on an Apple silicon laptop, the recommended local model fills
  the fixture Greenhouse and Lever forms in dry run with no cloud calls.

## M9. Cloud backends + where-it-runs (weeks 9-12)
- Shared setup-prompt generator (`packages/prompts`); heartbeat-based "connected" status.
- Grok Bot (recommended): setup prompt + bot bundle (worker mode, state repo, routines).
- Claude: Claude Code CLI/Desktop (local MCP); Claude API computer/browser toolsets via the
  in-app runner; Claude Code cloud scheduled tasks and Claude in Chrome via setup prompt.
- ChatGPT: ChatGPT Work cloud browser + scheduled tasks via setup prompt; custom MCP over
  Secure MCP Tunnel (opt-in); Codex local MCP config.
- Gemini: Gemini API computer use via the in-app runner; Gemini CLI with `mcpServers` +
  headless settings; Gemini app tasks/schedules and Chrome auto browse via setup prompt.
- Cursor: Automations / cloud agents via setup prompt, Cursor CLI via local MCP (user choice;
  the project itself never develops with Cursor cloud agents).
- Worker mode for spare computer/VPS (headless service, start on boot).
- Exit: the same fixture run passes on Grok Bot, Claude Code, Claude API, Gemini API,
  ChatGPT Work, and a local model; two workers on two machines apply without duplicates.

## M10. Sync repo (weeks 10-12)
- "Sign in with GitHub" (GitHub App device flow; OAuth App fallback); auto-create a private
  state repo; scoped tokens for remote workers; union merge, lock files with TTL, heartbeats.
- Credentials and sessions in the repo (`credentials/portals.json`, `sessions/<id>/`,
  opt-in `browser-profiles/`); "must stay private" notice; visibility check before every
  credential write; worker `fill_secret` / `save_credential` / `save_session`.
- Exit: a second machine and a Grok Bot sign in to a portal using only the repo; a repo flipped
  to public blocks credential writes; a killed worker's lock expires and another finishes.

## M11. Packaging, signing, updates (weeks 11-13)
- macOS notarization, Windows signing, electron-updater on GitHub Releases, beta channel,
  release workflow on tags; e2e tests in the CI matrix.
- Exit: signed v1.0.0-rc installs and auto-updates on all three OSes.

## M12. v1.0 launch (week 13-14)
- Docs (privacy, architecture, one guide per backend including Local), 3 starter packs with
  fictional data, screenshots with the fictional persona.
- Maintainer migration with `import-legacy`, run in parallel for a week before cutover.

## M13. Password-manager integration (post-v1)
- 1Password and Bitwarden as optional `fill_secret` sources, plus write-back of generated
  portal passwords; the sync repo stays the default.
- If the maintainer approves DESIGN 6.3: encryption of repo credentials and sessions.
- Exit: a portal login is filled from each manager without the value reaching the model.

## Post-v1
- More ATS adapters (Phenom, Avature, Oracle, iCIMS, SuccessFactors, Jobvite), verified
  Google OAuth app (removes the 7-day re-auth), mobile notifications, team/coach mode.
