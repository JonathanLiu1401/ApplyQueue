# CI on self-hosted runners

ApplyQueue CI runs **only on self-hosted runners**. Nothing uses paid GitHub-hosted minutes:
there are no `ubuntu-latest`, `windows-*` or `macos-*` jobs. The repo is private.

| Runner | Labels | Where | Runs as |
|---|---|---|---|
| `applyqueue-box-linux` | `self-hosted, linux, x64, applyqueue` | the shared Grok Bot box (Debian 13) | user `aqrunner` (no sudo), supervised loop |
| `applyqueue-mac-intel-<host>` | `self-hosted, macOS, X64, intel, applyqueue` | an Intel Mac | the Mac user, launchd agent |
| `applyqueue-mac-arm64-<host>` | `self-hosted, macOS, ARM64, apple-silicon, applyqueue` | an Apple silicon Mac (optional) | the Mac user, launchd agent |
| `applyqueue-win-<host>` | `self-hosted, windows, x64, applyqueue` | a Windows PC | Windows service, `NETWORK SERVICE` |

## What runs when

| Event | Jobs |
|---|---|
| push to `main` (code paths) | `ci.yml` Linux checks: gitleaks, personal-data scan, licenses, tsc, ESLint, Vite build, Vitest, pytest (agent e2e + break-it), PyInstaller sidecar + self-test, UI e2e. Plus Intel Mac **only when** the repo variable `INTEL_MAC_RUNNER` is `true`. |
| push to `main`, docs only (`docs/**`, `*.md`) | nothing (`paths-ignore`). The full-tree privacy scan runs on the next code push. |
| push to any other branch | nothing; dispatch the workflow on the branch instead |
| `pull_request`, fork PRs, `pull_request_target`, schedules | never: no such triggers, and every job also checks `github.event_name != 'pull_request' && github.repository == 'JonathanLiu1401/ApplyQueue'` |
| `workflow_dispatch` on `ci.yml` | Linux checks, plus opt-in `linux_app` (Tauri compile + WebDriver + .deb), `windows`, `intel_mac`, `apple_silicon` |
| `workflow_dispatch` on `build.yml` | draft release build for the platforms you tick (default Linux only) |

At most one CI run at a time (`concurrency: applyqueue-ci`, a newer run cancels the older one).
Release builds use their own group and are never cancelled. Every job deletes `src-tauri/target`,
the sidecar, `dist`, `node_modules` and `.venv` at the end (`if: always()`) and prunes the uv
cache. No `actions/cache`, no artifacts: nothing is stored on GitHub's (billed) storage.
Dependabot config was removed (its PRs would only trigger CI noise).

```bash
# Linux only
gh workflow run ci.yml -R JonathanLiu1401/ApplyQueue --ref main
# everything that has a runner online
gh workflow run ci.yml -R JonathanLiu1401/ApplyQueue --ref main -f linux_app=true -f windows=true -f intel_mac=true
# check runners
gh api repos/JonathanLiu1401/ApplyQueue/actions/runners --jq '.runners[] | [.name, .status, .busy] | @tsv'
```

## Intel Mac (one command)

Open **Terminal** on the Intel Mac, signed in as your normal (admin) user, and paste:

```bash
command -v gh >/dev/null || { command -v brew >/dev/null || /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"; eval "$(/usr/local/bin/brew shellenv 2>/dev/null || /opt/homebrew/bin/brew shellenv)"; brew install gh; } && { gh auth status >/dev/null 2>&1 || gh auth login -h github.com -p https -w; } && gh api -H 'Accept: application/vnd.github.raw' 'repos/JonathanLiu1401/ApplyQueue/contents/scripts/runners/setup-macos-runner.sh?ref=main' > /tmp/aq-runner-setup.sh && bash /tmp/aq-runner-setup.sh
```

Why it looks like this: the repo is private, so there is no public raw URL to `curl`. The
command installs Homebrew (only if missing) and the GitHub CLI, signs you in to GitHub in the
browser, then downloads the setup script **through your own signed-in gh** and runs it. No token
is ever typed, pasted, stored in the repo or written to the shell history.

