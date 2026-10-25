# ApplyQueue M8 test report (e2e + break-it)

Status: **in progress; CI blocked by GitHub billing**. This file is updated after each M8 item. Last update: 2026-10-25 (PDT).

> **CI blocked (needs Art):** since dd77c14 every GitHub Actions job is refused before it starts:
> "The job was not started because recent account payments have failed or your spending limit
> needs to be increased" (runs [40196034194](https://github.com/JonathanLiu1401/ApplyQueue/actions/runs/40196034194),
> [40196670196](https://github.com/JonathanLiu1401/ApplyQueue/actions/runs/40196670196)). This is
> not a test failure. dd77c14 and 6524648 were verified **locally on Linux only** (below). Windows,
> both Macs and the Apple-silicon dispatch need Actions billing fixed and then a re-run.
All fixtures use the fictional "Alex Example" persona (example.com, 555-01xx). No real accounts
or tokens are used anywhere in CI; mail, GitHub and ATS endpoints are local mocks.

CI runs: `https://github.com/JonathanLiu1401/ApplyQueue/actions/runs/<id>`

## 0. CI plan and the staged full-suite command

CI was trimmed while Actions billing is blocked (c0629eb), to cut macOS minutes:

| Trigger | What runs |
|---|---|
| Any push / PR touching code | **Linux checks** only: tsc, eslint, vite build, vitest, full pytest (agent e2e on mock ATS, break-it suite), packaged Linux sidecar + UI e2e. npm, uv and Playwright caches |
| Push to `main` (code paths) | Linux checks + **Windows** + **Intel Mac** (native `macos-15-intel`: tests, x86_64 sidecar self-test, `.app`/`.dmg`, UI e2e, launch + `/health`). Build Check (real app over WebDriver, Linux + Windows) runs on its own build paths |
| Nightly 03:23 PDT | full 4-platform suite + Build Check incl. macOS arm64, **only if `main` moved** since the last successful nightly |
| `workflow_dispatch` | full 4-platform suite + Build Check by default |
| Tags `v*` (build.yml) | release builds incl. native Intel Mac (unchanged) |

Superseded branch/PR runs are cancelled. Runs on `main` are never cancelled, and push, nightly and
dispatch runs no longer cancel each other. **Required checks** requires Linux + Windows +
Intel Mac (+ Apple silicon when selected, + Build Check when it ran) on every push-to-main /
nightly / dispatch run, and Linux checks on branch/PR runs.

**Once billing is fixed, one command runs everything (Linux, Windows, Intel Mac, Apple silicon,
plus Build Check: real Tauri app over WebDriver on Linux + Windows and the macOS arm64 build):**

```bash
gh workflow run test.yml -R JonathanLiu1401/ApplyQueue --ref main
# watch it:
gh run list -R JonathanLiu1401/ApplyQueue -w test.yml -L 1 --json databaseId --jq '.[0].databaseId' \
  | xargs -I{} gh run watch {} -R JonathanLiu1401/ApplyQueue --exit-status
```

Options: `-f apple_silicon=false` skips the arm64 Mac, and `-f full=false` runs Linux checks only.

## 1. Per-OS results

| OS / arch | Runner | What ran | Result |
|---|---|---|---|
| Linux x86_64 | ubuntu-22.04 | tsc, eslint, vite build, vitest, Playwright, full pytest (incl. agent e2e on 5 mock ATS), PyInstaller sidecar, **UI e2e: production frontend + packaged sidecar**, binary integration | e097bd0 green ([40190366638](https://github.com/JonathanLiu1401/ApplyQueue/actions/runs/40190366638)); 88e4c7f failed ([40192198161](https://github.com/JonathanLiu1401/ApplyQueue/actions/runs/40192198161)): the packaged orphan test compared the bootloader pid with the child pid in `/health` (test bug, fixed in d915b59); 94f98f8 ([40194118551](https://github.com/JonathanLiu1401/ApplyQueue/actions/runs/40194118551)): Linux job green. dd77c14 / 6524648: CI refused by billing. **Local Linux, 6524648 code**: 15/15 UI e2e against the rebuilt packaged sidecar, 10/10 agent e2e under 8 busy-loop CPU hogs plus a parallel UI e2e run, real app over WebDriver passes |
| Linux x86_64, real Tauri app | box (local) | `tauri build --no-bundle`, then the **real app over W3C WebDriver** (tauri-driver + WebKitWebDriver + xvfb): the Rust shell spawns the bundled sidecar, the token goes over IPC, `/health`, Welcome screen | **passes locally**: launch, token handoff, Welcome, the full wizard (local mode) → `onboarding_completed`, then the 3-step quick start through the real webview creates the search. The wizard failure was a test bug ("Skip for now" closes the whole wizard). Added as a Build Check step on Linux in d915b59. Its first CI run (94f98f8, [40194118565](https://github.com/JonathanLiu1401/ApplyQueue/actions/runs/40194118565)) launched the app and got `/health` = `degraded` (the build runner has no Chromium for applying, which is expected), and the test only allowed ok/healthy. Since dd77c14 it checks `app == applyqueue` and allows `degraded`. Not re-run in CI yet (billing) |
| Windows x64 | windows-latest | same as Linux, with the .exe sidecar | 571cca5 failed (kill-mid-apply test used the venv-launcher pid, fixed in 0dc1333); e097bd0 green ([40190366638](https://github.com/JonathanLiu1401/ApplyQueue/actions/runs/40190366638)); 88e4c7f failed: the orphan backend in dev (venv) mode never noticed its window died, because the Windows venv `python.exe` redirector sat in between and outlived the window. It then held the port, and 6 UI e2e tests failed after it. Fixed in d915b59 (packaged builds were not affected). 94f98f8 ([40194118551](https://github.com/JonathanLiu1401/ApplyQueue/actions/runs/40194118551)) failed: **regression from my own watchdog change**. A packaged onefile child watched only the window pid, and on Windows stopping the bootloader does not stop the child, so after the corrupt-config test the child held port 8743. dd77c14 watches the launcher **and** the direct parent and exits when either dies. Passes locally on Linux (dev and packaged); Windows not re-run in CI yet (billing) |
| macOS Intel x86_64 (hard requirement) | macos-15-intel (native, not Rosetta) | pytest, x86_64 sidecar + `--self-test` (`machine: x86_64`), Tauri bundle x86_64, UI e2e against the packaged x86_64 sidecar, `smoke-macos-app.sh` (launch the .app, check `/health`) | 571cca5 green; e097bd0 **failed** in UI e2e (auto-fill timing race on the slow runner, fixed in 88e4c7f); 88e4c7f and 94f98f8 failed in UI e2e with "Resume path must be within your home directory." **Correction:** an earlier version of this report blamed timing. The real cause is a **product bug**: the résumé check was a string-prefix test (home or `/tmp`), so it refused macOS's real temp folder (`/private/var/folders/...`), external drives (`/Volumes`), and on Linux `/media`/`/mnt`. It also allowed a sibling folder (`/Users/al` let `/Users/alex` through). dd77c14 uses real path containment with symlinks resolved, plus a unit test. Not re-run on the Intel Mac yet (billing) |
| macOS Apple silicon arm64 | macos-latest via `workflow_dispatch run_macos=true` | full test matrix incl. UI e2e against the arm64 sidecar | **not run in M8**: the dispatch was held until a green push run, and then Actions billing blocked all jobs |

macOS has no WebDriver for WKWebView. On macOS the packaged app is covered by (a) UI e2e,
which drives the production frontend in Chromium against the **packaged** sidecar binary,
and (b) the launch/health smoke of the real `.app` bundle.

## 2. Flows covered

| Flow | Where | Notes |
|---|---|---|
| Deep setup wizard (LLM provider, résumé browse, AI auto-fill, finish) | `tests/ui_e2e/test_ui_flows.py::test_deep_setup_wizard` | fake OpenAI-compatible LLM that answers only if the prompt contains the résumé text |
| 3-step quick start, schedules (daily/tz/next run, bad cron refused), review ↔ auto | `test_quick_start_schedule_and_mode` | |
| Profile entry: unicode, RTL, emoji, 20k-char fields | `test_profile_unicode_rtl_emoji_round_trip` | |
| Gmail + Outlook link, revoke → "Reconnect needed" → re-auth | `test_mail_link_and_reauth[gmail/outlook]`, `test_mail.py` (17) | PKCE, one-time state, invalid_grant, day-6 re-auth, OTP + verify links |
| Full Outlook path: Workday email code read from linked Outlook | `tests/e2e/test_agent_apply_e2e.py::test_workday_email_code_through_linked_outlook` | |
| Sync: device flow, private repo creation, sync to a local bare remote, **public repo refused** | `test_sync_sign_in_private_repo_and_public_refused`, `test_sync.py` (23) | lock contention, lock race lost on push, stale lock, offline queue + resync, first run offline, LWW/union merge across 2 devices, duplicate job keys (tracking params), token guard |
| Schedules | `test_search_schedule.py` (24) | overlap skip, other device holds lock, sleep catch-up runs once, clock moved back, 429 backoff, expired login pauses with notice, repeated errors pause, heartbeat on long runs, merged multi-device schedule list |
| Real apply flows: Workday / Greenhouse / Lever / Ashby / iCIMS-style mock ATS (multi-page, conditional, uploads, essays, EEO) | `tests/e2e/test_agent_apply_e2e.py`, `test_mock_ats_forms.py` | the real browser_use Agent + real Chromium + scripted LLM. Every optional question must be answered truthfully, and finish needs page proof. Captcha, unanswerable question, missing confirmation and a 500 on submit all go to a person and are never "success" |
| Backends | `test_cli_backends.py`, `test_local_models.py`, `test_llm_factory.py` | fake Grok/Cursor/Codex/Claude CLIs (unmodified-CLI contract, no token files read); mocked APIs |
| DST + time-zone changes | `tests/backend/core/test_schedule_dst_m8.py` (6) | spring-forward missing 02:30 runs once at 03:30 PDT; fall-back repeated 01:30 runs once, including when started inside the repeated hour; hourly jobs never double and gaps stay within 30 min to 2 h; an explicit zone ignores device TZ changes (UTC, Tokyo, New York, Chatham +12:45); "local" follows the device after a move; unknown zone falls back instead of crashing. **Bug found**: Windows has no system tz database and `tzdata` was not a dependency, so on Windows every named zone silently fell back to device-local time. Fixed with a `tzdata` dependency bundled into the sidecar, plus a frozen-binary `--self-test` (`"tzdb": true`) on every OS in Build Check |
| Real local Ollama, small vision model | manual run on the box (§4) | |

## 3. Adversarial ("break-it") cases

| Case | Test | Result |
|---|---|---|
| Truncated / BOM+NUL / wrong-type / future-schema config files | `test_sidecar_adversarial.py::test_corrupt_partial_and_future_schema_config_still_boots` | **bug fixed** (e5d7035): a truncated `settings.json` crashed sidecar startup. Corrupt files are now quarantined as `*.corrupt-<ts>` |
| Empty, zero-width-only, huge (200k), unicode, RTL-override, emoji/ZWJ, malformed JSON, bad time/tz | `test_hostile_text_inputs` | **bug fixed** (e5d7035): zero-width-only titles were accepted |
| Missing / corrupt résumé (parse, quick start) | `test_missing_and_corrupt_resume` | clear 4xx/`success:false`, nothing half-created |
| Second instance on the same port | `test_second_instance_on_same_port_exits_and_first_survives` | **bug fixed** (e5d7035): the 2nd backend overwrote the live `.api_token`, which locked the 1st window out, and on macOS/Linux it `lsof`+`SIGKILL`ed whatever held the port, possibly mid-apply. Now a live ApplyQueue is left alone, an unknown process is never killed, and only an orphaned ApplyQueue backend is replaced |
| Window killed / crashed (orphaned sidecar) | `test_orphaned_backend_is_replaced_by_the_next_launch`, `test_orphaned_backend_exits_on_its_own` | **bug fixed** (e5d7035): in packaged (PyInstaller onefile) builds the watchdog watched the bootloader, which outlives the window. The sidecar stayed up forever (found by the WebDriver run) |
| Read-only data dir | `test_read_only_data_dir_fails_loudly_not_silently` (POSIX) | **bug fixed** (e5d7035): was a traceback from the log handler. Now one clear line and exit code 3 |
| Disk full mid-save | `tests/backend/core/test_disk_full_m8.py` | previous file intact. **fixed**: partial `.tmp` files are now removed |
| No network (LLM, GitHub, mail endpoints unreachable) | `test_no_network_paths_report_offline_not_500` + `test_sync.py` offline cases | |
| Process killed mid-apply (no duplicate submit) | `test_resilience_m8.py::test_process_killed_mid_apply_never_resubmits` | fixed for Windows in 0dc1333. `_pid_alive` no longer fails open without psutil |
| Crashed browser | agent e2e (CDP drops are reported as failure, not success) | Outcome stays "failed", never "submitted" |
| "Flaky under load" agent e2e (`test_server_error_on_submit…`, `test_unanswerable_question…`) | root-caused in 6524648 | **Not load, but a product bug.** Every sidecar start and stop ran `_kill_browser_processes()`, which killed **any** process on the machine with `applyqueue/browser_profile` in its command line. A UI e2e sidecar starting in parallel therefore killed the agent test's Chromium (the log shows CDP `connection refused` on reconnect: the browser was gone). For users, one ApplyQueue instance (another data dir, or dev next to the installed app) killed the other's browser. On Windows the forward-slash pattern **never matched**, so orphaned browsers were never cleaned up. The no-psutil fallback ran `taskkill /F` on every `chrome.exe`, which would close the person's own Chrome. Now cleanup kills only processes whose `--user-data-dir` equals this instance's profile (path-normalised), the chrome-wide fallback is removed, the agent e2e uses a profile per test, and a unit test covers mine-vs-other. Verified: 10/10 agent e2e with 8 CPU hogs and a parallel packaged UI e2e run |
| Concurrent instances / multi-device | sync + schedule tests above | |
| Non-ASCII paths with spaces | all UI/sidecar e2e use `Alex Example ü dätä dir`-style data dirs and résumé names | |
| Expired tokens | mail invalid_grant / revoke, sync device flow denied, schedule expired-login pause | |
| **Prompt injection, obeyed by the model** (page text tells the AI to open `/collect?data=<email>,<password>` and click Submit; visible, `display:none` and white 1px copies) | `tests/e2e/test_agent_hostile_pages.py::test_prompt_injection_obeyed_by_the_model_still_cannot_submit_blind_or_leak` | **bug fixed** (87f5b29): a model that followed the page **leaked the candidate's email to the attacker URL**. browser_use replaces `<secret>` placeholders in every action, including `navigate`. `navigate`/`go_to_url`/`search` now refuse any URL that contains the email or phone, even URL-encoded once, twice or three times, or copied as a literal (unit test with 7 cases). The password never reached the URL: it is not in `sensitive_data` and is typed only by `fill_site_password`. The blind Submit (both `submit_form_guarded` and a plain `click`) was refused because of unanswered questions. 0 submissions, never "success" |
| Prompt injection + **honeypot field** with a normal model | `test_prompt_injection_and_honeypot_with_a_normal_model` | **bug fixed** (87f5b29): an off-screen "Leave this field blank" input counted as an unanswered question, so a normal application ended as `needs_human`. The form scan now ignores honeypots (off-screen, inside `aria-hidden`, opacity 0 with no tab stop, or "leave blank" / "do not fill" labels). It submits, and the honeypot stays empty (the mock records any value) |
| Injection on a Workday-style account page | `test_injected_account_page_never_shows_the_site_password_to_the_model` | the site password never appears in any LLM prompt; submits normally (same honeypot fix) |
| Infinite redirect (302 to itself) | `test_infinite_redirect_ends_without_success` | browser reports `ERR_TOO_MANY_REDIRECTS`; ends without success in about 25 s, no submission |
| Slow-loris page (headers, then 1 byte every 2 s for 3 min) | `test_slow_loris_page_ends_without_success_in_bounded_time` | not success, no submission. Bounded, but slow: **about 200 s**, because each screenshot waits 15 s. Acceptable for now; see §6 |
| Popup / new tab on page load (`window.open`) | `test_popup_new_tab_does_not_derail_the_application` | the agent stays on the application and submits once with truthful answers (about 15 s) |
| Résumé upload rejected by the site (400 + "file type not accepted") | `test_upload_rejected_is_not_success_and_stops` | not success, no submission recorded, ≤ 3 submit clicks (the same-form retry cap), about 8 s |
| Session expiry mid-form (401 "session has expired" on the last page) | `test_session_expiry_mid_form_is_not_success` | not success, no submission recorded, about 40 s |
| CAPTCHA pages | `test_agent_apply_e2e.py` captcha cases (earlier) | goes to a person, never solved or skipped |
| **Malformed credentials file** (truncated JSON, empty, invalid UTF-8, a list, `sites` as a string, one bad entry, UTF-8 BOM) | `tests/backend/core/test_breakit_m8.py` | **bug fixed** (87f5b29): any of these crashed every vault call (job runs and Settings). Now the file is validated, a broken one is quarantined byte-for-byte as `site_credentials.json.corrupt-<ts>` and the vault starts empty (sync can restore it), and damaged entries are dropped while the others are kept. If the broken file cannot be moved, it is **never overwritten**: writes raise instead |
| **Corrupt SQLite** memory/metrics DB (text file, damaged header) | `test_corrupt_memory_db_is_quarantined_and_recreated[text/bad-header]` | **bug fixed** (87f5b29): the store crashed on open. Now the DB, `-wal`, `-shm` and `-journal` files are quarantined and a fresh DB is created |
| **Locked SQLite** (another process holds `BEGIN EXCLUSIVE`) | `test_locked_memory_db_waits_then_works`, `…_past_timeout_raises_without_quarantine` | waits up to 15 s (busy timeout), and a lock is never treated as corruption |
| Memory DB unusable **after a submission** | `test_memory_context_survives_an_unusable_store` + `cli/apply_jobs.py` | **bug fixed** (87f5b29): a memory/metrics error after the agent had submitted hit the outer handler and marked the job **failed** (which invites a re-apply). Memory is now best-effort before and after the run |
| Giant résumé PDF (> 50 MB sparse file; 1500-page PDF) | `tests/backend/test_main_api.py` | **bug fixed** (87f5b29): parsing ran on the event loop over every page, so the whole sidecar UI froze. Now there is a 50 MB cap (clear message in < 2 s), only the first 20 pages are read, and parsing runs off the event loop |
| Image-only (scanned) résumé PDF | `test_main_api.py` | **fixed**: was a generic "no text" error. Now it says it looks scanned or image-only and to export a text PDF |
| Unicode / NFD / RTL / emoji / spaced résumé filenames (`Alex Example résumé 履歴書 🚀.pdf`) | `test_main_api.py` | parse and ATS check work |
| 1000+ job queue (5000 jobs) | `test_breakit_m8.py` (claim/update < 0.25 s each), `test_main_api.py` (`/jobs`, `/jobs/stats` < 3 s) | passes. No fix needed |
| Sync remote **force-pushed with an orphan history** | `test_sync.py::test_force_pushed_orphan_history_loses_nothing` | no local data lost. The next sync merges unrelated histories, re-publishes every job and the `.gitattributes`, and a second device converges |
| Sync remote **rewound** (`reset --hard HEAD~1` + force push dropped a `confirmed` event) | `test_remote_rewound_by_force_push_is_restored` | the device that still has it re-pushes it, the other device gets `confirmed`, and there are no duplicate ledger events |

## 4. Real local Ollama (honest numbers)

- Box: 8 vCPU, **no GPU**. Ollama with `qwen3-vl:2b`. Real browser_use Agent on the Greenhouse-style mock ATS.
- Before 8496f74, every step died at 75 s: `llm_call_timeout` is not a browser_use setting and was silently ignored. Now Ollama gets `llm_timeout=900`, `step_timeout=1200`.
- Result: **5 agent steps in 50 min (3005 s wall), STATUS failed, no submission** (`SUBS []`, outcome "Application was not submitted"). It hit 5 empty `ModelProviderError` responses. Generation ran at about 1–3 tok/s while sharing the CPU with the test suite and a PyInstaller build.
- Conclusion: a 2B vision model on CPU is **not practical** for applying. It failed safely: no false success, no submit. Local models need a GPU / Apple-silicon unified memory, or should only be used for extraction. See `LOCAL_MODELS.md`.

## 5. Bugs found in M8 and fix commits

| Commit | Fix |
|---|---|
| 357967b | schedule lock heartbeat (long runs lost the lock); schedule list merged across devices instead of overwritten |
| 596c303 | mock ATS fixtures + the form bugs they found |
| 571cca5 | agent e2e on 5 ATS styles + Outlook OTP; crash / duplicate-submit / corrupt-file fixes |
| 8496f74 | browser_use timeouts were ignored (local Ollama steps died at 75 s) |
| 0dc1333 | Windows: kill-mid-apply pid; `_pid_alive` never fails open without psutil |
| 1ac019e | loopback-only env-gated mail/GitHub overrides so the **packaged** sidecar can be e2e tested |
| e097bd0 | revoked mail sign-in showed "Not connected" with no Reconnect; profile inputs had no accessible name; Tauri shell ignored `APPLYQUEUE_DATA_DIR` (401 on every call) |
| e5d7035 | truncated settings.json crash; 2nd instance token overwrite + SIGKILL of the port holder; orphaned onefile sidecar; read-only/full data dir; zero-width titles; partial tmp on ENOSPC |
| 1b862ca | real Tauri app WebDriver test |
| 88e4c7f | UI e2e wait for auto-fill on slow runners (Intel Mac) |
| 2ac9b2f | Windows: schedule time zones silently ignored (no tz database). `tzdata` bundled; DST/time-zone tests; frozen self-test checks the tz database on all OSes |
| dd77c14 | résumé path check: real containment (macOS temp, `/Volumes`, Linux mounts; sibling-prefix hole); watchdog watches launcher + direct parent (Windows onefile regression from 94f98f8); WebDriver allows `degraded` health; experimental Windows WebDriver job (msedgedriver-tool + tauri-driver, continue-on-error) |
| 6524648 | browser cleanup limited to this instance's profile (other instances' browsers killed; never matched on Windows; chrome-wide taskkill fallback removed); per-test browser profile in agent e2e |
| 87f5b29 | prompt-injection exfiltration (email in `navigate` URL); honeypot fields blocked submission; malformed credentials file crash (+ never overwrite an unmovable corrupt vault); corrupt/locked SQLite; memory error after submit marked the job failed; giant PDF froze the sidecar; image-only PDF message |
| d3fed50 / dc930fd | hostile-page e2e fixtures + tests; sync force-push / rewind tests (no product change needed) |
| d915b59 | Windows dev mode: orphan detection through the venv redirector; packaged orphan test pid fix; real-app WebDriver wizard fix and a Linux CI job |

## 6. Known limitations

- Cross-origin iframes in ATS pages are not scanned by the form guard.
- `evaluate`/`send_keys` paths could still trigger a submit that the guard does not see.
- Agent e2e uses a scripted LLM stand-in. Real-model quality is measured only by the Ollama run above.
- Sync e2e uses a local bare git repo instead of github.com, and repo visibility is checked only before a push (pulls from a public repo are harmless, and creds are only written on push).
- macOS: no WebDriver for WKWebView, so the real webview is not driven there, only launched and health-checked.
- The exfiltration guard covers URLs (navigate/search). A hostile page could still ask the model to type personal data into a field **on that page**, which is indistinguishable from filling the application. Only the email and phone are guarded, not the name or address.
- A slow-loris page takes about 200 s to give up (browser_use screenshot and step timeouts); this is bounded, but slow.

## 7. Not tested yet (honest)

- **Any CI since dd77c14** (Actions billing). Windows, Intel Mac and Apple silicon need a re-run once billing is fixed.
- Apple-silicon arm64 run in M8 (included in `gh workflow run test.yml -R JonathanLiu1401/ApplyQueue --ref main`, blocked by billing).
- Real Tauri app over WebDriver on Windows: job written in dd77c14 (`msedgedriver-tool` fetches the msedgedriver matching WebView2; tauri-driver `--native-driver`; `continue-on-error` until proven), never run (billing).
- Sync against a real github.com repo: needs Art's OK to create a throwaway private repo on his account (proposed: `applyqueue-sync-e2e-20261025`, created `--private`, visibility re-checked as PRIVATE before any write, deleted afterwards only if `gh` has the `delete_repo` scope, otherwise left private and listed here). Not done.
- Real Gmail/Outlook/GitHub accounts (by design: mocks only).
