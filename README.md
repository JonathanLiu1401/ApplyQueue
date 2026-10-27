# ApplyQueue

**Open-source desktop app that applies to jobs for you with the AI you already have.**

> **ApplyQueue is a fork of [LangHire](https://github.com/jaimaann/LangHire) by
> [jaimaann](https://github.com/jaimaann)** (MIT). It keeps LangHire's full git history and
> its stack: a Tauri v2 desktop shell, a React + TypeScript UI, and a Python FastAPI sidecar
> that drives a real browser with [browser-use](https://github.com/browser-use/browser-use) /
> Playwright. Thank you to the LangHire authors; see [LICENSE](LICENSE) and the upstream
> README kept at [docs/LANGHIRE_README.md](docs/LANGHIRE_README.md).

> **Status: alpha (v0.1.1).** The source is public; installers are built by GitHub Actions
> and are **unsigned** (macOS Gatekeeper and Windows SmartScreen will warn; see Download below).
>
> **Tested platforms:** Linux x86_64 (self-hosted CI on every push), plus **GitHub-hosted VMs**
> (not physical hardware) for Windows x64, Intel Mac (macos-15-intel) and Apple silicon
> (macos-14): vitest, the full pytest suite and UI e2e against the packaged sidecar, and an
> install + launch + `/health` smoke of the built installers. Details and run links:
> [docs/applyqueue/TEST_REPORT.md](docs/applyqueue/TEST_REPORT.md).

**Works with:** Grok Bot (recommended) · Claude Code · Grok Build · Cursor CLI · ChatGPT / Codex
· Gemini (API key) · any API key LangHire already supports (OpenAI, Anthropic, Bedrock, Gemini,
OpenRouter, OpenAI-compatible) · **local models with no subscription** (Ollama, LM Studio,
llama.cpp, Hugging Face).

## Download

Download the installer for your computer (version 0.1.1). Pick **one** file:

| Your computer | Download |
|---|---|
| **Mac with Apple silicon** (M1, M2, M3, M4 or newer) | [ApplyQueue_0.1.1_aarch64.dmg](https://github.com/JonathanLiu1401/ApplyQueue/releases/download/v0.1.1/ApplyQueue_0.1.1_aarch64.dmg) |
| **Mac with an Intel chip** | [ApplyQueue_0.1.1_x64.dmg](https://github.com/JonathanLiu1401/ApplyQueue/releases/download/v0.1.1/ApplyQueue_0.1.1_x64.dmg) |
| **Windows 10 or 11** | [ApplyQueue_0.1.1_x64-setup.exe](https://github.com/JonathanLiu1401/ApplyQueue/releases/download/v0.1.1/ApplyQueue_0.1.1_x64-setup.exe) (or the [.msi](https://github.com/JonathanLiu1401/ApplyQueue/releases/download/v0.1.1/ApplyQueue_0.1.1_x64_en-US.msi) if your workplace prefers MSI) |
| **Linux, any distro** | [ApplyQueue_0.1.1_amd64.AppImage](https://github.com/JonathanLiu1401/ApplyQueue/releases/download/v0.1.1/ApplyQueue_0.1.1_amd64.AppImage) |
| **Ubuntu, Debian, Mint, Pop!_OS** | [ApplyQueue_0.1.1_amd64.deb](https://github.com/JonathanLiu1401/ApplyQueue/releases/download/v0.1.1/ApplyQueue_0.1.1_amd64.deb) |
| **Fedora, RHEL, openSUSE** | [ApplyQueue-0.1.1-1.x86_64.rpm](https://github.com/JonathanLiu1401/ApplyQueue/releases/download/v0.1.1/ApplyQueue-0.1.1-1.x86_64.rpm) |

Checksums: [SHA256SUMS.txt](https://github.com/JonathanLiu1401/ApplyQueue/releases/download/v0.1.1/SHA256SUMS.txt). Newer versions will appear on the
[Releases page](https://github.com/JonathanLiu1401/ApplyQueue/releases/latest).

Not sure which Mac you have? Click the Apple menu, then **About This Mac**. If it says **Chip:
Apple M…**, take the Apple silicon file. If it says **Processor: Intel**, take the Intel file.

### Opening the app the first time

The installers are **not code-signed yet** (signing certificates cost money and this is a free
project). Your computer will warn you the first time. This is expected, and you only do it once.
To check that your download was not changed, compare it with `SHA256SUMS.txt` on the release page.

- **macOS:** open the `.dmg` and drag **ApplyQueue** into **Applications**. In Applications,
  **right-click** (or Control-click) ApplyQueue and choose **Open**, then click **Open** again.
  If macOS only offers "Move to Trash" or "Done", click **Done**, open **System Settings >
  Privacy & Security**, scroll down to the message about ApplyQueue, click **Open Anyway**, and
  confirm.
- **Windows:** run the `-setup.exe`. If a blue **"Windows protected your PC"** (SmartScreen) box
  appears, click **More info**, then **Run anyway**.
- **Linux AppImage:** make it executable, then run it:
  `chmod +x ApplyQueue_*.AppImage && ./ApplyQueue_*.AppImage`
  (or right-click the file > Properties > Permissions > "Allow executing file as program").
  If it complains about FUSE, install `libfuse2` (Ubuntu 22.04: `sudo apt install libfuse2`).
- **Linux .deb / .rpm:** `sudo apt install ./ApplyQueue_*.deb` or `sudo dnf install ./ApplyQueue-*.rpm`.

## Run 100% locally, no subscription

Pick **"Run 100% locally"** in the setup wizard. ApplyQueue finds Ollama, LM Studio, llama.cpp,
or GGUF models you already downloaded, checks your RAM and GPU, recommends a vision model that
fits, and downloads it with one click. Nothing leaves your computer except the job sites
themselves.

Be realistic: on a gaming GPU an application takes a few minutes. On a CPU-only machine (Intel
Macs included) small models are often too slow to finish at all: in our measured run a 2B vision
model on 8 CPU cores managed 5 agent steps in 50 minutes and did not submit (it failed safely).
Small models make more mistakes, so start in review-first mode. See
[docs/applyqueue/LOCAL_MODELS.md](docs/applyqueue/LOCAL_MODELS.md) and the measured results in
[docs/applyqueue/TEST_REPORT.md](docs/applyqueue/TEST_REPORT.md).

## What ApplyQueue adds on top of LangHire

| | LangHire (upstream) | ApplyQueue |
|---|---|---|
| AI sign-in | API keys and Ollama | Also your **existing subscriptions**, by running each vendor's official CLI under your own login (never by reusing tokens) |
| Optional questions | Agent may skip a field after 5 stuck steps | **Hard rule: every question is answered**, optional ones included, truthfully from your profile |
| "Applied" status | Trusts the agent's own `done(success)` ([#89](https://github.com/jaimaann/LangHire/issues/89)) | Applied only with **page confirmation + network check + confirmation email** |
| Email | Gmail used for OTP codes | **Gmail and Outlook** confirmation tracking and labeling |
| Multiple machines/agents | Single machine | **Private GitHub sync repo** for queue, ledger, and credentials (refuses a public repo) |
| Local models | Ollama supported | **Headline path:** runtime detection, vision-model recommender, one-click download |
| Searches | Collect by title | **3-step quick start**, saved searches with stats and review-first or automatic mode, a tracker, resume routing with A/B stats and an ATS keyword check, your own categories, bans and answers, **per-search schedules** (hourly, daily, cron) with pause/resume that keep running through an always-on agent or an OS scheduler |
| Replies | Not tracked | Rejection and interview emails labelled `ApplyQueue/Rejected` and `ApplyQueue/Interview`; recruiter outreach off by default, drafts only |

## Why ApplyQueue

- **Free and open source (MIT).** No subscription to us, ever.
- **Run it 100% locally if you want.** Local models are slower and need a decent GPU, so they
  work best left running overnight.
- **Answers every question truthfully.** Never invents experience; never leaves a field blank.
- **Many agents, one queue.** Your bots and computers share one private repo, so no job is
  applied to twice.

## Docs

- [docs/PLAN.md](docs/PLAN.md): fork audit, subscription-CLI integration, vendor terms, milestones.
- [docs/applyqueue/DESIGN.md](docs/applyqueue/DESIGN.md): original product design (written for an
  Electron prototype, archived on branch `archive/electron-scaffold`; stack sections superseded by PLAN.md).
- [TESTING.md](TESTING.md): running the test suites.

## Develop

```bash
npm ci                 # UI deps
uv sync                # Python sidecar deps (Python 3.13)
npm run tauri dev      # desktop app in dev mode
npm test && uv run pytest
npm run privacy-scan   # personal-data scan over the full git history
```

## License

MIT. Copyright (c) 2026 LangHire Contributors, and Copyright (c) 2026 JonathanLiu1401 and
ApplyQueue contributors. See [LICENSE](LICENSE).
