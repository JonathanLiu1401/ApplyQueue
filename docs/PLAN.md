# PLAN: ApplyQueue on LangHire

Status: 2026-10-24. Supersedes `docs/applyqueue/MILESTONES.md`; product decisions in
`docs/applyqueue/DESIGN.md` §16 still apply. Repo `JonathanLiu1401/ApplyQueue` is **private** and
stays private until the publish gate (§6). No GitHub fork of LangHire is created (forks of public
repos are public); LangHire's history was merged into this private repo instead.

## 1. What happened (M1, step 1)

- LangHire (`github.com/jaimaann/LangHire`, MIT, upstream tip `e43d8fa`) was merged with full
  history (`git merge --allow-unrelated-histories`); the Electron scaffold is preserved on branch
  `archive/electron-scaffold` and removed from `main`.
- `LICENSE` keeps "Copyright (c) 2026 LangHire Contributors" and adds ApplyQueue's line beneath.
- README states "ApplyQueue is a fork of LangHire by jaimaann"; the upstream README is kept at
  `docs/LANGHIRE_README.md`.
- CI: `ci.yml` = gitleaks + hashed-denylist personal-data scan over full history + npm/Python
  license checks (Linux, every push). Upstream LangHire history is public third-party content:
  it is exempt from the generic email/phone rules (`scripts/privacy-scan.upstream`) but still
  checked against the denylist.

## 2. LangHire audit (as of upstream `e43d8fa`, 2026-06-29)

### 2.1 Architecture
- **Tauri v2 shell** (`src-tauri/`, ~100 lines of Rust): resolves the data dir
  (`~/.config/langhire`, `~/Library/Application Support/langhire`, `%APPDATA%\langhire`), spawns the
  PyInstaller sidecar `binaries/langhire-backend-<target>`, kills it on exit.
- **UI** (`src/`): React 19 + Vite + TS, react-router, i18next (13 locales), pages for
  Dashboard, Setup wizard, Profile, Jobs, Apply, Memory, QA, Logs, Settings, Feedback.
- **Sidecar** (`backend/main.py`, ~2.1k lines FastAPI): per-launch bearer token
  (`_AuthMiddleware`), rate limiter, parent-process watchdog, stale-job recovery. Endpoints:
  `/profile`, `/settings/llm`, `/llm/test`, `/llm/ollama-models`, `/jobs/collect`, `/jobs*`,
  `/apply/start|stop|status` (in-process worker threads + asyncio queue), `/auth/login/{service}`
  (opens a headed browser for the user to log in to LinkedIn/Gmail), `/memory/*`, `/qa/*`,
  `/metrics/*`, `/logs/*`, `/resume/tailor*`, `/cover-letter/generate`, `/setup/*`, `/plugins`.
- **Job sources**: YAML plugins in `backend/sources/plugins/` (linkedin, indeed, glassdoor,
  ziprecruiter, naukri, reed, seek, stepstone) driven by a browser-use agent.
- **Telemetry**: Google Analytics Measurement Protocol (`src/lib/analytics.ts`, on by default) and
  Sentry (`@sentry/browser`), both inert unless `VITE_GA_*` / `VITE_SENTRY_DSN` are set at build
  time. ApplyQueue builds set neither; M1 makes telemetry opt-in and off by default.

### 2.2 Backends / LLM config
`backend/core/llm_factory.py:create_llm(settings)`: `openai`, `anthropic`, `bedrock` (boto3
session patch), `ollama` (ChatOllama, `num_ctx` 32768, `num_predict` 4096, temp 0), `gemini`,
`openrouter`, `openai_compatible` (LM Studio / llama.cpp / vLLM work here). Settings live in the
data dir; `/llm/test` does a live round trip with friendly errors. All paths are API-key or
local; **no subscription sign-in exists**.

### 2.3 Apply loop (`cli/apply_jobs.py:apply_to_job`)
1. `claim_job` under a file lock on `jobs.json`; skip `BLOCKED_DOMAINS`.
2. Persistent `BrowserSession` (`BROWSER_PROFILE_DIR`); login preamble verifies the LinkedIn feed
   and the Gmail inbox are logged in.
3. One browser-use `Agent` per job: `use_vision=True`, `MAX_STEPS = 70` (callback raises),
   `max_failures=10`, loop detection, `sensitive_data` placeholders (`<secret>email</secret>` …),
   memory injected via `extend_system_message`, resume as the only uploadable file.
