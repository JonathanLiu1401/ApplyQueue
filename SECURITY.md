# Security policy

ApplyQueue handles sensitive things on your behalf: job-portal passwords, browser sessions,
email access, and your personal profile. We take reports seriously.

## Reporting a vulnerability

**Please do not open a public issue.** Report privately via
[GitHub Security Advisories](https://github.com/JonathanLiu1401/ApplyQueue/security/advisories/new).
Include steps to reproduce and the affected version. Never include real credentials or
personal data in a report; use the fictional "Alex Example" persona.

We aim to acknowledge reports within 7 days. ApplyQueue is pre-alpha and has no stable
release yet; fixes land on `main`.

## Where your data lives (by design)

| Data | Where | Never |
| --- | --- | --- |
| Profile, answers, queues | Your computer (app data folder) | In this public repo |
| Portal passwords, browser sessions | Your **private** sync repo on your GitHub account (default), mirrored in an OS-encrypted local vault | In this public repo, in logs, in AI prompts |
| Email access | OAuth tokens in the local vault (Gmail/Outlook re-auth every 7 days in testing mode) | Shared with any AI backend |

- The app verifies your sync repo is **private** through the GitHub API before every
  credential write and refuses to write if it is public or the check fails.
- AI backends receive secrets only through the `fill_secret` tool, which types the value into
  the browser field without returning it to the model.
- Encrypting credential files inside the private sync repo is a planned option, not yet a
  default.

## Scope

In scope: the desktop app, the ApplyQueue MCP server, CI configuration, and anything that
could leak secrets or personal data. Out of scope: vulnerabilities in third-party job sites
or AI providers (report those to them).

## Repository hygiene

Every push and pull request runs [gitleaks](https://github.com/gitleaks/gitleaks) over the
full history plus a personal-data scan (`scripts/privacy-scan.mjs`) that rejects real email
addresses, phone numbers, browser-profile files, cookie/password stores, and databases.
