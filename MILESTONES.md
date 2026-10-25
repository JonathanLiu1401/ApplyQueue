# MILESTONES: v1 plan

Assumes 1 to 2 developers plus agents; durations are rough. Each milestone ends with a demo
and a tagged pre-release. Public repo: github.com/JonathanLiu1401/ApplyQueue. Project development never uses Cursor cloud agents.

## M0. Decisions (week 0-1)
- Decided 2026-10-24 (DESIGN 16): public repo under `JonathanLiu1401`; MIT license
  (`LICENSE` added); all backends supported, Grok Bot recommended; private sync repo via the
  user's own GitHub login (GitHub App device flow); any job domain with user-defined
  everything; Gmail + Outlook with 7-day Google re-auth accepted; safeStorage vault in v1.
- Name picked: **ApplyQueue** (domains/npm not registered). Remaining: confirm TS port.
- Decided: portal credentials and browser sessions live in the user's private sync repo by
  default (DESIGN 6.1); the existing private repo's password files and profiles stay as they are.
- Open (option only): encrypt repo credentials with age/libsodium (DESIGN 6.3).
- Exit: decisions recorded in `DECISIONS.md`; public repo created (LICENSE, README, community files, CI).

## M1. Core library + schema (weeks 1-3)
- `packages/schema`: profile, preferences, answers, accounts, ledger event schemas + zod types.
- `packages/core`: ranking with user categories, location gates, bans, term matching,
  dedupe (job key + requisition number), ledger compaction, confirmation classifier
  (port of `confirm.py` NOT_PROOF/IS_PROOF), resume routing rules.
- Golden tests ported from `tests/` with a synthetic persona "Alex Example" (no real PII).
- Generic schema: categories, filters, bans, answers all user data; no field built in.
- `applyqueue import-legacy` converter for legacy files (runs locally only).
- Exit: `pnpm test` green on 3 OSes; legacy import reproduces today's queue counts.

## M2. Sources (weeks 2-4)
- Greenhouse, Ashby, Lever, Workday CXS, SmartRecruiters adapters (public JSON); GitHub
  aggregator lists; coverage reporting.
- Public `catalog/companies.json` seeded from the verified board list (names + ATS slugs only).
- Exit: discovery run produces ranked queue + coverage report.

## M3. MCP server + apply loop (weeks 3-5)
- Tools: next_job, skip_job, finish_job, get_answer, report_unanswered, fill_secret,
  get_email_code, label_confirmation, log_search_coverage.
- finish_job enforces email proof; claims via local SQLite + sync locks.
- Generated agent prompt with the core rules (answer every question, EEO decline still picks).
- Exit: Claude Code / any MCP client applies to a fixture Greenhouse form end-to-end in dry-run.

## M4. Electron shell, secrets, browser (weeks 4-7)
- Main/preload/renderer skeleton, contextIsolation, typed IPC.
- safeStorage vault as a local mirror of repo credentials (plus opt-in local-only mode),
  persistent agent Chromium profile + CDP MCP, storageState export/import.
- Dashboard: queue, ledger, unanswered questions, answer-bank editor.
- Architecture view (Discover -> Queue -> Apply -> Confirm email -> Log) with live counts.
- Exit: app runs on macOS/Windows/Linux dev builds.

## M5. Email linking: Gmail + Outlook (weeks 6-8)
- Gmail API as an unverified Testing-mode app (or BYO client); Microsoft Graph
  (Mail.ReadWrite + offline_access, folder or category labeling); IMAP fallback.
- Confirmation poller: classify, label, archive; OTP code reader + archive.
- Re-auth: token-age tracking, day-6 and `invalid_grant` OS notification + tray badge +
  dashboard banner, one-click loopback re-consent, `pending-proof` parking and backfill,
  `email_link` status in the sync repo for remote workers.
- Exit: real confirmations in a Gmail and an Outlook test mailbox are labeled/archived and
  promote ledger rows; a forced `invalid_grant` recovers with one click and backfills.

