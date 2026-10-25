# Contributing to ApplyQueue

Thanks for helping! ApplyQueue is pre-alpha, so the best contributions right now are
feedback on the design ([DESIGN.md](DESIGN.md)), bug reports, ATS/job-board adapters, and
the milestones tracked in issues ([MILESTONES.md](MILESTONES.md)).

## Ground rules

1. **No personal data, ever.** Not in code, tests, fixtures, screenshots, logs, issues, or
   commit messages. Use the fictional persona **Alex Example** (`alex@example.com`,
   `+1 555-555-0100`). CI rejects real-looking emails and phone numbers.
2. **No secrets.** Credentials and browser sessions belong in a user's private sync repo or
   local vault, never here. CI runs gitleaks on every push.
3. **Truthfulness is a product rule.** Features must never invent facts on a user's behalf
   (see DESIGN.md section 9). Outreach emails are drafts only and are never auto-sent.
4. **MIT-compatible dependencies only.** `pnpm license-check` enforces an allowlist.
5. Be kind. See [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md).

## Development

Requirements: Node 22+ and pnpm 10.

```bash
pnpm install
pnpm --filter applyqueue dev   # run the desktop app with hot reload
pnpm check                      # lint, typecheck, tests, privacy scan, license check
pnpm dist                       # package for your OS into apps/desktop/release/
```

Layout:

```
apps/desktop/        Electron main, preload, and React renderer
packages/schema/     zod schemas for profile, preferences/categories, answers, accounts
templates/packs/     starter packs (fictional data only)
scripts/             privacy scan and license check used by CI
```

## Pull requests

- Keep PRs focused; link the issue.
- Add or update tests for logic changes (`vitest`).
- Run `pnpm check` before pushing.
- Commit messages: imperative mood, short subject ("Add Greenhouse source adapter").

## Optional: personal denylist for maintainers

`scripts/privacy-scan.mjs` can also block exact strings you never want committed (your own
name, email, address) without storing them anywhere in the repo. Put SHA-256 hashes in a
local file and run `PRIVACY_DENYLIST_FILE=~/my-denylist.txt pnpm privacy-scan`; generate a
hash with `node scripts/privacy-scan.mjs --hash "text"`. In CI the hashes come from the
`PRIVACY_DENYLIST_SHA256` repository secret.