What you will be asked to do, in order:

1. **Mac password** (Homebrew install, and later `pmset` to stop sleeping on the charger).
   Homebrew may also say "Press RETURN to continue".
2. **Xcode Command Line Tools**: if a dialog appears, click *Install* and *Agree*; the script waits
   (up to an hour) and continues by itself.
3. **GitHub sign-in**: gh prints a one-time code and opens the browser. Sign in as
   the GitHub account that owns the repo, paste the code, click *Authorize*.
4. Wait (10-20 minutes the first time: Node, Python 3.11, uv, Rust, the runner).
5. The script ends with `Done. applyqueue-mac-intel-<host> is online ...`.

Then keep the Mac **logged in, plugged in, awake and online** (the runner is a launchd agent of
your user: it starts when you log in and stops when you log out). If FileVault is off you can
enable automatic login in System Settings > Users & Groups so it survives a reboot.

Last step (done by an agent after the runner shows online): set the repo variable so pushes to
`main` also test on the Intel Mac:

```bash
gh variable set INTEL_MAC_RUNNER -R JonathanLiu1401/ApplyQueue --body true
```

Rerunning the one-liner is safe: it skips what is installed, keeps an existing registration and
restarts the service. On an Apple silicon Mac it refuses unless you run
`bash /tmp/aq-runner-setup.sh --allow-arm64` (labels `macOS, ARM64, apple-silicon`).
Other flags: `--name`, `--skip-prereqs`, `--no-keep-awake`, `--token-stdin`, `--uninstall`.

## Windows (one command, for a future machine)

No Windows runner exists today (no Windows machine is available). This is ready for when one is.

Open **PowerShell as administrator** (Start > type PowerShell > right-click > Run as
administrator) and paste:

```powershell
winget install -e --id GitHub.cli --accept-source-agreements --accept-package-agreements; $env:Path=[Environment]::GetEnvironmentVariable('Path','Machine')+';'+[Environment]::GetEnvironmentVariable('Path','User'); gh auth status 2>$null; if ($LASTEXITCODE) { gh auth login -h github.com -p https -w }; cmd /c "gh api -H ""Accept: application/vnd.github.raw"" ""repos/JonathanLiu1401/ApplyQueue/contents/scripts/runners/setup-windows-runner.ps1?ref=main"" > %TEMP%\aq-runner-setup.ps1"; powershell -NoProfile -ExecutionPolicy Bypass -File $env:TEMP\aq-runner-setup.ps1
```

It installs (winget, machine-wide) Git, GitHub CLI, Node LTS, Python 3.11, uv, the WebView2
runtime and Visual Studio 2022 Build Tools (C++ workload, needed by Rust MSVC; large, 20-40 min),
Rust into `C:\rust`, then registers `applyqueue-win-<host>` as a Windows service under
`NETWORK SERVICE` in `C:\actions-runner-applyqueue`. msedgedriver is fetched by the workflow per
run so it always matches the installed WebView2. You will be asked to sign in to GitHub in the
browser. Keep the PC on, plugged in, awake (Settings > System > Power > Sleep: Never when plugged
in). Uninstall: `powershell -ExecutionPolicy Bypass -File $env:TEMP\aq-runner-setup.ps1 -Uninstall`.

Known limit: a Windows service runs in session 0 without a desktop. The WebDriver job drives the
WebView2 app there; if it cannot render, reinstall the runner to run as your user
(`config.cmd ... --runasservice --windowslogonaccount .\<you>`) or run `run.cmd` in a logged-in
session.

## Linux box runner

Installed in `/home/aqrunner/actions-runner-applyqueue`, registered with labels
`self-hosted,linux,x64,applyqueue` (`--no-default-labels`), runner version from the latest
official `actions/runner` release (SHA-256 checked). The box has no systemd (PID 1 is `tini`)
and no cron, so `svc.sh` cannot be used. Instead `scripts/runners/linux-box-runner.sh` runs
`run.sh` as `aqrunner` in a detached session with a restart loop (crash or self-update restarts
it after 15 s; old `_diag` logs are pruned; the supervisor log is capped at 1 MB):