4. Prompt rules include **"If you've been stuck on the same form field for more than 5 steps,
   skip it or call done with success=false."** (line ~244). This lets required or optional
   questions go unanswered; M2 replaces it.
5. Markers: `@@JOB_APPLIED`, `@@QUESTION` (saved to the QA repository), `@@LEARNING`.
6. `success = result.is_successful()` → `save_job_status(url, "applied")`. That is the whole
   proof of submission (**bug #89**, below).
7. With no password configured the prompt tells the agent to create ATS accounts with "a strong
   password like 'JobApp2026!'". The generated password is **never stored**, so the user cannot
   log back in, and the example invites a reused weak password. M2/M5 fix this: generated
   per-site passwords go into the credential store first.
8. Exceptions: credential-expiry detection retries once after a re-login prompt.

### 2.4 Bug #89 ([jaimaann/LangHire#89](https://github.com/jaimaann/LangHire/issues/89))
A job is marked applied when the model calls `done(success=True)`, even if the submit POST
returned HTTP 500. The reporter suggests watching CDP `Network.responseReceived` for 4xx/5xx on
POSTs. ApplyQueue's fix (M2): `applied` requires (a) a confirmation-page check (URL/text
classifier plus a screenshot saved to the ledger), (b) no 4xx/5xx on the submit POST (CDP), and
(c) a matching confirmation email when one arrives (M4); otherwise the status is
`submitted_unverified` or `failed`.

### 2.5 Gmail OTP use
There is no Gmail API. The agent opens `mail.google.com` in the same logged-in browser profile,
reads the newest code, and types it ("NOT a blocker — always attempt…"). It works but costs
steps and vision tokens, and it only covers Gmail. M4 replaces it with Gmail API / Microsoft
Graph `email.get_otp` and `email.find_confirmation` tools, keeping the browser fallback.

### 2.6 Data / DB schema
- `jobs.json` (+ `jobs.json.lock`, filelock): job list with status
  (`new|in_progress|applied|failed|skipped…`).
- `memory_store.db` (SQLite): `schema_version`; `memories(id, website_domain, ats_platform,
  category, content, content_hash UNIQUE, success, confidence, job_url, created_at, updated_at,
  access_count)`; `qa_repository(id, question, normalized, answer, question_type, source_domain,
  times_seen, merged_into_id, …)`.
- `metrics.db`-style tables: `run_metrics(run_id, job_url, job_title, company, website_domain,
  ats_platform, success, error_message, started_at, finished_at, duration_seconds, step_count,
  memories_injected, memories_extracted, cost_usd, run_type, …)`, `run_logs(run_id, job_url,
  timestamp, level, message, …)`.
- Profile/settings JSON in the data dir; browser profile dir holds sessions.
ApplyQueue maps these to the DESIGN.md ledger/queue model. The sync repo (M5) stores JSONL;
SQLite stays a local cache.

### 2.7 Build / packaging (3 OSes)
- `scripts/build-backend.sh|.ps1`: PyInstaller `--onefile` sidecar (`--collect-all browser_use
  playwright`); macOS signs it with `Entitlements.plist`.
- `build.yml`: on `v*` tags, tauri-action matrix for macOS arm64 + x64, Linux x64 + arm64, and
  Windows x64, with Apple signing/notarization secrets. `tauri.conf.json` targets `all`.
- `test.yml`: 4-OS matrix (tsc, eslint, vite build, vitest+coverage, pytest+coverage, Playwright
  Chromium, integration test with an OpenRouter key, backend binary + binary integration test).
- Licensing finding: **PyMuPDF (`fitz`) is AGPL-3.0** (or Artifex commercial) and is bundled
  into the sidecar binary, along with `pymupdf4llm`/`pymupdf-layout` (no license metadata).
  Distributing that binary under MIT terms is a problem. M1 replaces it (e.g. `pypdf`, BSD)
  before any public binary.

### 2.8 Tests (run on the box, Linux x64, 2026-10-24)
- `uv run pytest`: **all pass** (~590 tests across core, memory, resume, sources, API).
- `vitest`: **481 pass / 8 fail**. All 8 are `translations.test.ts` because `src/locales/{it,zh}/jobs.json`
  are missing: LangHire's `.gitignore` ignores `jobs.json` everywhere, so those translations
  were never committed (an upstream bug). Fix in M1 (negated ignore + add files).
- `npm run build` (tsc + vite): passes. Backend binary and `tauri build` results: see the M1 status.

