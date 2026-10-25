# DESIGN: ApplyQueue, an open-source, cross-platform job-application agent

Status: Phase 1 design, updated 2026-10-24 with the maintainer's decisions (section 16).
Repo: `github.com/JonathanLiu1401/ApplyQueue` (public). License: **MIT** (`LICENSE`).
Prior art: the maintainer's private prototype (Python), summarized in section 1.
Companion docs: `MILESTONES.md` (v1 plan), `NAME_SHORTLIST.md` (naming research).

---

## 1. Prior art: the private prototype

```
 search lanes (agents + scripts)            apply agents (N in parallel, many machines)
 ─────────────────────────────              ─────────────────────────────────────────────
 public ATS JSON scrapers (Greenhouse,       MCP server (stdio)
   Ashby, Lever, Workday CXS,                ├ next_job   -> SQL pick (term, tier, not banned,
   SmartRecruiters, others)                  │               in-scope country, not in ledger,
 aggregator lists                            │               posting still live) + "card" with
 signed-in job boards                        │               answers + resume path
 ranker: tier + term + policy flags          ├ skip_job   -> terminal status + reason
        │                                    └ finish_job -> requires real confirmation email
        ▼                                                    classified as "proof", then logs + pushes
 SQLite (jobs, applications, answers,        browser: persistent Chrome profiles via CDP
   accounts, claims)                         email: confirmation labeled + archived
        │                                                  │
        └──────────── git (main) ◄────────────────────────┘
          append-only JSONL ledger (union merge, compacted per job key; cross-machine truth)
```

