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
  PyInstaller sidecar `binaries/applyqueue-backend-<target>`, kills it on exit.
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
  Distributing that binary under MIT terms is a problem. **Replaced in `58108e7`** by
  `pdfminer.six` (MIT, text + layout) and ReportLab (BSD, PDF output) in `backend/core/pdf_io.py`.

### 2.8 Tests (run on the box, Linux x64, 2026-10-24)
- `uv run pytest`: **all pass** (~590 tests across core, memory, resume, sources, API).
- `vitest`: **481 pass / 8 fail**. All 8 are `translations.test.ts` because `src/locales/{it,zh}/jobs.json`
  are missing: LangHire's `.gitignore` ignores `jobs.json` everywhere, so those translations
  were never committed (an upstream bug). Fix in M1 (negated ignore + add files).
- `npm run build` (tsc + vite): passes.
- Backend sidecar (`scripts/build-backend.sh`, PyInstaller): builds (183 MB onefile). It needs a Python
  with a shared `libpython` (uv-managed CPython 3.13 works; Debian's system python did not).
  Smoke test with an isolated `HOME`: `GET /health` → 200 (`database: true`; Chromium auto-install
  starts on first run).
- `npx tauri build --bundles deb,appimage`: passes (`.deb` 194 MB, `.AppImage` 295 MB). Under
  `xvfb-run` the app starts, spawns the sidecar on port 8743, and stays up until killed.

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
| **Claude Code** (Anthropic) | `claude -p "<task>" --mcp-config aq.json --allowedTools "mcp__applyqueue__*" --output-format stream-json --verbose` ([headless](https://code.claude.com/docs/en/headless)); Agent SDK ([docs](https://code.claude.com/docs/en/agent-sdk/)) | [Legal & compliance](https://code.claude.com/docs/en/legal-and-compliance): third-party developers may not offer claude.ai login or route requests through Free/Pro/Max credentials for their users, and may not collect or store tokens. The same page says this does not prevent an end user from signing in to the **unmodified** Claude Code binary with their own subscription. The [Agent SDK + plan](https://support.claude.com/en/articles/15036540-use-the-claude-agent-sdk-with-your-claude-plan) article (updated 2026-10-07) says `claude -p` and third-party apps draw on the plan's limits. See also [sdk-ts#382](https://github.com/anthropics/claude-agent-sdk-typescript/issues/382). | **Allowed as opt-in (Art, 2026-10-25).** API key stays the default. "Use my installed Claude Code" is off until the user enables it after a clear terms warning; it runs the **unmodified** `claude` CLI under the user's own login only. We never bundle it, never offer a "Sign in with Claude" button, and never extract, store, or route its subscription token. |
| **Grok Build** (xAI) | `grok -p "<task>" --output-format streaming-json --always-approve --no-auto-update`; `grok mcp add …`; `grok agent stdio` (ACP) ([CLI reference](https://docs.x.ai/build/cli/reference), [headless](https://docs.x.ai/build/cli/headless-scripting), [launch post](https://x.ai/news/grok-build-cli)) | SuperGrok / X Premium+ via `grok login` / `--device-auth`, or `XAI_API_KEY`. xAI promotes headless/ACP for scripts and "agent orchestration apps". [ToS](https://x.ai/legal/terms-of-service) (2026-10-12) forbids sharing credentials or accounts; the [AUP](https://x.ai/legal/acceptable-use-policy) forbids "using bots to access" the service in the sense of copying or reverse-engineering, circumventing rate limits, and reselling. | **Allowed** via the official CLI/ACP under the user's own login. One user, their own machine, respect rate limits. |
| **Grok Bot** | n/a (the bot runs ApplyQueue itself) | Setup prompt + private sync repo; the bot uses its own sign-in and vault. | **Allowed** (recommended path). |
| **Cursor CLI** | `agent -p "<task>" --output-format stream-json --force --trust --approve-mcps` with `.cursor/mcp.json`; `agent acp` ([headless](https://cursor.com/docs/cli/headless), [parameters](https://cursor.com/docs/cli/reference/parameters), [auth](https://cursor.com/docs/cli/reference/authentication), [MCP](https://cursor.com/docs/mcp)) | `agent login` (the user's account) or `CURSOR_API_KEY`. [ToS](https://cursor.com/terms-of-service) §1.5: no reverse engineering, scraping, or reselling. Cursor staff on the [forum](https://forum.cursor.com/t/inquiry-regarding-official-api-or-commercial-license-for-using-cursors-engine-as-a-backend-component/165063/5) (2026-07-10): headless CLI/SDK as a backend is "supported and intended"; just don't resell or give third parties access under your account. Unofficial proxies to private endpoints are not OK ([thread](https://forum.cursor.com/t/does-using-oh-my-pi-s-cursor-provider-or-an-openai-compatible-proxy-to-the-same-endpoints-violate-cursor-s-tos/167778)). | **Allowed** via the official CLI under the user's own account. |
| **ChatGPT / Codex** (OpenAI) | `codex exec --json "<task>"` with `[mcp_servers.applyqueue]` in `config.toml` ([non-interactive](https://learn.chatgpt.com/docs/non-interactive-mode)); `codex app-server --listen stdio://` ([app-server](https://developers.openai.com/siwc/token-sharing-open-source/codex-app-server)) | `codex exec` under the user's own `codex login` is the official CLI used by its owner. Separately, [Sign in with ChatGPT terms](https://openai.com/policies/sign-in-with-chatgpt-terms/) (2026-09-29) explicitly allow open-source local apps to use a ChatGPT plan through OpenAI's SIWC flow. Conditions: the app keeps its own identity, tokens come only from OpenAI's flow and stay local, requests run from the user's runtime, the user gives express consent, there is no charge, and no pooling. Apps must **not** reuse Codex CLI credentials. | **Allowed, including "Sign in with ChatGPT" (Art, 2026-10-25).** API key stays the default. SIWC tokens come only from OpenAI's flow and stay on the user's device (OS keychain or the Codex CLI's own store); never in the sync repo, the public repo, logs, or telemetry. A guard rejects Codex/ChatGPT tokens in sync-repo writes (see §3.2). |
| **Gemini** (Google) | Gemini CLI `gemini -p` ([auth](https://geminicli.com/docs/get-started/authentication/)); for consumer plans it was replaced by **Antigravity CLI** `agy -p --output-format stream-json` on 2026-06-18 ([Google blog](https://developers.googleblog.com/en/an-important-update-transitioning-gemini-cli-to-antigravity-cli/)) | [Gemini CLI ToS/privacy](https://geminicli.com/docs/resources/tos-privacy/) and the [FAQ](https://geminicli.com/docs/resources/faq/) say third-party software using Gemini CLI OAuth violates the terms and risks suspension ([discussion](https://github.com/google-gemini/gemini-cli/discussions/22970)); headless docs recommend API keys. A Google AI forum answer ([thread](https://discuss.ai.google.dev/t/is-it-allowed-for-an-open-source-tool-to-call-the-official-agy-cli-in-headless-mode/183429)) says spawning the unmodified `agy` locally is OK if tokens are never touched. That answer came from a community "Regular", not confirmed Google staff, and it recommends AI Studio/Vertex keys for automation. | **Forbidden** for Gemini CLI OAuth: use a **Gemini API key / Vertex only** (already in LangHire). `agy` subscription driving stays **off** until Google confirms officially. |

### 3.2 Subscription decisions (Art, 2026-10-25)

1. **Claude Code: allowed as opt-in.** Before it can be enabled, the UI shows a terms warning
   (link to Anthropic's legal & compliance page; the user's own subscription and limits; the
   user is responsible for complying). ApplyQueue runs the unmodified `claude` CLI that the user
   installed and logged in to; it never extracts, stores, proxies, or routes the subscription
   token, and never offers its own Claude login. The Anthropic API key remains the default.
2. **Codex: "Sign in with ChatGPT" allowed.** It uses OpenAI's own SIWC / `codex login` flow.
   The token stays on the user's device only (OS keychain or the Codex CLI's own credential
   store) and is never written to the private sync repo, the public repo, logs, or telemetry.
   ApplyQueue does not reuse Codex CLI credentials in its own HTTP calls. Guard: a test plus a
   scan rule make every sync-repo write reject Codex/ChatGPT/OpenAI OAuth tokens (e.g. `auth.json`
   content, JWT-shaped `access_token`/`refresh_token`/`id_token`, and `sk-` keys). It ships with
   the M3 adapter and is enforced again by the M5 sync writer and the privacy scan. The OpenAI
   API key remains the default.

Common rule for all vendors: one user, on their own machine and their own account. No credential
sharing, pooling, reselling, or proxying, and no reading of CLI token files. Respect plan limits:
the default is 1 concurrent job per CLI backend, with backoff on rate-limit errors.

## 4. Milestones

| # | Milestone | Exit criteria |
|---|---|---|
| **M1** | **Fork + audit + builds** | LangHire history merged; scaffold archived; attribution/license; privacy + license CI green; this audit; rename to ApplyQueue (product name, bundle id `org.applyqueue.desktop`, data dir with migration from `langhire`, sidecar name); telemetry off by default; fix missing `it/zh` translations; CSP tightened; PyMuPDF replaced; app + sidecar build on Linux/Windows/macOS in CI (path-filtered; macOS ARM manual/release only; **Intel macOS on every code push, required** — see §4.2). |
| **M2** | **Answer every question + fix #89** | Remove "skip after 5 steps"; `form.unanswered_fields()` must be empty before submit; every question (optional included) is answered truthfully from profile/QA, EEO may use "decline to self-identify", and unknowns go to `ask_user` instead of being invented. `applied` requires a confirmation page + no failing submit POST (CDP) + an email proof when available; otherwise `submitted_unconfirmed`/`needs_human`/`failed`. Generated ATS passwords are stored before use. Fixture ATS pages + tests. |
| **M3** | **Subscription CLI backends** | MCP server (`browser/form/email/queue`), adapters for Grok Build, Cursor CLI, Codex (`exec` + "Sign in with ChatGPT", token kept in the OS keychain / CLI store only, plus a sync-write guard test rejecting Codex/ChatGPT tokens), Claude Code (opt-in behind a terms warning, unmodified CLI under the user's own login, token never touched), each with an install/login check and stream-json parsing; API keys stay the default for Claude and Codex; Grok Bot setup prompt; Gemini = API key only; API keys + Ollama untouched. |
| **M4** | **Gmail + Outlook confirmation tracking** | Gmail API + Microsoft Graph OAuth (user's own consent, local tokens for *email* only); OTP retrieval tool; confirmation matching + labels (`ApplyQueue/Applied`, `…/Rejected`, `…/Interview`); weekly one-click Google re-auth reminder; recruiter emails off by default, drafts only. |
| **M5** | **Private GitHub sync repo** | Queue/ledger/QA/credentials/sessions sync via the user's own private repo; app verifies `private=true` via the GitHub API and **refuses** public repos (and re-checks before each push); sync writes reject Codex/ChatGPT and other subscription OAuth tokens (§3.2); claim locks across machines/agents; encryption as an optional setting (off by default; Art approved credentials+sessions in the private repo). |
| **M6** | **Local models as headline** | Detect Ollama / LM Studio / llama.cpp / vLLM; hardware probe (VRAM/RAM/Apple silicon); vision-model recommender; one-click pull with progress; overnight mode. |
| **M7** | **LoopCV-style searches** | Saved searches with schedules and stats (found/applied/verified/replies), review-first vs automatic mode per search, user-defined job domains (starter packs in `docs/examples/packs/`). |
| **M8** | **Full e2e + break-it suite** | Fixture ATS sites + real-browser e2e on **macOS Apple silicon, macOS Intel (x86_64, `macos-15-intel`), Windows x64 and Linux x64**, run against the *packaged* app of each platform, chaos cases (500 on submit, CAPTCHAs, expired sessions, OTP, required/optional fields, crash/resume, duplicate claims, public-repo refusal); `TEST_REPORT.md` (plan in §4.3). |
| **Gate** | **Publish gate** | Art approves `TEST_REPORT.md`; final privacy scan; only then: visibility change, releases, domains/npm. Nothing is published before this. |

### 4.2 Platform support and required checks (Art, 2026-10-25)
**Intel Macs (x86_64) are a hard requirement**, equal to Apple silicon, Windows and Linux.
- Dependencies: every Python wheel in `uv.lock` must exist for `macosx_*_x86_64` (or universal2).
  `scripts/check-macos-x64-wheels.py` enforces it on Linux CI and on the Intel job. Known pin:
  `cryptography>=48.0.1,<49` (49+ dropped macOS x86_64 wheels) via `[tool.uv] constraint-dependencies`.
  PyMuPDF (whose `onnxruntime` dependency had no Intel wheel) is gone.
- Sidecar: PyInstaller cannot cross-compile, so the x86_64 sidecar is built natively on
  `macos-15-intel` (`build-backend.sh` refuses a non-native Darwin target and prints `lipo` archs).
  The release workflow builds the Intel bundle on `macos-15-intel` too.
- CI job **"Intel Mac (test, bundle, launch /health)"** (`test.yml`, every code-path push/PR):
  wheel check → `uv sync` with an x86_64 assert → pytest → x86_64 sidecar → frozen
  `--self-test` (PDF libs + arch) → `tauri build --target x86_64-apple-darwin` (.app + .dmg) →
  `scripts/smoke-macos-app.sh` (every Mach-O is x86_64, the packaged app launches and
  `127.0.0.1:8743/health` answers) → unsigned .dmg artifact kept 3 days.
- Required check: the **"Required checks"** aggregator job needs `test` (Linux/Windows) and
  `intel-mac`, and branch protection on `main` requires that one context (no review requirement,
  admins not enforced so fast-forward pushes from the owner still work, force-push and deletion
  disabled). Docs-only pushes skip `test.yml` via path filters, so they are not blocked.
- Cost: macOS minutes are 10x; the Intel job is the only per-push macOS job (concurrency cancels
  superseded runs). Apple-silicon macOS stays on manual dispatch (`run_macos=true`) and releases.

### 4.3 TEST_REPORT.md plan (M8)
One section per platform: macOS Apple silicon, **macOS Intel**, Windows x64, Linux x64. Each lists
the packaged artifact tested (name, version, arch from `lipo`/`file`), install + first launch +
`/health`, data-folder migration from `langhire`, the fixture-ATS e2e results (answer-every-question
including optional/EEO, needs-human path, #89 status transitions, per-site passwords), the
break-it cases, and links to the CI runs and artifacts. Intel results must come from an x86_64
runner or machine, never from Rosetta on Apple silicon.

### 4.1 Candidate upstream PRs to LangHire (NOT opened)
Only with Art's OK via Job Manager:
1. #89 fix: verified-submission status (CDP POST check + confirmation-page check).
2. Optional/required-question rule: replace "skip after 5 steps" with a configurable "answer
   every question" mode.
3. `.gitignore` fix so `src/locales/*/jobs.json` is tracked, plus the missing it/zh files.
4. Lockfile refresh (`npm ci` fails upstream).

## 5. M1 status
M1 checklist (updated with each M1 commit):
- [x] Merge LangHire history; archive Electron scaffold; attribution; LICENSE; docs moved.
- [x] Privacy + license CI adapted; test.yml path filters; macOS manual-only.
- [x] Local Linux build: `npm run build`, pytest, vitest (8 known i18n failures).
- [x] Sidecar binary + `tauri build` (deb/AppImage) on Linux; app + sidecar smoke-run under xvfb.
- [x] Rename to ApplyQueue (identifier, data-dir migration, sidecar name) — done in M2.
- [x] CI app builds (`build-check.yml`): Linux + Windows on path-filtered push/PR, macOS ARM64 on
  manual dispatch.
- [x] i18n: `src/locales/*/jobs.json` un-ignored; it/zh `jobs.json` added (vitest parity passes).
- [x] `uv.lock` committed (CI `--frozen`); Dependabot monthly + grouped (it came with upstream).
- [x] CI green: Linux + Windows on push; macOS ARM64 build + tests via dispatch.
- [x] macOS x64 unblocked (`58108e7`): PyMuPDF replaced, cryptography pinned <49, Intel job required (§4.2).
- [x] Rolled to M2 and done there: telemetry off by default; CSP; PyMuPDF replacement; rename (above).

### 5.1 M2 status
- [x] PyMuPDF → pdfminer.six + ReportLab; Intel macOS build/test/bundle/launch in CI (§4.2).
- [x] Rename to ApplyQueue (`org.applyqueue.desktop`, `applyqueue-backend`, data folder migrated
  from `langhire` on first start).
- [x] Telemetry off by default (opt-in in Settings); stricter CSP; feedback form needs consent.
- [x] Answer every question: upstream's "skip after 5 steps" rule removed; `form_guard` scans every
  field (optional included), fills from profile/QA with retry strategies (direct → search → typing),
  EEO falls back to the decline option; `submit_form_guarded` refuses Next/Submit while anything is
  blank; impossible fields go to the ledger as `needs_human`. Mock-form Playwright test.
- [x] #89: statuses `submitted_unconfirmed` (page confirmation read by our code) / `confirmed`
  (page + email; email hook `/jobs/confirm-email` for M4) / `needs_human` / `failed`; agent
  self-report alone never counts; legacy `applied` rows migrated.
- [x] Hard-coded `JobApp2026!` removed: per-site generated passwords in a local 0600 vault
  (`<data>/vault/site_credentials.json`); the old shared password is only a sign-in fallback.
  Sync repo storage comes in M5.

### 5.2 M3 status (subscription CLI backends)
- [x] MCP server (`applyqueue-backend --mcp-server`, stdio JSON-RPC, schema-validated): `next_job`,
  `skip_job`, `finish_job`, `get_candidate_context`, `browser_*` (navigate/snapshot/click/type/
  select/press/upload_resume/wait/tabs/screenshot/network_errors), `form_scan/autofill/fill/
  answer_for/submit`, `report_needs_human`, `fill_secret` (the CLI never sees the password),
  `email_get_otp`, `email_find_confirmation`. `finish_job` re-checks the page itself (#89 rules).
- [x] Adapters (`backend/core/cli_backends/`): Grok Build (ACP over stdio; `grok models` as the
  login check), Cursor (`agent -p --output-format stream-json`, workspace `.cursor/mcp.json` +
  deny Shell/Write), Codex (`codex exec --json`, MCP via `-c` overrides; ChatGPT sign-in allowed,
  token stays in Codex's own store), Claude Code (opt-in after a terms acknowledgement, unmodified
  CLI, user's own login, `--strict-mcp-config`), Gemini = API key only (not runnable as a CLI).
  We run the vendor's binary and never read, copy, or proxy its token files.
- [x] Detection: install path / custom path, version, login state; fixed-shape outcomes
  (`ok`, `auth_needed`, `rate_limited`, `crash`, `timeout`, `cancelled`, …); stop-the-queue on
  auth/rate-limit; process-tree kill on timeout/cancel.
- [x] `token_guard`: sync-repo writes reject Codex/ChatGPT/Claude/Google/Microsoft token shapes and
  token files (`auth.json`, `email_tokens.json`, …).
- [x] Settings → "Agent backend" cards (install/login state, login command, path override, Claude
  terms dialog, Grok Bot setup prompt with no secrets). `/apply/start` routes to the selected CLI.
- [x] Tests: fake CLI binaries per vendor (stream output, auth-needed, crash, timeout, ACP), MCP
  protocol tests, API tests, token-guard tests, UI tests.

### 5.3 M4 status (Gmail + Outlook)
- [x] OAuth with the user's own client (Google "Desktop app" client + secret; Microsoft public
  client, PKCE, no secret), loopback redirect, one-time state, 15-min TTL. Tokens for email only, in
  `<data>/vault/email_accounts.json` (0600). Google Testing-mode apps: "Reconnect" prompt from
  day 6 and on `invalid_grant`/401.
- [x] Gmail API / Graph: confirmation detection → `confirm_by_email` (needs page evidence first)
  → label/category (`ApplyQueue/Applied`, configurable) + archive; verification/security-code mail
  for an application we are running → `ApplyQueue/Verification` + archive. Only mail matched to a
  job we applied to is modified; bodies are not stored. Background sweep every N minutes.
- [x] OTP via API: `get_email_code` (browser-use) / `email_get_otp` (MCP) replace mail.google.com
  browsing when a mailbox is linked; used codes are not handed out twice in a run.
- [x] Mock OAuth/Gmail/Graph server (`tests/fixtures/mock_mail_server.py`) + API/UI tests.
- [ ] Rejection/interview labels and recruiter drafts: deferred (not in this milestone's brief).
- Setup notes: `docs/applyqueue/EMAIL_SETUP.md`.

### 5.4 M5 status (private GitHub sync repo)
- [x] Sign-in with the user's own GitHub OAuth App (device flow; client ID user-supplied, no
  ApplyQueue-registered app) or a fine-grained PAT; token in `<data>/vault/github_token.json`
  (0600, never in the repo, `.git/config` or argv: passed as an `http.extraheader` env value).
- [x] Repo created private (`POST /user/repos private=true`) or reused; `private==true` and
  `visibility=="private"` verified via the API **before every push and every credential/session
  write**; public/internal repos and the app's own repos are refused (sync pauses, logins stay local).
- [x] Layout: `queue/postings/YYYY-MM.jsonl`, `ledger/events-YYYY-MM.jsonl` (append-only,
  `merge=union`, dedupe on id), `locks/<job_key>.json` (TTL, stale-lock takeover),
  `credentials/portals.json` (LWW per entry), `sessions/<id>.json` (sanitized: AI/email/GitHub
  origins dropped), `workers/<device>.json` heartbeats. Optional AES-256-GCM for credentials and
  sessions (off by default; key export/import per device).
- [x] Offline: commits queue locally and push on reconnect; non-fast-forward → fetch/merge/retry;
  conflicts resolved per file type. `claim_job` takes a remote lock, so two devices/agents never
  apply to the same posting; losers back off 15 min.
- [x] Token guard on every sync write (vendor/AI/email/Codex/ChatGPT tokens refused; portal
  JWTs allowed only inside session files). Plain `git` remotes never get credentials.
- [x] MCP `save_session`; synced cookies restored on browser launch.
- [x] Tests: local bare remote + mocked GitHub API (`tests/fixtures/mock_github.py`).
- Setup notes: `docs/applyqueue/SYNC_SETUP.md`.

## 6. Guardrails (unchanged)
Private repo only; no GitHub fork; no upstream PRs, publishing, domains, or npm names without
Art's approval; no Cursor cloud agents; no secrets or personal data in the repo. Fixtures use
"Alex Example", `example.com`, and 555-01xx numbers.
