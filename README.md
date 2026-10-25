# ApplyQueue

**Open-source desktop app that applies to jobs for you with the AI you already have.**

> **ApplyQueue is a fork of [LangHire](https://github.com/jaimaann/LangHire) by
> [jaimaann](https://github.com/jaimaann)** (MIT). It keeps LangHire's full git history and
> its stack: a Tauri v2 desktop shell, a React + TypeScript UI, and a Python FastAPI sidecar
> that drives a real browser with [browser-use](https://github.com/browser-use/browser-use) /
> Playwright. Thank you to the LangHire authors; see [LICENSE](LICENSE) and the upstream
> README kept at [docs/LANGHIRE_README.md](docs/LANGHIRE_README.md).

> **Status: pre-alpha, private.** This repository stays private until the full end-to-end and
> break-it test suite passes on macOS, Windows, and Linux and the maintainer approves the test
> report. See [docs/PLAN.md](docs/PLAN.md).

**Works with:** Grok Bot (recommended) · Claude Code · Grok Build · Cursor CLI · ChatGPT / Codex
· Gemini (API key) · any API key LangHire already supports (OpenAI, Anthropic, Bedrock, Gemini,
OpenRouter, OpenAI-compatible) · **local models with no subscription** (Ollama, LM Studio,
llama.cpp, Hugging Face).

## What ApplyQueue adds on top of LangHire

| | LangHire (upstream) | ApplyQueue |
|---|---|---|
| AI sign-in | API keys and Ollama | Also your **existing subscriptions**, by running each vendor's official CLI under your own login (never by reusing tokens) |
| Optional questions | Agent may skip a field after 5 stuck steps | **Hard rule: every question is answered**, optional ones included, truthfully from your profile |
| "Applied" status | Trusts the agent's own `done(success)` ([#89](https://github.com/jaimaann/LangHire/issues/89)) | Applied only with **page confirmation + network check + confirmation email** |
| Email | Gmail used for OTP codes | **Gmail and Outlook** confirmation tracking and labeling |
| Multiple machines/agents | Single machine | **Private GitHub sync repo** for queue, ledger, and credentials (refuses a public repo) |
| Local models | Ollama supported | **Headline path:** runtime detection, vision-model recommender, one-click download |
| Searches | Collect by title | **Saved searches** with stats and review-first or automatic mode; fully user-defined job domains |

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
