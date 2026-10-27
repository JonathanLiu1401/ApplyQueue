# CI runners

The repo is **public**, so **standard GitHub-hosted runners are free** and are used for Windows
and macOS. Linux checks stay on the self-hosted box runner. Only standard labels are used
(`windows-latest`, `macos-15-intel`, `macos-14`, `ubuntu-22.04`, `ubuntu-latest`): **never
larger or paid runner labels**. Nothing runs on `pull_request`.

| Runner | Labels | Where | Used by |
|---|---|---|---|
| `applyqueue-box-linux` | `self-hosted, linux, x64, applyqueue` | the shared Grok Bot box (Debian 13), user `aqrunner` (no sudo) | `ci.yml` Linux checks, `linux-app`, `Required checks`; `build.yml` self-hosted .deb (opt-in) |
| GitHub-hosted | `windows-latest` | Windows Server VM, x64 | `ci.yml` Windows tests; `build.yml` .exe (NSIS) + .msi |
| GitHub-hosted | `macos-15-intel` | macOS 15 VM, Intel x86_64 | `ci.yml` Intel Mac tests; `build.yml` Intel .dmg |
| GitHub-hosted | `macos-14` | macOS 14 VM, Apple silicon arm64 | `ci.yml` Apple silicon tests; `build.yml` arm64 .dmg |
| GitHub-hosted | `ubuntu-22.04` | Ubuntu 22.04 VM, x64 | `build.yml` .deb, .rpm, AppImage (22.04 keeps the AppImage glibc baseline low) |
| optional self-hosted | `macOS, X64, intel` / `macOS, ARM64, apple-silicon` / `windows, x64` | your own machines | not used by the workflows any more; setup kept below for real-hardware testing |

`macos-13` (the upstream LangHire Intel label) was retired by GitHub in December 2025;
`macos-15-intel` is the standard Intel replacement (available until August 2027).

These are **GitHub-hosted virtual machines, not physical hardware**: they prove the app builds,
installs, launches and passes the suites on each OS, not that it behaves on every real machine.

## What runs when

| Event | `ci.yml` | `build.yml` |
|---|---|---|
| push to `main` (code paths) | Linux checks (box) + Windows, Intel Mac, Apple silicon tests (hosted) | installers on all four hosted OSes, real-app smoke, artifacts |
| push of a `v*` tag | same | same, then a **draft** release with all installers (an existing release for the tag is left untouched) |
| docs only (`docs/**`, `*.md`) | nothing (`paths-ignore`) | nothing |
| push to any other branch | nothing; dispatch on the branch instead | nothing; dispatch on the branch |
| `pull_request`, fork PRs, `pull_request_target`, schedules | never | never |
| `workflow_dispatch` | choose `linux`, `linux_app`, `windows`, `intel_mac`, `apple_silicon`; `probe` | all hosted builds; `self_hosted_linux`; `probe` |

**Test coverage per job.** Linux (box): gitleaks, personal-data scan, licenses, tsc, ESLint, Vite
build, Vitest, pytest, PyInstaller sidecar + self-test, UI e2e; `linux_app` adds the real Tauri app
over WebDriver and `cargo test`. Windows: tsc, Vitest, pytest, sidecar + self-test, UI e2e against
the packaged sidecar, `cargo test`, the real Tauri app over WebDriver (tauri-driver +
msedgedriver). Intel Mac / Apple silicon: the same minus WebDriver: **Tauri's WebDriver does not
support macOS (WKWebView has no driver)**, so the macOS real-app check is the `build.yml` smoke:
open the built `.dmg`, launch the `.app`, wait for its bundled sidecar to answer `GET /health` as
the frozen backend. Windows and Linux get the same smoke on the NSIS install, the AppImage and the
installed `.deb`.

**Unsigned.** No signing or notarization secrets are configured; installers are unsigned (see the
README Download section for opening them). Artifacts are kept 30 days.

**Probe mode (no red runs, no failure emails while iterating).** Dispatch on a feature branch with
`-f probe=true`: test and smoke steps get `continue-on-error`, so the run stays green while each
failing step still shows as failed in the log. Fix, repeat, then do one normal (non-probe) run on
the branch and fast-forward `main` only when it is green.