Key properties worth keeping: (1) the **three-tool queue contract** (`next_job`/`skip_job`/
`finish_job`) that makes any agent a worker; (2) **email is the only proof** of submission;
(3) an **append-only, union-mergeable ledger** that tolerates many machines;
(4) deterministic answers from a profile, so the model rarely improvises; (5) explicit
coverage reporting for search lanes; (6) portal logins and browser sessions kept in a
**private** repo so every bot on every machine finds them with zero setup (the app makes this
the default via the user's private sync repo, section 6.1).

Key things to change for OSS: personal data hard-coded throughout; tiers are regexes for
one person's field; SQLite pushed through git (binary, conflict-prone); email labeling
driven by UI clicks; Linux-only shell glue.

## 2. User config schema (local only, never in any repo)

Stored in the OS app-data dir (`app.getPath('userData')`):
macOS `~/Library/Application Support/ApplyQueue/`, Windows `%APPDATA%\ApplyQueue\`,
Linux `~/.config/ApplyQueue/`.

```
userData/
  config/profile.json        # identity, education, workAuth, demographics, links
  config/preferences.json    # categories (tiers), term, locations, bans, limits
  config/answers.json        # answer bank (learned + user-confirmed)
  config/knowledge.json      # projects, experience, essays ingredients
  config/accounts.json       # portal/email account METADATA (no secrets)
  config/queues.json         # saved searches ("queues"), each with its own mode and filters (8.3)
  documents/                 # resumes, transcript (copied in by the user)
  browser/<profile-id>/      # persistent Chromium profile(s) for the agent
  state/                     # local cache of the sync repo (section 6)
  logs/
portal credentials + browser sessions -> private sync repo (section 6.1), mirrored in the local vault (3.3)
```

JSON Schema (abridged; full schema ships as `packages/schema/*.schema.json` and is
validated with Ajv/Zod at load and in the wizard):

```jsonc
// profile.json
{
  "schemaVersion": 1,
  "name": { "first": "", "middle": "", "last": "", "preferred": "", "legal": "" },
  "email": "", "phone": { "e164": "", "country": "US" },
  "address": { "line1": "", "city": "", "region": "", "postal": "", "country": "", "county": "" },
  "links": { "linkedin": "", "github": "", "website": "", "other": [] },
  "education": [ { "school": "", "aliases": [], "degree": "BS", "major": "", "gpa": 0, "gpaScale": 4,
                   "start": "YYYY-MM", "end": "YYYY-MM", "expected": true } ],
  "educationPolicy": { "singleGraduationDate": "last" },   // which date to use when a form has one box
  "workAuth": {
    "citizenships": ["XX"],
    "countries": { "US": { "authorized": true, "basis": "citizen", "sponsorshipNow": false,
                           "sponsorshipFuture": false, "usPerson": false, "clearance": "none" } }
  },
  "demographics": { "gender": "decline", "race": "decline", "hispanic": "decline",
                    "veteran": "decline", "disability": "decline" },
  "references": [ { "name": "", "relation": "", "contact": "", "requireConsent": true } ]
}
// preferences.json
{
  "roleType": "internship",            // internship | new-grad | full-time | contract | any
  "term": { "id": "summer-2027", "label": "Summer 2027", "start": "2027-06-21", "end": "2027-09-24" },
  "categories": [                      // user-defined tiers (replaces rank.py regexes)
    { "id": "data-analyst", "label": "Data analyst", "priority": 1, "apply": true,
      "include": ["data analyst", "business intelligence", "sql"], "exclude": ["senior", "director"],
      "resumeId": "primary" }
  ],
  "locations": { "include": ["US:*", "remote"], "exclude": [], "weights": {"US":1.0} },
  "companies": { "banned": ["Example Corp"], "bannedIndustries": ["tobacco"],
                 "targets": [], "notes": {} },
  "limits": { "dailyCap": 50, "perDomainGapSec": 8, "linkedinEasyApply": { "minGapSec": 60, "maxGapSec": 90, "dailyMax": 30 } },
  "apply": { "autoSubmit": true, "requireEmailProof": true }
}
// answers.json
{ "bank": [ { "id": "", "patterns": ["are you 18"], "answer": "Yes",
              "scope": { "company": null }, "source": "user|learned|derived", "confirmedAt": "" } ],
  "freeText": { "whyCompany": "", "summary": "", "proudest": "" } }
// accounts.json  (metadata only; passwords live in the sync repo credentials/ and the local vault mirror)
{ "email": [ { "id": "primary", "provider": "gmail|outlook|imap", "address": "",
               "confirmationLabel": "Job Applications", "archiveAfterLabel": true } ],
  "portals": [ { "id": "workday:examplecorp", "loginUrl": "", "username": "", "signInWith": "password|google|microsoft",
                 "keychainRef": "applyqueue/portal/workday:examplecorp", "notes": "" } ],
  "resumes": { "items": [ { "id": "primary", "path": "documents/resume.pdf" } ],
               "rules": [ { "match": { "titleRegex": "analyst" }, "resumeId": "analyst" } ], "default": "primary" } }
```

```jsonc
// queues.json  (a queue = a saved search the agents work through)
{ "queues": [ { "id": "q-analyst-remote", "name": "Data analyst, remote",
    "titles": ["data analyst", "business intelligence analyst"],    // required
    "locations": ["remote", "US:*"],                                 // required
    "filters": { "roleType": "full-time", "seniority": ["entry", "junior"], "postedWithinDays": 14,
                 "salaryFloor": null, "include": [], "exclude": ["senior"] },   // optional
    "excludeCompanies": ["Example Corp"],                            // optional
    "boards": ["greenhouse", "lever", "ashby", "linkedin"],          // optional; default = all enabled sources
    "mode": "review-first",                                          // review-first | automatic
    "resumeId": null,                                                // null = use resume routing rules
    "dailyCap": 25, "paused": false } ] }
```

Quick start (8.1) writes a single queue from just titles + locations; everything else falls
back to defaults and the categories/answers in `preferences.json` / `answers.json`.

Derivation layer: answers like "Need sponsorship now or later" or availability dates are
**derived** from `workAuth`/`term` at runtime (no duplicated strings), and the loader
flags answer-bank entries that contradict structured facts (e.g. a company-specific
sponsorship answer that disagrees with `workAuth`). All examples here are fictional.

## 3. Electron architecture

### 3.1 Processes
- **Main process (Node, TypeScript):** config store, keychain, scheduler, sync engine,
  agent-backend adapters, local MCP server, email/portal OAuth, browser launcher,
  auto-updater. Only the main process touches disk, network secrets, or child processes.
- **Renderer (React + Vite):** wizard, dashboard, queue, ledger, architecture view,
  answer-bank editor. `contextIsolation: true`, `nodeIntegration: false`, `sandbox: true`,
  strict CSP; talks to main via a typed `contextBridge` IPC API (`window.api.*`) with
  zod-validated payloads.
- **Worker processes (`utilityProcess`):** discovery scrapers (public ATS JSON), ranker,
  email poller. Crash-isolated, restartable.
- **Agent browser:** a separate Chrome/Chromium (system Chrome preferred, Playwright
  Chromium fallback) launched with a persistent `--user-data-dir` under `userData/browser/`
  and a CDP port bound to 127.0.0.1. Exposed to agents through `chrome-devtools-mcp` or
  Playwright MCP.

### 3.2 Local storage
- Config: JSON files + schema validation; writes are atomic (temp + rename).
- Operational DB: SQLite via `better-sqlite3` (local only, never synced) for jobs cache,
  queue view, claims, FTS on postings.
- Synced state: plain JSONL files in the private sync repo (section 6). SQLite is
  rebuilt from JSONL on any machine, so no binary DB in git.

### 3.3 Secrets and credentials

Decision (maintainer, 2026-10-24): the user's **private sync repo is the default source of truth
for portal credentials and browser sessions** (section 6.1), so every device and agent finds
them with zero setup. The local store is secondary:

- **Local vault = cache/mirror.** Electron `safeStorage` (Keychain on macOS, DPAPI on
  Windows, libsecret/kwallet on Linux) encrypts a per-install vault file that mirrors
  `credentials/` from the sync repo for offline use and fast `fill_secret`. On pull, repo
  wins; on a local change (new account created, password changed) the app writes the repo
  first, then the mirror. If Linux reports the `basic_text` backend, the mirror is skipped
  (the repo copy is still used) rather than writing a second plaintext copy.
- **Local-only mode (opt-in):** the user can choose "keep credentials on this device only";
  then the vault is the source of truth, nothing goes to `credentials/` or `sessions/`, and
  remote workers must be given logins another way.
- **Tokens that stay per device** (never in any repo): the GitHub sync token, email OAuth
  refresh tokens, and AI backend API keys. They live in the local vault; remote bots get their
  own scoped tokens in their own vaults.
- **Where secrets never go:** the **public app repo**, logs, crash reports, analytics, the
  renderer, or model prompts. Agents get credentials only through `fill_secret(field, ref)`,
  which types the value into the page from the app or worker, so the model never sees it.
  Setup prompts reference `credentials/portals.json` by path, never by value.
- Decision (maintainer): no external password manager in v1. 1Password / Bitwarden integration is
  a post-v1 milestone (MILESTONES M13).

## 4. Agent backends (who drives the browser)

Decision (maintainer, 2026-10-24): **support every backend; recommend Grok Bot for now.**
All backends share one contract, so adding one is an adapter, not a fork.

```ts
interface AgentBackend {
  id: "grok-bot" | "chatgpt" | "claude" | "gemini" | "cursor" | "local" | "custom-mcp";
  capabilities(): {
    alwaysOn: boolean;          // keeps running with the user's computer off
    computerControl: "own-cloud-browser" | "client-executed" | "user-browser" | "local-playwright" | "none";
    scheduling: "native" | "app-scheduler";
    connect: "direct" | "setup-prompt";   // see 4.2
  };
  connect(auth: BackendAuth): Promise<void>;            // API key, CLI sign-in, or setup prompt
  startWorker(spec: WorkerSpec): Promise<WorkerHandle>; // gives it the MCP tools + prompt
  stop(h: WorkerHandle): Promise<void>;
}
```

Every backend gets the same contract: the **ApplyQueue MCP server** (next_job, skip_job,
finish_job, get_answer, report_unanswered, fill_secret, save_credential, save_session,
get_email_code, label_confirmation, log_search_coverage, and the browser/form tools; full
interface in 4.5) plus a browser (the backend's own computer/browser control, or the
app's CDP/Playwright MCP) and a generated system prompt (the core rules in section 9). The
backend never sees the DB, git internals, or secrets. When a backend cannot reach the local
MCP server (cloud-only products), it works through the **private sync repo** (section 6)
using a small `applyqueue-worker` CLI/MCP it installs in its own sandbox, or through an
authenticated HTTPS MCP endpoint the user opts into (tunnel; off by default).

### 4.1 Capability matrix (researched October 2026; verify at release time, these products change monthly)

| Backend | Always-on? | Computer control? | Scheduling? | How the app connects | Plan / cost notes |
|---|---|---|---|---|---|
| **Grok Bot** (recommended) | Yes: its own persistent box | Yes: own desktop + browser with persistent logins and vault | Yes: routines | Setup prompt or bot template (section 10); worker talks to the sync repo or app MCP | Grok Bot subscription; proven on this exact workload |
| **ChatGPT Work** (cloud browser) [C1][C2] | Yes: cloud browser keeps working after you leave | Yes: cloud browser on a separate cloud computer; sign-in via a secure form the model cannot see; user can take over; asks before consequential actions | Yes: scheduled tasks, up to hourly on paid plans; event triggers (Gmail, Slack, GitHub) with Work on Plus+ [C3][C4] | Setup prompt (creates the scheduled task with the rules); queue via sync repo; custom MCP only where available (below) | Website sign-in in the cloud browser on Plus/Pro, not Enterprise/Edu; Free/Go have no cloud browser. Active task caps: Free/Go 3, Plus 5, Business/Edu 10, Pro/Enterprise 15 |
| ChatGPT custom MCP (developer mode) [C5][C6] | n/a (adds tools to the above) | n/a | n/a | "Add custom MCP server": public HTTPS endpoint or Secure MCP Tunnel | Write-capable custom MCP is beta for Business/Enterprise/Edu; not a v1 dependency |
| Codex (OpenAI) automations [C7] | Cloud runs yes; local project automations need the desktop app running | Via MCP (browser MCP) | Yes: automations | Direct: local MCP config written by the app | ChatGPT plan with Codex |
| **Claude Code cloud scheduled tasks** (formerly "routines") [A1] | Yes: runs on Anthropic cloud with the machine off | Limited: cloud sandbox, no logged-in user browser; can run headless Playwright | Yes: schedule (1 h minimum), API POST, GitHub events; create at claude.ai/code/routines or `/schedule` | Setup prompt + sync repo (cloud cannot reach the local MCP) | Pro, Max, Team, Enterprise |
| Claude Code CLI / Desktop scheduled tasks [A2][A3] | No: needs the machine on (Desktop tasks need the app open; `/loop` is one session) | Yes: via the app's Playwright/CDP MCP on a persistent signed-in profile | Yes (local) | Direct: the app writes `.mcp.json` / `claude mcp add` | Claude subscription or API key |
| Claude API computer use / browser use [A4][A5][A6] | Only if the app's worker host is always-on | Yes: `computer_toolset_20260801` and `browser_toolset_20260801` (GA); the client (our app) executes actions | App scheduler | Direct: API key in the vault; the app runs the agent loop | Pay per token; not available in Claude Managed Agents |
| Claude in Chrome [A7] | No: user's Chrome must be open | Yes: the user's own Chrome with existing logins; classifier auto-approval | Its own scheduled shortcuts only | Setup prompt (paste rules + queue link) | Paid Claude plans |
| **Gemini API computer use** [G1][G2][G3] | Only if the app's worker host is always-on | Yes: `computer_use` tool, `environment: "browser"`; the client (our app) executes actions with Playwright in a screenshot loop. Gemini 4 also supports mobile/desktop environments. A `safety_decision` of `require_confirmation` is routed to the user | App scheduler | Direct: API key in the vault; the app runs the agent loop (4.5) | Preview; pay per token. Models: Gemini 3.8 Flash, 3.5 Flash-Lite, Gemini 4 (Vertex lists 4 Argon, 3.5-3.8 Flash) |
| **Gemini app tasks + schedules** [G4][G5][G6] | Partly: the remote cloud browser runs unattended; tasks on local Chrome need the computer awake | Yes: local Chrome or a remote cloud browser; sign-in steps pause for user takeover; Google advises not to put sign-in info in the task thread | Yes: time-based or "when an email arrives" triggers; up to 10 active schedules | Setup prompt + sync repo | Google AI Pro/Ultra, personal Google account |
| Chrome auto browse (Gemini in Chrome) [G7] | No: the user's Chrome must be open | Yes: the user's own Chrome with existing logins; asks for confirmation on sensitive steps | Not on its own; pair with Gemini app schedules | Setup prompt (paste rules + queue link) | US, 18+, AI Pro (20 tasks/day) or Ultra (200 tasks/day) |
| Gemini CLI [G8][G9][G10][G11] | No: needs the machine (or a VPS) on | Yes: via the app's Playwright/CDP MCP | App scheduler runs `gemini -p ... --output-format json` headless | Direct: the app writes `mcpServers` into `~/.gemini/settings.json`; headless runs need `mcp.autoAllowInHeadless: true` and a trusted folder (`--skip-trust` / `GEMINI_CLI_TRUST_WORKSPACE`) | Google account or Gemini API key |
| **Cursor** Automations / cloud agents [U1][U2] | Yes: cloud agents | Yes: computer use on by default in cloud agents | Yes: cron or event triggers | Setup prompt + sync repo, or Cursor CLI/IDE agent with local MCP | Cursor plan. **The OSS project's own development must not depend on Cursor cloud agents;** users may still pick Cursor as their backend |
| **Local: Ollama** [L1][L5][L17] | Only if the user's machine/VPS is always-on | Yes: the in-app runner (4.5) with a local vision model (Qwen3-VL, Qwen3.5/3.6, Gemma 4) | App scheduler | Direct: `http://127.0.0.1:11434` (native API + OpenAI-compatible `/v1`) | Free; needs a GPU or Apple silicon for usable speed; best run overnight (4.6) |
| **Local: LM Studio / any OpenAI-compatible server** [L14] | Same as Ollama | Yes: same runner | App scheduler | Direct: `http://127.0.0.1:1234/v1` by default, or any user-entered OpenAI-compatible URL (vLLM, TGI, LocalAI, ...) | Free |
| **Local: llama.cpp `llama-server`** [L15][L11] | Same as Ollama | Yes: same runner; vision via a GGUF + `mmproj` projector (e.g. UI-TARS-1.5-7B) | App scheduler | Direct: OpenAI-compatible `/v1/chat/completions`; current default port 9931 (8080 in older docs and Docker examples) | Free |
| **Local: Hugging Face models** [L4][L10][L16] | Same as Ollama | Yes: GGUF repos run through Ollama (`ollama run hf.co/{user}/{repo}`) or llama-server; full-precision models via transformers / vLLM / TGI behind an OpenAI-compatible URL (or an HF Inference Endpoint) | App scheduler | Direct: one of the endpoints above | Free locally, or HF endpoint billing |
| Custom MCP client (any) | Depends | Via the app's browser MCP | App scheduler | Direct: stdio or authenticated HTTP MCP | Escape hatch for future agents |

Retired, do not target: **ChatGPT agent mode** ("no longer available", replaced by ChatGPT
Work; it had Plus 40 / Pro 400 agent messages per month) and **Operator** (merged into agent
mode, site gone) [C8].

### 4.2 What "log in" means per backend

- **Direct backends** (Claude API, Claude Code CLI/Desktop, Codex local, Cursor CLI, Ollama,
  Hugging Face, custom MCP): the wizard signs in (API key into the vault, or the vendor's own
  CLI login) and the app starts and supervises workers itself.
- **Subscription backends that cannot be driven by a third-party app** (Grok Bot, ChatGPT
  Work, Claude Code cloud scheduled tasks, Claude in Chrome, Cursor Automations): "log in"
  means the app **generates a setup prompt** (and, where the product supports it, an
  installable bundle such as a Grok Bot template) that the user pastes into their bot. The
  prompt contains: the core rules (section 9), the user's non-secret preferences, the worker
  install steps, the private sync repo URL, where portal credentials and sessions live in it
  (6.1) and how to load them through the worker, and the schedule to create. It never
  contains secret values; the only thing the user enters in the bot's own vault is the
  repo-scoped GitHub token (plus the encryption key if option 6.3 is adopted). The app
  then watches `workers/<id>.json` heartbeats in the sync repo to show the backend as
  "connected", and offers "regenerate prompt" when rules or prefs change.
- Vendor cloud browsers (ChatGPT Work, Cursor, Claude cloud) also keep their own session
  store; the worker seeds them from `sessions/` and `credentials/` where the product allows
  scripted sign-in, otherwise the user signs in once through the product's secure form.

### 4.3 Recommendation and where it runs

Two headline paths, shown side by side in onboarding and the README:
- **Grok Bot (recommended):** always-on, nothing to keep running at home.
- **Run 100% locally, no subscription:** the in-app runner plus a local vision model the app
  detects or downloads for you (4.6). Free and private; slower, needs a decent GPU, best run
  overnight.

v1 order after those: Claude Code (local CLI, direct) -> ChatGPT Work (setup prompt) ->
Claude / Gemini / OpenAI API computer use through the in-app runner -> Gemini app tasks,
Cursor, Codex, Claude cloud scheduled tasks (setup prompt). All ship behind the same interface;
order only sets polish and docs priority.

Where it runs (shown in the wizard):
1. Recommended: an **always-on host**: Grok Bot's box, ChatGPT Work's cloud browser,
   Claude/Cursor cloud, or a VPS running the app in worker mode, with the sync repo as
   shared state.
2. Otherwise a **spare computer** running the app in "worker mode" (no UI needed, starts on boot).
3. Otherwise the **user's own computer overnight** (app schedules runs, prevents sleep with
   `powerSaveBlocker`, stops when the user returns).

### 4.4 Sources (accessed 2026-10-24)

- [C1] https://help.openai.com/en/articles/20001280-using-cloud-browser-in-chatgpt
- [C2] https://learn.chatgpt.com/docs/browser
- [C3] https://help.openai.com/en/articles/10291617-scheduled-tasks-in-chatgpt
- [C4] https://learn.chatgpt.com/docs/automations
- [C5] https://help.openai.com/en/articles/12584461
- [C6] https://developers.openai.com/api/docs/guides/custom-mcp-server ; https://developers.openai.com/plugins/deploy/connect-chatgpt
- [C7] https://developers.openai.com/codex/app/automations
- [C8] https://help.openai.com/en/articles/11752874-agent ; release notes https://help.openai.com/en/articles/6825453
- [A1] https://code.claude.com/docs/en/routines
- [A2] https://code.claude.com/docs/en/scheduled-tasks
- [A3] https://code.claude.com/docs/en/desktop-scheduled-tasks
- [A4] https://platform.claude.com/docs/en/agents-and-tools/tool-use/computer-use-tool
- [A5] https://platform.claude.com/docs/en/agents-and-tools/tool-use/browser-use-tool
- [A6] https://claude.com/blog/computer-use-skills-api-files-api
- [A7] https://claude.com/resources/articles/claude-in-chrome-generally-available
- [U1] https://cursor.com/docs/cloud-agent/automations
- [U2] https://cursor.com/changelog/06-18-26
- [G1] https://ai.google.dev/gemini-api/docs/computer-use
- [G2] https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/computer-use
- [G3] https://github.com/google-gemini/computer-use-preview (reference client: Playwright or Browserbase)
- [G4] https://support.google.com/gemini/answer/17094710 (Gemini app tasks)
- [G5] https://support.google.com/gemini/answer/17094507 (task schedules, email triggers)
- [G6] https://support.google.com/gemini/answer/16316416 (scheduled actions limits)
- [G7] https://support.google.com/gemini/answer/16821166 (Chrome auto browse)
- [G8] https://google-gemini.github.io/gemini-cli/docs/get-started/configuration.html
- [G9] https://google-gemini.github.io/gemini-cli/docs/cli/headless.html
- [G10] https://github.com/google-gemini/gemini-cli/pull/27215 (`mcp.autoAllowInHeadless`)
- [G11] https://geminicli.com/docs/cli/trusted-folders/
- [L1] https://ollama.com/library/qwen3-vl
- [L2] https://github.com/benbroks/Qwen3-ComputerUse (Playwright + Ollama loop)
- [L3] https://github.com/browser-use/web-ui
- [L4] https://github.com/bytedance/ui-tars
- [L5] https://ollama.com/library/gemma4
- [L6] https://ollama.com/library/qwen3.6
- [L7] https://ollama.com/library/qwen3.5
- [L8] https://ollama.com/library/gemma3
- [L9] https://www.reddit.com/r/LocalLLaMA/comments/1ubx4rw (community VLM benchmark, 2026-06-21; community data, not vendor claims)
- [L10] https://huggingface.co/ByteDance-Seed/UI-TARS-1.5-7B
- [L11] https://huggingface.co/Mungert/UI-TARS-1.5-7B-GGUF
- [L12] https://github.com/bytedance/UI-TARS-desktop
- [L13] https://ollama.com/ahmadwaqar/smolvlm2-agentic-gui ; https://ollama.com/ahmadwaqar/gui-owl (community uploads, unverified)
- [L14] https://lmstudio.ai/docs/developer/openai-compat
- [L15] https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md
- [L16] https://huggingface.co/docs/hub/ollama
- [L17] https://github.com/ollama/ollama/blob/main/docs/faq.md

### 4.5 Shared tool layer (ApplyQueue MCP server) and the in-app agent runner

Local and cloud backends drive **the same tools**. The app's main process owns them and
exposes them two ways: as an **MCP server** (stdio for local clients such as Claude Code,
Gemini CLI, Codex, Cursor CLI; streamable HTTP on 127.0.0.1 with a per-client bearer token;
remote access only through an opt-in authenticated tunnel or the sync-repo worker), and as
in-process functions for the **in-app runner**. One implementation, so a fix or a new ATS
quirk helps every backend.

| Group | Tools | Notes |
| --- | --- | --- |
| Queue | `next_job(queueId?)` -> job + claim token; `skip_job(token, reason)`; `finish_job(token, outcome, evidence?)` | Claims are locked (local SQLite + sync-repo lock with TTL). `finish_job("submitted")` parks the job as `pending-proof` until a confirmation email matches (core rule 3). |
| Answers | `get_answer(question, fieldType, options?)` -> `{answer, source}` or `unknown`; `report_unanswered(question, context)` | Answer bank, then derived facts, then a truthful composition from profile/knowledge; never invents (section 9). |
| Secrets | `fill_secret(elementRef, ref)`; `save_credential(portal, username, generate?)`; `save_session(portal)` | Values never return to the model. Writes go to the private sync repo after the visibility check (6.2), then the vault mirror. |
| Email | `get_email_code(portal, since)`; `label_confirmation(jobKey)` | Reads OTPs; labels and archives confirmation and verification mail (core rule 4). |
| Search | `log_search_coverage(lane, stats)` | Makes "exhaustive" checkable. |
| Browser | `browser.navigate`, `browser.snapshot` (accessibility tree with stable element refs), `browser.screenshot`, `browser.click(ref or x,y)`, `browser.type`, `browser.select`, `browser.upload(ref, documentId)`, `browser.scroll`, `browser.wait`, `browser.tabs` | Playwright over CDP on the persistent agent profile (3.1). `upload` resolves a resume id to a file path inside the app; the model never handles paths. |
| Forms | `form.fields()` -> labelled fields with types, options, required flags; `form.fill(mapping)` | DOM-first path: lets text-only or small models fill forms without pixel clicking. |
| Outreach | `draft_outreach(jobKey)` | Disabled unless the user enables outreach; creates a draft only (8.6). |

**In-app agent runner** (for API keys and local models; subscription products bring their own
loop and only call the tools):

1. `next_job`, open the posting, `browser.snapshot` + `form.fields()`; add a screenshot only
   when the model is vision-capable and the DOM is not enough (canvas widgets, custom
   dropdowns, CAPTCHAs that need the user).
2. Send the observation and the tool list to the model: OpenAI-compatible chat completions
   with tools (Ollama, LM Studio, llama-server, vLLM, OpenAI), or the vendor's native
   computer-use API (Claude computer/browser toolsets, Gemini `computer_use`) whose actions
   are mapped onto the `browser.*` primitives.
3. Execute the chosen tool, append the result, repeat. Step budget per job, per-domain pacing,
   per-job timeout, checkpoint after each page so a crash resumes instead of re-submitting.
4. Known ATS adapters (Greenhouse, Lever, Ashby, Workday) fill deterministically from
   `form.fields()` + `get_answer`; the model handles only the unusual questions. This is what
   makes small local models practical.
5. Safety gates: final submit requires `mode: automatic` on the queue (otherwise the job waits
   in review), vendor confirmation requests (Gemini `require_confirmation`, Claude/Chrome
   prompts) surface to the user, and the runner stops on CAPTCHAs for a human.

### 4.6 Local models: detection, recommender, one-click download, honest expectations

**Detection** (Settings > AI backends > Local, and onboarding step 3):
- Probe, on 127.0.0.1 only: Ollama `:11434` (`/api/version`, `/api/tags`, `/api/show` for
  model capabilities such as vision) [L17]; LM Studio `:1234/v1/models` [L14]; llama-server
  `:9931` and `:8080` `/v1/models` [L15]; plus any user-entered OpenAI-compatible URL.
- Binaries on PATH and default install locations: `ollama`, `lms`, `llama-server`.
- Hardware: NVIDIA VRAM via `nvidia-smi`, AMD via `rocm-smi`, Apple silicon unified memory via
  total RAM, else Electron `app.getGPUInfo("complete")`; CPU-only is allowed but flagged.

**Recommender** (picks the largest vision model that fits; sizes are download sizes of the
default 4-bit quantizations unless noted; the rule of thumb is model size + 2-4 GB for
context, KV cache, and image tokens <= free VRAM, or about 70% of unified memory on Apple
silicon; heuristic, tuned in M8):

| Free VRAM / unified memory | First pick | Alternatives | Sources |
| --- | --- | --- | --- |
| CPU only or under 6 GB | `qwen3.5:4b` (3.3-4.0 GB) | `qwen3-vl:4b` (3.3 GB), `qwen3-vl:2b` (1.9 GB) | [L7][L1][L9] |
| 8-12 GB | `qwen3-vl:8b` Q4 (6.2 GB) | UI-TARS-1.5-7B GGUF q4_k_m 4.78 GB + mmproj 853 MB via llama-server; `gemma4:e4b` (6.6-9.5 GB) | [L1][L10][L11][L5] |
| 12-16 GB | Qwen3-VL 8B at Q8 (8.1 GB, the community benchmark's pick for this tier) | `gemma4:12b` (7.7-8 GB), `gemma3:12b` | [L9][L5][L8] |
| 24 GB+ | `qwen3.6:27b` (18-19 GB; community benchmark pick at Q4 with thinking off) | `gemma4:26b` MoE A4B (16-19 GB), `qwen3-vl:30b` (20 GB), `gemma4:31b` (19-20 GB) | [L6][L9][L5][L1] |
| 32-48 GB | `qwen3.6:35b` (23-24 GB) | `qwen3-vl:32b` (21 GB), `qwen3.5:35b` (22 GB) | [L6][L1][L7] |

Model notes: run vision steps with thinking off (the community benchmark found thinking hurts
vision accuracy) [L9]; Gemma 4 needs a larger image token budget [L9]; UI-TARS-1.5-7B is a
GUI-agent specialist (OSWorld 27.5, ScreenSpot-Pro 49.6 per its model card) [L10]; community
GUI models such as `ahmadwaqar/smolvlm2-agentic-gui` and `ahmadwaqar/gui-owl` are listed as
experimental, unverified uploads [L13]. Models with 256K context (Qwen3-VL, Qwen3.5) fit long
application pages [L1][L7].

**One-click download:** Ollama `POST /api/pull` with streamed progress; LM Studio `lms get`;
llama-server: download GGUF + `mmproj` from Hugging Face over HTTPS with size/hash checks;
Hugging Face GGUF repos through `ollama pull hf.co/{user}/{repo}` [L16]. If no runtime is
installed, the app links to the official Ollama installer (it does not bundle runtimes).
Downloads show size and disk space first and are resumable.

**Honest expectations** (shown before the user picks Local): slower than cloud agents
(minutes per application rather than seconds per step); needs a decent GPU or Apple silicon
for usable speed; best run overnight with the computer plugged in (`powerSaveBlocker`, quiet
hours); smaller models handle known ATS forms well through the DOM-first path but struggle
with unusual widgets, so those jobs are parked for review instead of guessed. No data leaves
the machine except the applications themselves.

## 5. Durable portal and email linking

Decision (maintainer): **Gmail and Outlook are both v1**; IMAP stays as a fallback.

- **Gmail:** OAuth 2.0 installed-app loopback + PKCE, scopes `gmail.modify` (read, label,
  archive) and `gmail.labels`. Confirmation detection via `users.messages.list` with a query
  (`newer_than:2d (subject:application OR "thank you for applying")`), classify with the
  ported NOT_PROOF/IS_PROOF rules, then `messages.modify` add label, remove `INBOX`
  (= archive). Refresh token in the vault. `gmail.modify` is a restricted scope; public
  verification needs a CASA assessment, so v1 ships as an **unverified app in Testing mode**
  (or the user's own OAuth client, same limits).
  - **Accepted trade-off:** Testing-mode refresh tokens expire after **7 days**, so Gmail
    needs a re-login about weekly [G1]. Production tokens can also die (6 months unused,
    password change, user revoke) [G2], so the same flow handles any `invalid_grant`.
- **Outlook / Microsoft 365:** Microsoft Graph, delegated `Mail.ReadWrite` + `offline_access`
  (+ `User.Read`), multi-tenant + personal accounts app registration; label = move to a
  folder (default "Job Applications") or a category. Same poller/classifier.
- **IMAP fallback** (Fastmail, iCloud, Yahoo): app password, label = folder/keyword.
- **One-click re-auth (all providers):**
  - Store `grantedAt` and `lastRefreshOk` per account (metadata only, no token).
  - At day 6 for Google Testing-mode accounts, and immediately on `invalid_grant` /
    Graph `interaction_required`: OS notification ("Gmail link expires tomorrow, click to
    renew"), tray/dock badge, red dot on the Email node of the architecture view, and a
    banner in the dashboard.
  - The notification and banner have one button that opens the system browser on the
    loopback OAuth URL with `login_hint` pre-filled; on success the new refresh token
    replaces the old one in the vault and the poller resumes. No settings screens.
  - While expired: workers keep applying but `finish_job` parks submissions as
    `pending-proof`; the poller backfills proof after re-auth (looks back to the last
    successful poll), so nothing is lost.
  - Workers on other machines (Grok Bot, VPS) see `email_link: expired` in the sync repo
    status file and surface it in their daily report.
- The agent never opens the mailbox UI; it calls `label_confirmation(job_id)` and the main
  process does it. OTP/verification codes: `get_email_code(sender, since)` reads the code
  and archives that mail.
- **Portals (Workday, iCIMS, Oracle, LinkedIn, Indeed):**
  - Default: credentials and sessions are stored in the user's private sync repo
    (`credentials/`, `sessions/`, `browser-profiles/`, section 6.1) and mirrored in the local
    vault (3.3). 1Password / Bitwarden integration is a later milestone (MILESTONES M13).
  - Generated strong unique password per tenant at account creation, written to
    `credentials/portals.json` and committed; typed by `fill_secret` so the model never sees it.
  - "Sign in with Google/Microsoft" preferred where the portal supports it.
  - Weekly session health check flags expired portal sessions in the dashboard.

Sources: [G1] https://support.google.com/cloud/answer/10311615 ;
[G2] https://developers.google.com/identity/protocols/oauth2

## 6. Private GitHub sync repo (multi-agent state)

Decision (maintainer, confirmed): the state repo is a **private repo created in the user's own
GitHub account through the user's own GitHub login**. The project never hosts user data.
Auth, in order of preference (verify exact permission names when building M10):
1. **GitHub App** published by the project, using its **user-to-server device flow**: the
   app shows a code, the user approves on github.com, the app gets an expiring, refreshable
   user token limited to the App's permissions (repo Administration to create, Contents
   read/write) and to the repos the user selects. Nothing is stored server-side; the App's
   client ID ships in the binary (no client secret needed for device flow).
2. Fallback: **OAuth App device flow** (scope `repo`, broader, non-expiring token).
3. Advanced: user pastes a fine-grained PAT, or picks any git remote / local folder.

The GitHub token itself lives in the local vault. Remote workers (Grok Bot, VPS, cloud
agents) get a **separate** fine-grained token scoped to only the state repo, which the user
enters in that backend's own vault, never in a prompt; with it they read portal credentials
and sessions from the repo (6.1) with no further setup.

On onboarding (optional but recommended for multi-machine/VPS), the app creates
`<user>/applyqueue-state` (**private**), or uses an existing repo.

```
applyqueue-state/
  README.md                         # what this is; MUST stay private (holds portal logins)
  .gitattributes                    # *.jsonl merge=union
  ledger/applications.jsonl         # one event per line: queued|claimed|submitted|skipped|blocked|confirmed
  ledger/email_confirmations.jsonl  # {job_key, subject, received_at, message_id_hash}
  queue/postings/<yyyy-mm>.jsonl    # discovered postings (public data), sharded by month
  queue/ranked.jsonl                # current ranked queue snapshot (regenerated)
  locks/<job_key>.json              # claim: {worker, device, claimed_at, ttl_sec}
  credentials/  sessions/  browser-profiles/   # portal logins + signed-in sessions (6.1)
  workers/<worker_id>.json          # heartbeat, backend, version
  coverage/<date>/<lane>.json       # search-lane coverage counts
  unanswered/questions.jsonl        # questions the bank could not answer
  config-public/categories.json     # optional: non-secret prefs to share across devices
```

Rules: append-only JSONL with union merge (as today); compaction picks one terminal row per
`job_key = ats:company_slug:native_id` with the existing priority (email-confirmed >
blocking > latest). Claims use **create-only lock files**: a worker commits
`locks/<job_key>.json` and pushes; if the push is rejected and the pulled tree already has
the lock from someone else, it backs off. TTL expiry releases dead claims. Before submit, a
worker pulls and re-checks the ledger (today's rule). Requisition-number dedupe across
surfaces is preserved. Ledger files hold no PII beyond company/job/subject lines; no SQLite.
Portal credentials and browser sessions go in their own folders (6.1). Per-device tokens
(GitHub, email OAuth, API keys) are never written to the repo.

### 6.1 Credentials and sessions in the private sync repo (default)

```
<state-repo>/
  credentials/portals.json          # [{id, loginUrl, username, password, signInWith, notes, updatedAt, updatedBy}]
  credentials/README.md             # "private repo only"; how bots read this file
  sessions/<profile-id>/storage.json  # portable Playwright storageState: cookies + localStorage per origin
  browser-profiles/<profile-id>/    # optional full Chromium profile (Linux workers, basic password store)
  browser-profiles/.gitignore       # excludes Cache/, Code Cache/, GPUCache/, Service Worker/CacheStorage, *.log, Singleton*
  .gitattributes                    # credentials/*.json merge=ours-by-updatedAt driver; browser-profiles/** -diff (LFS optional)
```

How the app writes them:
- **Visibility gate first** (6.2). No credential or session file is written or pushed unless
  the repo is confirmed private.
- On account creation or password change, the app updates `credentials/portals.json`
  (one entry per tenant, `updatedAt`/`updatedBy` for last-writer-wins merge), commits
  "credentials: update <id>" and pushes; then refreshes the local vault mirror.
- After a successful portal sign-in, the worker exports `storageState` to
  `sessions/<profile-id>/storage.json` (portable across OSes), and at most once per run
  commits it. Full profiles under `browser-profiles/` are opt-in and meant for Linux hosts
  launched with `--password-store=basic`; on macOS/Windows, Chrome encrypts cookies with an
  OS key, so a copied profile does not carry sessions to another machine, which is why
  `storage.json` is the default.
- A profile is used by one Chrome instance at a time: workers copy
  `browser-profiles/<id>` to a local working dir, and write back under the profile's lock file
  (`locks/profile-<id>.json`, same TTL rules as job claims).

How bots read them (written into every setup prompt, section 4.2):
1. Clone or pull the private state repo with the bot's own repo-scoped token.
2. Install the worker (`applyqueue-worker`), which loads `credentials/portals.json` and
   exposes it only through `fill_secret`; restore `sessions/<id>/storage.json` into the
   bot's browser (or point Chrome at `browser-profiles/<id>` on Linux).
3. When the bot creates an account or refreshes a session, it uses the worker's
   `save_credential` / `save_session` tools, which commit and push. Bots never paste values
   into chat, reports, or prompts.

### 6.2 Keep the sync repo private (enforced)

- Onboarding copy, shown before any credential is saved: **"This repository holds your job
  portal passwords and signed-in sessions. It must stay private. Anyone with access to it
  can sign in as you. Never make it public, never add collaborators you would not give your
  passwords to."**
- Before **every** write or push of `credentials/`, `sessions/`, or `browser-profiles/`, the
  app calls `GET /repos/{owner}/{repo}` and requires `"private": true` (`visibility` is
  `private`). If public or internal, or the check fails: refuse the write, show a blocking
  warning with a "Make private on GitHub" link, and keep credentials in the local vault only
  until it is fixed. The same check runs in `applyqueue-worker` on bots.
- It also warns (not blocks) when the repo has collaborators other than the owner or has
  been forked, and re-checks visibility daily.
- Never use the public app repo, a fork of it, or any public remote as a state repo; the
  app refuses remotes whose owner/name matches the public project.

### 6.3 OPTION, not yet approved by the maintainer: encrypt credential files in the repo

Idea: store `credentials/portals.json.age` (and optionally `sessions/*.age`) encrypted with
a user key, using **age** (X25519 recipients or passphrase/scrypt) or **libsodium**
`crypto_secretbox` with an Argon2id-derived key. The app generates the key at onboarding,
keeps it in the local vault, and the setup prompt tells the user to put the same key in the
bot's own vault (never in the prompt text or the repo). `applyqueue-worker` decrypts in
memory.

| Pros | Cons |
|---|---|
| A repo leak (mis-set visibility, stolen GitHub token, compromised collaborator, GitHub breach) is not full account takeover | One more secret to hand to every bot/device; lose the key and repo credentials are unrecoverable (passwords can be reset per portal) |
| Git history stays safe even if visibility is ever flipped | Plain `git diff`/union merge no longer work on the file; needs app-side merge (decrypt, merge by `updatedAt`, re-encrypt) |
| Allows per-device recipients (age), so one device can be removed by re-encrypting | Bots without the worker cannot read credentials by just opening the file, which loses some of the "zero setup" benefit |
| Cheap to add later; format is open and auditable | Full browser profiles are large; encrypting them on every write is slow, so likely only `credentials/` and `sessions/` |

Status: design only; default remains unencrypted in the private repo per the maintainer's ruling.


## 7. In-app architecture view

A live diagram (React Flow) of the pipeline, also the app's home screen:

```
 Discover ──► Queue ──► Apply ──► Confirm email ──► Log
 (sources,    (ranked,   (agent    (label + archive,  (ledger,
  coverage)    filters)   workers)  proof check)       sync, report)
```

Each node shows counts and health (e.g. "Discover: 312 boards, 41 new today, LinkedIn lane
12/12 pages"), click-through to a plain-language explanation ("Why does a thank-you page not
count?"), the rules in force, and the underlying records. A "What happens to my data" panel
shows exactly which files live locally, in the keychain, and in the sync repo.

## 8. Onboarding and everyday UX (non-technical users)

UX reference: hosted auto-apply services (for example LoopCV) were reviewed for interaction
patterns only; no branding, text, or assets are copied.

### 8.1 Quick start: three steps

1. **Upload your resume.** The chosen AI (or the local model) pre-fills the profile; the user
   confirms each field (green check UI).
2. **Pick job titles and locations.** Everything else is optional.
3. **Press Start.** Creates the first queue in **review-first** mode. The same screen shows
   "Who does the work?" with two first-class choices, **Grok Bot (recommended)** and **Run
   100% locally (free, no subscription)**, plus "other AI" (ChatGPT, Claude, Gemini, Cursor).
   Picking Local runs detection and the recommender (4.6) inline.

### 8.2 Progressive setup (optional, offered when it first matters)

The former 11-step wizard becomes a checklist the app surfaces at the right moment, never a
wall of forms:
- Answer bank review (top 50 common questions pre-filled) when the first unknown question appears.
- Work authorization (plain questions per country -> structured `workAuth`) before the first
  application that asks.
- Voluntary EEO choice per question type (an answer or "Decline to self-identify"; a choice is
  always made).
- Categories and starter packs (any field; every keyword editable), banned companies/industries.
- Resumes and routing (8.5).
- Email: connect Gmail/Outlook (OAuth) or IMAP; choose a label; test with a sample mail. Needed
  for "confirmed" status, so offered before switching a queue to automatic.
- Portals: sign in to LinkedIn/Indeed etc. in the agent browser; passwords and sessions go to
  the private sync repo after the privacy warning and visibility check (6.2), mirrored
  locally; or "this device only".
- AI backend details and where it runs (4.3); dry run (fill but don't submit one posting).
- Sync: "Sign in with GitHub" (device flow), auto-create a **private** state repo, verify
  visibility. Needed for more than one agent and for credential sharing.

### 8.3 Queues (saved searches)

A queue = titles, locations, optional filters (role type, seniority, posted within, salary
floor when posted, include/exclude keywords), excluded companies, and boards (schema in
section 2). Each queue has:
- **Mode toggle:** *review-first* (default; matches wait for the user's approve/skip, in bulk
  or one by one) or *fully automatic* (the agent submits within limits). Automatic requires a
  connected email so submissions can be confirmed.
- **Stats page:** found, applied, skipped (with reasons), confirmed (real email proof),
  interviews, plus response rate and a 30-day trend. Numbers come from the ledger, never
  from agent self-reports.
- Pause/resume, daily cap, and a coverage view (which boards and pages were searched).

Queues map onto the ranking engine: the queue's titles become a category, its locations a
location gate, and its exclusions merge with global bans.

### 8.4 Application tracker

One dashboard across all queues with columns *Applied (pending proof) -> Confirmed ->
Assessment -> Interview -> Offer*, plus *Rejected* and *Needs you* (unanswered questions,
follow-up tasks, sign-in prompts). Cards move automatically from the ledger and the email
classifier (confirmation, assessment invite, interview scheduling, rejection); the user can
drag to correct, which feeds the classifier's tests. Export to CSV.

### 8.5 Multiple resumes

- **Routing:** per category or per queue ("titles containing PCB -> PCB resume"), with a default.
- **A/B analytics:** response rate (assessment, interview) per resume variant, with counts and
  a "not enough data yet" guard instead of over-claiming significance.
- **ATS keyword check:** compare a resume against a posting or a whole queue; show missing
  and matched keywords. It suggests; it never rewrites the resume with claims the user did
  not make (core rule 2).

### 8.6 Recruiter outreach (off by default)

Disabled by default. When enabled, the agent may write a short note to a recruiter or hiring
manager after applying, saved as a **draft** in the user's mailbox or in the app. Nothing is
ever sent automatically; the user reviews and sends each one. Drafts use only facts from the
profile.

## 9. Core rules (product invariants, injected into every agent prompt and enforced by tools)

1. **Answer every question, including optional ones, truthfully and never blank.** Use the
   answer bank or derived facts; if unknown, `get_answer` asks the backend to compose a
   truthful answer from profile/knowledge; if it still cannot, the agent reports via
   `report_unanswered` and skips rather than inventing. EEO questions may use the decline
   option, but an option must be selected.
2. Never invent facts (citizenship, GPA, dates, IDs, employers).
3. A submission counts only after a real confirmation email (`finish_job` enforces it);
   thank-you pages and security-code mails are not proof.
4. Label and archive the confirmation and any OTP/verification/reset mail for that job.
5. Complete follow-up tasks (assessments forms, "complete your profile") or log them.
6. Respect bans, locations, limits, and per-portal notes.
7. CAPTCHAs: solve only as a human would in the visible browser; no solving services or
   token forgery.
8. Search lanes report coverage (pages seen, queries run) so "exhaustive" is checkable.
9. Queues in review-first mode never submit without the user's approval; automatic mode
   respects caps and pacing.
10. Recruiter outreach is off by default and is drafts only; nothing is sent without the
    user's explicit approval.

## 10. Grok Bot users (recommended backend)

Two paths, both generated from config by the app (the same generator powers the ChatGPT
Work, Claude cloud, Claude in Chrome, and Cursor setup prompts in section 4.2):
- **Auto-setup:** app produces a bot template/routine bundle (setup script that installs the
  ApplyQueue MCP server in worker mode on the bot's box, clones the private state repo with a
  fine-grained token the user pastes into the bot's vault, loads portal credentials and
  sessions from it (6.1), and creates routines: N apply workers, daily search lanes, daily
  report).
- **Setup prompt:** a copy-paste prompt (rules from section 9 + MCP install steps + the
  user's non-secret prefs) the user sends to their Grok Bot. Secrets are entered by the user
  in the bot's own vault, never embedded in the prompt.

## 11. Repo layout (public OSS repo)

```
ApplyQueue/            # public: github.com/JonathanLiu1401/ApplyQueue
  LICENSE                  # MIT
  apps/desktop/            # Electron main + preload + renderer (React)
  packages/core/           # queue, ranking, ledger, dedupe, confirm-classify (pure TS, tested)
  packages/schema/         # JSON Schemas + zod types for config + ledger
  packages/mcp-server/     # ApplyQueue MCP server (stdio + optional authenticated HTTP)
  packages/sources/        # Greenhouse, Ashby, Lever, Workday CXS, SmartRecruiters, ... (public data)
  packages/backends/       # grok-bot, chatgpt, claude, gemini, cursor, local, custom-mcp
  packages/runner/         # in-app agent runner (OpenAI-compatible + native computer-use adapters)
  packages/local/          # local runtime detection, hardware probe, model recommender, downloads
  packages/prompts/        # setup-prompt generator shared by subscription backends
  packages/email/          # gmail, graph (outlook), imap, re-auth notifier
  catalog/companies.json   # community-maintained company -> ATS board map (no personal notes)
  templates/packs/         # starter packs: categories + sample answers, fictional data only
  docs/                    # architecture, privacy, backend guides
  .github/workflows/       # ci.yml, release.yml
```

Language choice: port the Python logic to TypeScript (one runtime, easy packaging). Optional
alternative: ship the Python core as a sidecar (PyInstaller) to reuse code faster; not
recommended for v1 because of three-OS packaging and signing overhead.

## 12. CI, packaging, auto-update

- GitHub Actions matrix (macos-14, windows-latest, ubuntu-22.04): lint, typecheck, unit
  tests (vitest), Playwright e2e of the wizard against fixture ATS pages, secret scan
  (gitleaks) and personal-data scan on every PR and push to the **public** app repo. The
  public repo never contains secrets, credentials, sessions, or personal data; those belong
  only in each user's private sync repo.
- `electron-builder` targets: macOS dmg + zip (universal or arm64/x64), Windows NSIS,
  Linux AppImage + deb (+ rpm optional).
- macOS: Developer ID signing + notarization (`notarize` via `@electron/notarize`,
  App Store Connect API key in Actions secrets), hardened runtime.
- Windows: Authenticode signing (Azure Trusted Signing or an OV/EV cert via
  `signtoolOptions`/custom sign hook); unsigned builds get SmartScreen warnings.
- Linux: AppImage (auto-update supported), deb via apt instructions.
- Auto-update: `electron-updater` with GitHub Releases provider (latest.yml/latest-mac.yml),
  staged rollout channel (beta/stable).
- Release flow: tag `v*` -> release workflow builds, signs, notarizes, uploads drafts;
  maintainer publishes.

## 13. License

Decision (maintainer): **MIT**. `LICENSE` at the repo root: "Copyright (c) 2026 JobAutomation OSS
contributors". Contributions are accepted under the same license (inbound = outbound, stated
in CONTRIBUTING.md; no CLA). Third-party dependencies must be MIT-compatible (checked in CI
with a license scanner). Apache-2.0 was considered for its patent grant and not chosen.

## 14. Job-domain generality

Decision (maintainer): **any kind of job.** Nothing about field, seniority, or country is built in.
Categories, filters (include/exclude keywords, locations, seniority, term, salary floor when
posted), banned companies/industries, resume routing, and the answer bank are all
user-defined data (section 2). Ranking = category match x location weight x company
priority x term match. Term detection generalizes to `{season year}`, "new grad YYYY", or
"any".

Starter packs are optional presets in `templates/packs/`, each with categories, sample
filters, and **fictional sample answers** (persona "Alex Example"). "Hardware internships"
is one example pack; it is written from scratch with public keyword lists and public company
catalog entries, never copied from the maintainer's own profile, answers, ban list, notes, or
ledger. A CI check fails if pack files contain strings from a private deny-list kept outside
the repo (names, emails, phone, school) or anything that looks like PII. Company catalog is
public data and community-maintained.

## 15. Migration path from a legacy setup

1. Import existing credential files and browser profiles as-is into the private state repo
   layout (`credentials/portals.json`, `sessions/`, `browser-profiles/`); the legacy repo is
   left unchanged.
2. Convert profile, knowledge, and preference files into the new schema with a one-shot
   importer (`applyqueue import-legacy`, runs locally only).
3. Convert the legacy applied log to `ledger/applications.jsonl` (same keys) so no job is
   re-applied.
4. Run the new app in parallel in dry-run mode before switching workers.

## 16. Decisions

Resolved by the maintainer on 2026-10-24:

| Topic | Decision |
|---|---|
| Repo | `JonathanLiu1401/ApplyQueue` (name picked from `NAME_SHORTLIST.md`), MIT; **private until full e2e testing and a test report the maintainer reviews**, then public (decided 2026-10-24 22:32 PT) |
| License | MIT, holder "JobAutomation OSS contributors" |
| Backends | Support every backend in 4.1; recommend Grok Bot; setup prompt = "login" for subscriptions |
| Sync | Private repo in the user's own GitHub, via GitHub App device flow (OAuth App device flow fallback) |
| Domain | Any job; everything user-defined; hardware internships only as a fictional-sample starter pack |
| Email | Gmail + Outlook (Graph) in v1; 7-day Google Testing-mode re-auth accepted with one-click renew |
| Portal logins + sessions | Default home is the user's private sync repo (`credentials/`, `sessions/`, `browser-profiles/`); local safeStorage vault is a mirror or opt-in local-only mode; app verifies the repo is private before writing; 1Password/Bitwarden later |
| Secrets in public repo | Never; gitleaks + personal-data scan in CI on the public repo |
| UX | Three-step quick start (resume, titles + locations, Start); deeper setup is progressive; queues with per-queue review-first/automatic mode and stats; tracker; multiple resumes with routing, A/B, keyword check (8.1-8.5) |
| Outreach | Off by default; drafts only; never auto-sent (8.6) |
| Local models | Headline "run 100% locally, no subscription" path next to the Grok Bot recommendation; Ollama, LM Studio / OpenAI-compatible, llama-server, Hugging Face; detection + recommender + in-app runner get their own milestone (4.5, 4.6, MILESTONES M8) |

Still open: encrypting repo credentials (6.3, option only); TS port vs Python sidecar (TS recommended); whether sync
also supports non-GitHub remotes in v1 (recommend: local folder + GitHub only).