### 2.9 Other findings
- CSP is `default-src 'self' 'unsafe-inline' 'unsafe-eval' https: http: …`. Tighten in M1.
- `npm ci` failed on upstream's lockfile (out of sync: `@emnapi/runtime`); the lock is refreshed.
- The repo has an upstream tag literally named `main`; we never fetch upstream tags
  (`remote.upstream.tagOpt --no-tags`).

## 3. Integration architecture: subscription CLIs + MCP

```
 UI (Tauri/React) ─HTTP+token─> Sidecar (FastAPI)
                                  ├─ Queue / ledger / evidence store (jobs, proofs, QA, profile)
                                  ├─ Backend adapters
                                  │    A. in-process  : browser-use Agent + create_llm()  (API keys, Ollama,
                                  │                     LM Studio, llama.cpp, OpenRouter, Bedrock …)  ← kept
                                  │    B. CLI + tools : spawn official CLI headless; it calls OUR MCP server
                                  │    C. CLI handoff : spawn CLI with the whole job task + our MCP queue tools
                                  └─ ApplyQueue MCP server (stdio, launched per run by the CLI)
                                       browser.*  navigate, snapshot (a11y tree + screenshot), click, type,
                                                  select, upload_resume, wait, network_errors   (Playwright, our profile)
                                       form.*     list_fields, answer_for(question) (profile + QA bank, truthful),
                                                  fill, unanswered_fields
                                       email.*    get_otp(site), find_confirmation(company, since) (Gmail/Graph)
                                       queue.*    claim_next, record_result(status, evidence), ask_user(question)
```

- **Rules live in the tools, not only the prompt.** `queue.record_result("applied")` is refused
  unless `form.unanswered_fields()` is empty (answer-every-question rule) and evidence exists:
  a confirmation snapshot plus no failing submit POST; the email proof is attached later (M4).
- **Sign-in is the vendor's own.** Adapters check `<cli> --version` and its auth status, and if
  needed open the vendor's own login command in a terminal window (`claude`, `grok login`,
  `agent login`, `codex login`) under the user's account. ApplyQueue never reads, copies, or
  proxies the CLI's token files and never calls vendor endpoints with them.
- **Process model.** One CLI subprocess per job (fresh context, bounded cost), streaming JSON
  parsed into the run log; a kill switch and step/time budget per job; a per-vendor concurrency
  cap (default 1) to respect plan limits.
- **Grok Bot (recommended)**: no adapter needed. A generated setup prompt tells the user's Grok
  Bot to clone their private sync repo and run ApplyQueue's headless runner, or to use the MCP
  tools directly. The bot keeps its own vault; secrets are never in the prompt.
- API keys and Ollama/local models stay first-class (adapter A); local models are the headline
  "free" path (M6).

### 3.1 Vendor terms and official CLIs (researched 2026-10-24)

