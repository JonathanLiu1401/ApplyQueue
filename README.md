# ApplyQueue

**Open-source desktop app that automates job applications with your AI agent.**

> **Status: pre-alpha.** The design is written and the app shell builds on macOS, Windows,
> and Linux, but it cannot apply to jobs yet. Follow the [milestones](MILESTONES.md) or
> [issues](https://github.com/JonathanLiu1401/ApplyQueue/issues) to see progress.

**Works with:** Grok Bot (recommended) · ChatGPT · Claude · Gemini · Cursor ·
**local models** (Ollama, LM Studio, llama.cpp, Hugging Face) with no subscription.

## What it is, in plain words

Job hunting means filling in the same forms hundreds of times. ApplyQueue does that for you:

1. **Upload your resume.**
2. **Pick job titles and locations.** Everything else is optional.
3. **Press Start.**

ApplyQueue finds matching postings, opens each application in a real browser, fills every
field truthfully from your profile, submits, and then watches your email for the
confirmation, so you know which applications actually went through. You stay in control:
each search can run in **review-first** mode (you approve every match) or **fully
automatic** mode.

## Why ApplyQueue

- **Free and open source (MIT).** No subscription to us, ever.
- **Run it 100% locally if you want.** Use a free local AI model on your own computer, with
  no account and no data leaving your machine. It is slower and needs a decent GPU, so it
  is best left running overnight. Or use the AI you already pay for.
- **Local-first.** Your profile, answers, and queues live on your computer, not on our
  servers (there are none).
- **Answers every question, truthfully.** Including the optional ones. It never leaves a
  field blank and never invents facts; if it truly cannot answer, it skips the job and asks
  you once, then remembers.
- **Proof, not guesses.** An application only counts as submitted when a real confirmation
  email arrives. ApplyQueue labels and archives it for you.
- **Many agents, one queue.** Run several AI workers at once (your laptop, a spare PC, a
  cloud bot). They coordinate through a **private** GitHub repo in your own account, so no
  job is applied to twice.

## Features (planned for v1)

| | |
| --- | --- |
| **Queues** | Named saved searches: titles, locations, filters, excluded companies, and job boards. Each queue has its own stats: found, applied, skipped, confirmed, interviews. |
| **Review-first or automatic** | Per queue. Start in review-first; switch when you trust it. |
| **Application tracker** | One dashboard for every application across all queues, updated from your email (confirmation, assessment, interview, rejection). |
| **Multiple resumes** | Route each job category to the right resume, see which resume gets more responses (A/B), and check a resume against a posting's keywords. |
| **Any field** | Software, nursing, finance, trades, research: you define the categories. |
| **Email** | Gmail, Outlook, or IMAP, for confirmations and one-time codes. |
| **Recruiter outreach** | **Off by default.** When enabled it only writes drafts; nothing is sent without your approval. |

## Choose your AI

| Option | Cost | Runs where | Notes |
| --- | --- | --- | --- |
| **Grok Bot** (recommended) | Your Grok Bot plan | Its own cloud computer | Nothing to keep running at home; scheduled workers. |
| **Local model** | Free | Your computer | ApplyQueue detects Ollama, LM Studio, or llama.cpp, recommends a vision model that fits your GPU/RAM, and downloads it in one click. |
| **ChatGPT / Claude / Gemini** | Your plan or API key | Their cloud or this app | Setup prompt for their agent features, or API key with the built-in runner. |
| **Cursor** | Your plan | Cursor agents | Connects to the ApplyQueue tool server. |

Every option drives the **same tools** (browser control, form filling, secure password
fill, email labeling, the shared job queue), exposed by the app as an
[MCP](https://modelcontextprotocol.io) server. Details and sources: [DESIGN.md](DESIGN.md)
section 4.

## Architecture overview

```
 You ──► ApplyQueue desktop app (Electron)
           │  profile, queues, answers, resumes: stored on this computer
           ▼
   ApplyQueue MCP server  ◄── AI backend: Grok Bot · local model · ChatGPT · Claude · Gemini · Cursor
     next_job · get_answer · fill_secret · finish_job · label_confirmation · ...
           │
           ├─► Browser (Playwright / Chrome DevTools Protocol) ──► job boards and portals
           ├─► Email (Gmail / Outlook / IMAP) ──► confirmations, codes, labels
           └─► Your PRIVATE sync repo (GitHub) ──► queue, ledger, and logins shared by your agents
```

Your secrets never go into this public repo or into AI prompts. Portal passwords and browser
sessions are stored in your **private** sync repo (ApplyQueue checks it is private before
every write) and mirrored in an OS-encrypted local vault. See [SECURITY.md](SECURITY.md).

## Develop

```bash
pnpm install
pnpm --filter applyqueue dev   # run the app
pnpm check                      # lint, typecheck, tests, privacy scan, license check
pnpm dist                       # build an installer for your OS
```

See [CONTRIBUTING.md](CONTRIBUTING.md). Design: [DESIGN.md](DESIGN.md). Roadmap:
[MILESTONES.md](MILESTONES.md).

## Responsible use

ApplyQueue submits applications as you, with your real information. Respect each site's
terms and rate limits (ApplyQueue paces itself and has daily caps), and review what it
submits. CAPTCHAs are only ever solved the way a person would, in the visible browser.

## License

[MIT](LICENSE)