```bash
sudo bash scripts/runners/linux-box-runner.sh start    # idempotent
sudo bash scripts/runners/linux-box-runner.sh status
sudo bash scripts/runners/linux-box-runner.sh stop
sudo tail -f /home/aqrunner/runner-supervisor.log
```

It survives agent sessions and runner crashes, but **not a reboot of the box**: after one, run
`start` again (an agent can do it). System libraries for Tauri/WebKitGTK/WebDriver/Playwright
(`libwebkit2gtk-4.1-dev`, `webkit2gtk-driver`, `xvfb`, ...) are preinstalled on the host so jobs
never need sudo.

Register again from scratch (token never written anywhere):

```bash
TOK=$(gh api -X POST repos/JonathanLiu1401/ApplyQueue/actions/runners/registration-token --jq .token)
printf '%s' "$TOK" | sudo -u aqrunner bash -c 'IFS= read -r T; cd ~/actions-runner-applyqueue && ACTIONS_RUNNER_INPUT_TOKEN="$T" ./config.sh --unattended --url https://github.com/JonathanLiu1401/ApplyQueue --name applyqueue-box-linux --labels self-hosted,linux,x64,applyqueue --no-default-labels --work _work --replace'; unset TOK
```

## Security notes

- **Private repo only.** Self-hosted runners on a public repo would run code from anyone's fork
  PR. The workflows have no PR triggers today. If the repo ever goes public, keep it that way
  (or run PR checks on GitHub-hosted runners, which are free for public repos).
- **No fork PRs.** No `pull_request` / `pull_request_target` triggers; each job also has the
  `github.event_name != 'pull_request' && github.repository == 'JonathanLiu1401/ApplyQueue'` guard.
- **A runner sees repo secrets** used by the jobs it runs (`PRIVACY_DENYLIST_SHA256`, and the
  Apple signing secrets in release builds) and the job's `GITHUB_TOKEN`. Only register runners on
  machines you trust, scoped to this repo (never org- or account-wide).
- **Unprivileged.** Box: `aqrunner` has no sudo and cannot read `/home/box` (gh login, other
  bots). It can read world-readable files elsewhere on the box (e.g. `/workspace`), so only code
  pushed by the owner may run there, which is what the triggers enforce. Mac: runs as your user
  (launchd agent, never root); release builds use a throwaway keychain that is added to (not
  replacing) your keychain list and deleted at the end. Windows: `NETWORK SERVICE`.
- **Tokens.** Registration tokens are short-lived (1 h), fetched with gh at setup time, passed to
  `config.sh` via the `ACTIONS_RUNNER_INPUT_TOKEN` environment variable, never echoed or saved.
- Release builds need `contents: write` (draft release). CI jobs are `contents: read`.

## Removing a runner

- Mac: `bash /tmp/aq-runner-setup.sh --uninstall` (refetch with the one-liner's `gh api` part if
  `/tmp` was cleared), then `rm -rf ~/actions-runner-applyqueue`.
- Windows: `... -File $env:TEMP\aq-runner-setup.ps1 -Uninstall`, then delete
  `C:\actions-runner-applyqueue`.
- Box: `sudo bash scripts/runners/linux-box-runner.sh stop`, then
  `RT=$(gh api -X POST repos/JonathanLiu1401/ApplyQueue/actions/runners/remove-token --jq .token); printf '%s' "$RT" | sudo -u aqrunner bash -c 'IFS= read -r T; cd ~/actions-runner-applyqueue && ACTIONS_RUNNER_INPUT_TOKEN="$T" ./config.sh remove'; unset RT`,
  optionally `sudo userdel -r aqrunner`.
- Any runner, from anywhere: `gh api -X DELETE repos/JonathanLiu1401/ApplyQueue/actions/runners/<id>`
  (ids from `gh api repos/JonathanLiu1401/ApplyQueue/actions/runners`).