| Vendor | Official CLI, headless + MCP | Subscription use by a third-party app driving the CLI | Verdict |
|---|---|---|---|
| **Claude Code** (Anthropic) | `claude -p "<task>" --mcp-config aq.json --allowedTools "mcp__applyqueue__*" --output-format stream-json --verbose` ([headless](https://code.claude.com/docs/en/headless)); Agent SDK ([docs](https://code.claude.com/docs/en/agent-sdk/)) | [Legal & compliance](https://code.claude.com/docs/en/legal-and-compliance): third-party developers may not offer claude.ai login or route requests through Free/Pro/Max credentials for their users, and may not collect or store tokens. The same page says this does not prevent an end user from signing in to the **unmodified** Claude Code binary with their own subscription. The [Agent SDK + plan](https://support.claude.com/en/articles/15036540-use-the-claude-agent-sdk-with-your-claude-plan) article (updated 2026-10-07) says `claude -p` and third-party apps draw on the plan's limits. See also [sdk-ts#382](https://github.com/anthropics/claude-agent-sdk-typescript/issues/382). | **Gray / restricted.** Default to an Anthropic API key. Offer "use my installed Claude Code" as an opt-in with a warning: the user installs and logs in to the unmodified binary themselves; we never bundle it, never offer a "Sign in with Claude" button, never touch its tokens. **Needs Art's decision.** |
| **Grok Build** (xAI) | `grok -p "<task>" --output-format streaming-json --always-approve --no-auto-update`; `grok mcp add …`; `grok agent stdio` (ACP) ([CLI reference](https://docs.x.ai/build/cli/reference), [headless](https://docs.x.ai/build/cli/headless-scripting), [launch post](https://x.ai/news/grok-build-cli)) | SuperGrok / X Premium+ via `grok login` / `--device-auth`, or `XAI_API_KEY`. xAI promotes headless/ACP for scripts and "agent orchestration apps". [ToS](https://x.ai/legal/terms-of-service) (2026-10-12) forbids sharing credentials or accounts; the [AUP](https://x.ai/legal/acceptable-use-policy) forbids "using bots to access" the service in the sense of copying or reverse-engineering, circumventing rate limits, and reselling. | **Allowed** via the official CLI/ACP under the user's own login. One user, their own machine, respect rate limits. |
| **Grok Bot** | n/a (the bot runs ApplyQueue itself) | Setup prompt + private sync repo; the bot uses its own sign-in and vault. | **Allowed** (recommended path). |
| **Cursor CLI** | `agent -p "<task>" --output-format stream-json --force --trust --approve-mcps` with `.cursor/mcp.json`; `agent acp` ([headless](https://cursor.com/docs/cli/headless), [parameters](https://cursor.com/docs/cli/reference/parameters), [auth](https://cursor.com/docs/cli/reference/authentication), [MCP](https://cursor.com/docs/mcp)) | `agent login` (the user's account) or `CURSOR_API_KEY`. [ToS](https://cursor.com/terms-of-service) §1.5: no reverse engineering, scraping, or reselling. Cursor staff on the [forum](https://forum.cursor.com/t/inquiry-regarding-official-api-or-commercial-license-for-using-cursors-engine-as-a-backend-component/165063/5) (2026-07-10): headless CLI/SDK as a backend is "supported and intended"; just don't resell or give third parties access under your account. Unofficial proxies to private endpoints are not OK ([thread](https://forum.cursor.com/t/does-using-oh-my-pi-s-cursor-provider-or-an-openai-compatible-proxy-to-the-same-endpoints-violate-cursor-s-tos/167778)). | **Allowed** via the official CLI under the user's own account. |
| **ChatGPT / Codex** (OpenAI) | `codex exec --json "<task>"` with `[mcp_servers.applyqueue]` in `config.toml` ([non-interactive](https://learn.chatgpt.com/docs/non-interactive-mode)); `codex app-server --listen stdio://` ([app-server](https://developers.openai.com/siwc/token-sharing-open-source/codex-app-server)) | `codex exec` under the user's own `codex login` is the official CLI used by its owner. Separately, [Sign in with ChatGPT terms](https://openai.com/policies/sign-in-with-chatgpt-terms/) (2026-09-29) explicitly allow open-source local apps to use a ChatGPT plan through OpenAI's SIWC flow. Conditions: the app keeps its own identity, tokens come only from OpenAI's flow and stay local, requests run from the user's runtime, the user gives express consent, there is no charge, and no pooling. Apps must **not** reuse Codex CLI credentials. | **Allowed.** v1: spawn `codex exec` (we hold no token). Later option: register SIWC (our app would hold a locally stored OpenAI-issued token; sanctioned, but it departs from "never hold tokens", so it needs Art's OK). |
| **Gemini** (Google) | Gemini CLI `gemini -p` ([auth](https://geminicli.com/docs/get-started/authentication/)); for consumer plans it was replaced by **Antigravity CLI** `agy -p --output-format stream-json` on 2026-06-18 ([Google blog](https://developers.googleblog.com/en/an-important-update-transitioning-gemini-cli-to-antigravity-cli/)) | [Gemini CLI ToS/privacy](https://geminicli.com/docs/resources/tos-privacy/) and the [FAQ](https://geminicli.com/docs/resources/faq/) say third-party software using Gemini CLI OAuth violates the terms and risks suspension ([discussion](https://github.com/google-gemini/gemini-cli/discussions/22970)); headless docs recommend API keys. A Google AI forum answer ([thread](https://discuss.ai.google.dev/t/is-it-allowed-for-an-open-source-tool-to-call-the-official-agy-cli-in-headless-mode/183429)) says spawning the unmodified `agy` locally is OK if tokens are never touched. That answer came from a community "Regular", not confirmed Google staff, and it recommends AI Studio/Vertex keys for automation. | **Forbidden** for Gemini CLI OAuth: use a **Gemini API key / Vertex only** (already in LangHire). `agy` subscription driving stays **off** until Google confirms officially. |

Common rule for all vendors: one user, on their own machine and their own account. No credential
sharing, pooling, reselling, or proxying, and no reading of CLI token files. Respect plan limits:
the default is 1 concurrent job per CLI backend, with backoff on rate-limit errors.

## 4. Milestones

| # | Milestone | Exit criteria |
|---|---|---|
| **M1** | **Fork + audit + builds** | LangHire history merged; scaffold archived; attribution/license; privacy + license CI green; this audit; rename to ApplyQueue (product name, bundle id `org.applyqueue.desktop`, data dir with migration from `langhire`, sidecar name); telemetry off by default; fix missing `it/zh` translations; CSP tightened; PyMuPDF replaced; app + sidecar build on Linux/Windows/macOS in CI (path-filtered; macOS manual/release only). |
| **M2** | **Answer every question + fix #89** | Remove "skip after 5 steps"; `form.unanswered_fields()` must be empty before submit; every question (optional included) is answered truthfully from profile/QA, EEO may use "decline to self-identify", and unknowns go to `ask_user` instead of being invented. `applied` requires a confirmation page + no failing submit POST (CDP) + an email proof when available; otherwise `submitted_unverified`/`failed`. Generated ATS passwords are stored before use. Fixture ATS pages + tests. |
| **M3** | **Subscription CLI backends** | MCP server (`browser/form/email/queue`), adapters for Grok Build, Cursor CLI, Codex `exec`, Claude Code (opt-in, gray, warning), each with an install/login check and stream-json parsing; Grok Bot setup prompt; Gemini = API key only; API keys + Ollama untouched. |
| **M4** | **Gmail + Outlook confirmation tracking** | Gmail API + Microsoft Graph OAuth (user's own consent, local tokens for *email* only); OTP retrieval tool; confirmation matching + labels (`ApplyQueue/Applied`, `…/Rejected`, `…/Interview`); weekly one-click Google re-auth reminder; recruiter emails off by default, drafts only. |
| **M5** | **Private GitHub sync repo** | Queue/ledger/QA/credentials/sessions sync via the user's own private repo; app verifies `private=true` via the GitHub API and **refuses** public repos (and re-checks before each push); claim locks across machines/agents; encryption as an optional setting (not approved yet). |
| **M6** | **Local models as headline** | Detect Ollama / LM Studio / llama.cpp / vLLM; hardware probe (VRAM/RAM/Apple silicon); vision-model recommender; one-click pull with progress; overnight mode. |
| **M7** | **LoopCV-style searches** | Saved searches with schedules and stats (found/applied/verified/replies), review-first vs automatic mode per search, user-defined job domains (starter packs in `docs/examples/packs/`). |
| **M8** | **Full e2e + break-it suite** | Fixture ATS sites + real-browser e2e on macOS/Windows/Linux, chaos cases (500 on submit, CAPTCHAs, expired sessions, OTP, required/optional fields, crash/resume, duplicate claims, public-repo refusal); `TEST_REPORT.md`. |
| **Gate** | **Publish gate** | Art approves `TEST_REPORT.md`; final privacy scan; only then: visibility change, releases, domains/npm. Nothing is published before this. |

### 4.1 Candidate upstream PRs to LangHire (NOT opened)
Only with Art's OK via Job Manager:
1. #89 fix: verified-submission status (CDP POST check + confirmation-page check).
2. Optional/required-question rule: replace "skip after 5 steps" with a configurable "answer
   every question" mode.
3. `.gitignore` fix so `src/locales/*/jobs.json` is tracked, plus the missing it/zh files.
4. Lockfile refresh (`npm ci` fails upstream).

## 5. M1 status
See the "M1 status" section of the current report in the PR/commit log. The tasks are tracked
here:
- [x] Merge LangHire history; archive Electron scaffold; attribution; LICENSE; docs moved.
- [x] Privacy + license CI adapted; test.yml path filters; macOS manual-only.
- [x] Local Linux build: `npm run build`, pytest, vitest (8 known i18n failures).
- [ ] Sidecar binary + `tauri build` (deb/AppImage) on Linux. In progress on the box.
- [ ] Rename to ApplyQueue (identifier, data-dir migration, sidecar name).
- [ ] CI app builds on 3 OSes (`build-check.yml`, path-filtered, macOS manual).
- [ ] Telemetry off by default; CSP; i18n files; PyMuPDF replacement.

## 6. Guardrails (unchanged)
Private repo only; no GitHub fork; no upstream PRs, publishing, domains, or npm names without
Art's approval; no Cursor cloud agents; no secrets or personal data in the repo. Fixtures use
"Alex Example", `example.com`, and 555-01xx numbers.