```bash
gh workflow run build.yml -R JonathanLiu1401/ApplyQueue --ref <branch> -f probe=true
gh workflow run ci.yml    -R JonathanLiu1401/ApplyQueue --ref <branch> -f probe=true -f linux=false
# check the box runner
gh api repos/JonathanLiu1401/ApplyQueue/actions/runners --jq '.runners[] | [.name, .status, .busy] | @tsv'
```

If a hosted run is ever refused for billing or spending limits, stop and report it; do not retry.

Box jobs delete `src-tauri/target`, the sidecar, `dist`, `node_modules` and `.venv` at the end
(`if: always()`) and prune the uv cache.

## Optional: your own Intel Mac runner (one command)

Open **Terminal** on the Intel Mac, signed in as your normal (admin) user, and paste:

```bash
command -v gh >/dev/null || { command -v brew >/dev/null || /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"; eval "$(/usr/local/bin/brew shellenv 2>/dev/null || /opt/homebrew/bin/brew shellenv)"; brew install gh; } && { gh auth status >/dev/null 2>&1 || gh auth login -h github.com -p https -w; } && gh api -H 'Accept: application/vnd.github.raw' 'repos/JonathanLiu1401/ApplyQueue/contents/scripts/runners/setup-macos-runner.sh?ref=main' > /tmp/aq-runner-setup.sh && bash /tmp/aq-runner-setup.sh
```

The command installs Homebrew (only if missing) and the GitHub CLI, signs you in to GitHub in the
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

The workflows no longer target self-hosted Mac or Windows labels (hosted runners cover them); a
runner of your own is for testing on real hardware by pointing a job at its labels.

Rerunning the one-liner is safe: it skips what is installed, keeps an existing registration and
restarts the service. On an Apple silicon Mac it refuses unless you run
`bash /tmp/aq-runner-setup.sh --allow-arm64` (labels `macOS, ARM64, apple-silicon`).
Other flags: `--name`, `--skip-prereqs`, `--no-keep-awake`, `--token-stdin`, `--uninstall`.

## Optional: your own Windows runner (one command)

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
`start` again (an agent can do it).

**Restart after a reboot or a box restore.** After a plain reboot, `sudo bash
scripts/runners/linux-box-runner.sh start` is enough. A box restore can also drop the `aqrunner`
user, `/home/aqrunner` and the system packages below (it did on 2026-10-25). Then: `sudo useradd
-m -s /bin/bash aqrunner && sudo chmod 750 /home/aqrunner`, reinstall the packages, download the
latest `actions-runner-linux-x64` release into `/home/aqrunner/actions-runner-applyqueue` as
`aqrunner` (check its SHA-256 against the release notes), register again with the command below,
then `start`. Confirm it shows `online` with the "check runners" command above. System libraries for Tauri/WebKitGTK/WebDriver are preinstalled on
the host so jobs never need sudo: `libwebkit2gtk-4.1-dev build-essential curl wget file
libxdo-dev libssl-dev libayatana-appindicator3-dev librsvg2-dev patchelf webkit2gtk-driver xvfb`.

Never run a Windows VM on the box: booting one crashed the shared box twice on 2026-10-25.

Register again from scratch (token never written anywhere):

```bash
TOK=$(gh api -X POST repos/JonathanLiu1401/ApplyQueue/actions/runners/registration-token --jq .token)
printf '%s' "$TOK" | sudo -u aqrunner bash -c 'IFS= read -r T; cd ~/actions-runner-applyqueue && ACTIONS_RUNNER_INPUT_TOKEN="$T" ./config.sh --unattended --url https://github.com/JonathanLiu1401/ApplyQueue --name applyqueue-box-linux --labels self-hosted,linux,x64,applyqueue --no-default-labels --work _work --replace'; unset TOK
```

## Security notes

- **Public repo + self-hosted runner.** A self-hosted runner on a public repo must never run code
  from fork PRs. The workflows have no `pull_request` triggers, and every self-hosted job runs only
  on pushes to `main`/`v*` tags or dispatches, which need write access. Keep it that way; if PR
  checks are ever added, run them on GitHub-hosted runners only.
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