## M6. Onboarding wizard (weeks 7-9)
- Resume import -> profile/knowledge extraction (via chosen backend), field confirmation,
  work-auth questionnaire, EEO choices, categories (blank or starter pack), resume rules,
  email, portals (vault), backend pick from the capability matrix (direct sign-in or
  generated setup prompt), dry run.
- Exit: a non-technical tester completes setup in under 20 minutes without docs.

## M7. Backends + where-it-runs (weeks 8-11)
- Shared setup-prompt generator (`packages/prompts`): rules, non-secret prefs, worker install,
  sync repo URL, schedule; "regenerate" on change; heartbeat-based "connected" status.
- Grok Bot (recommended): setup prompt + bot bundle (worker mode, state repo, routines).
- Claude: Claude Code CLI/Desktop (direct, local MCP); Claude API computer + browser
  toolsets with the app executing actions; Claude Code cloud scheduled tasks and Claude in
  Chrome via setup prompt + sync repo.
- ChatGPT: ChatGPT Work cloud browser + scheduled tasks via setup prompt + sync repo; custom
  MCP over Secure MCP Tunnel as an opt-in for Business/Enterprise/Edu; Codex local MCP config.
- Cursor: Automations / cloud agents via setup prompt, Cursor CLI via local MCP (user choice;
  the project itself never develops with Cursor cloud agents).
- Local: Playwright + vision-model loop over Ollama (Qwen3-VL) or Hugging Face
  (UI-TARS-1.5-7B / Qwen3-VL via transformers, vLLM, TGI); hardware check in the wizard.
- Worker mode for spare computer/VPS (headless service, start on boot); overnight mode
  with powerSaveBlocker and quiet hours.
- Exit: the same fixture run passes on Grok Bot, Claude Code, Claude API, ChatGPT Work, and
  Ollama; two workers on two machines apply in parallel without duplicates.

## M8. Sync repo (weeks 9-11)
- "Sign in with GitHub" via the project's GitHub App (device flow, expiring user tokens;
  OAuth App device flow fallback), auto-create private state repo in the user's own account;
  scoped token instructions for remote workers; layout from DESIGN 6,
  union merge, lock files with TTL, heartbeats.
- Credentials and sessions in the repo: `credentials/portals.json`, `sessions/<id>/storage.json`,
  opt-in `browser-profiles/`; "must stay private" onboarding notice; visibility check via the
  GitHub API before every credential write/push (refuse + warn if not private); profile locks;
  worker `fill_secret` / `save_credential` / `save_session`; setup prompts tell bots where to read.
- Exit adds: a second machine and a Grok Bot sign in to a portal using only the repo, and a
  repo flipped to public blocks credential writes.
- Exit: kill a worker mid-claim; lock expires; another worker completes; ledger stays clean.

## M9. Packaging, signing, updates (weeks 10-12)
- electron-builder: dmg/zip, NSIS, AppImage/deb; macOS notarization; Windows signing;
  electron-updater on GitHub Releases; beta channel.
- CI matrix with lint/test/e2e/gitleaks + dependency license check (MIT-compatible) +
  starter-pack PII check; release workflow on tags.
- Exit: signed v1.0.0-rc installs and auto-updates on all three OSes.

## M10. v1.0 launch (week 12-13)
- Docs (privacy, architecture, one guide per backend), 3 starter packs with fictional sample
  data only (hardware internships, software, one non-tech field), issue templates,
  CONTRIBUTING (MIT inbound = outbound), SECURITY.md.
- Migrate the maintainer's own setup with `import-legacy` and run in parallel for a week before cutover.

## M11. Password-manager integration (post-v1)
- 1Password (CLI / SDK service account) and Bitwarden (CLI) as optional sources for
  `fill_secret`, plus write-back of generated portal passwords; the sync repo stays the default.
- If the maintainer approves DESIGN 6.3: age/libsodium encryption of repo credentials and sessions.
- Exit: a portal login is filled from each manager without the value reaching the model.

## Post-v1
- More ATS adapters (Phenom, Avature, Oracle, iCIMS, SuccessFactors, Jobvite), verified
  Google OAuth app (removes the 7-day re-auth), mobile notifications, team/coach mode.
