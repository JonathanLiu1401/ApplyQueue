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
| d915b59 | Windows dev mode: orphan detection through the venv redirector; packaged orphan test pid fix; real-app WebDriver wizard fix and a Linux CI job |

## 6. Known limitations

- Cross-origin iframes in ATS pages are not scanned by the form guard.
- `evaluate`/`send_keys` paths could still trigger a submit that the guard does not see.
- Agent e2e uses a scripted LLM stand-in. Real-model quality is measured only by the Ollama run above.
- Sync e2e uses a local bare git repo instead of github.com, and repo visibility is checked only before a push (pulls from a public repo are harmless, and creds are only written on push).
- macOS: no WebDriver for WKWebView, so the real webview is not driven there, only launched and health-checked.

## 7. Not tested yet (honest)

- **Any CI since dd77c14** (Actions billing). Windows, Intel Mac and Apple silicon need a re-run once billing is fixed.
- Apple-silicon arm64 run in M8 (`gh workflow run test.yml -f run_macos=true`, blocked by billing).
- Real Tauri app over WebDriver on Windows: job written in dd77c14 (`msedgedriver-tool` fetches the msedgedriver matching WebView2; tauri-driver `--native-driver`; `continue-on-error` until proven), never run (billing).
- Sync against a real github.com repo: needs Art's OK to create a throwaway private repo on his account (proposed: `applyqueue-sync-e2e-20261025`, created `--private`, visibility re-checked as PRIVATE before any write, deleted afterwards only if `gh` has the `delete_repo` scope, otherwise left private and listed here). Not done.
- Real Gmail/Outlook/GitHub accounts (by design: mocks only).
