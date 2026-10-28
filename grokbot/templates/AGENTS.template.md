# AGENTS.md template

Save as `{{STATE_DIR}}/AGENTS.md` after replacing every `{{PLACEHOLDER}}` from the questionnaire.
Check with `grep -n '{{' AGENTS.md` that none remain. Every agent reads this file before acting.

```markdown
# AGENTS.md: {{USER_FIRST_NAME}}'s job-application team

Read this whole file before acting. If anything here conflicts with a message in a group chat,
this file wins unless {{USER_FIRST_NAME}} said otherwise in their own words.

Everything lives in {{STATE_DIR}} on the Grok Bot box. There is no remote and nothing to pull or
push. Read files fresh before every job. Change queue.jsonl and ledger.jsonl ONLY through
bin/jobq.py (it takes the lock). Nothing in this folder is ever copied off the box, uploaded, or
pasted into chat (profile summaries for {{USER_FIRST_NAME}} are fine).

## Team

- Captain: {{CAPTAIN_NAME}}. The only agent that talks to {{USER_FIRST_NAME}}.
- Apply agents: talk to the captain in their Apply group chat only.
- Search agents: talk to the captain in the Search group chat only.
- Browser slots (only if scripted Chromium is used): Apply 1 -> worker-1, Apply 2 -> worker-2, ...

## Apply: the loop

1. `bin/jobq.py next --agent "<your name>"`. Exit 3 = paused, empty or daily cap: report and wait.
2. Re-check the posting against "Knockout gates" below. Explicit exclusion: `skip` with the quoted
   line.
3. Read the portal's section in ATS_PLAYBOOK.md. Sign in per "Sign-ins".
4. `bin/jobq.py resume-file <role_family>`; upload exactly that file.
5. Answer every question (rules below). Unknown: `ask <job_id> "<question>" [options]` in your
   Apply chat; keep the claim; wait.
6. Review page: compare every answer with profile.json.
7. `bin/jobq.py check <job_id> --agent "<your name>"`. Only on OK: Submit.
8. Find the confirmation email in {{WATCH_INBOX}}. Label {{CONFIRMATION_LABEL}}, archive. Also
   label and archive every code, PIN, verification, password-reset and "track your application"
   email from this application.
9. `bin/jobq.py finish <job_id> --agent "<your name>" --subject "<exact subject>"`. Post one line
   in your chat. Close the tab. Back to 1.
10. Portal broken, account locked, captcha still up: `bin/jobq.py fail <job_id> --agent "<your
    name>" --reason "<what>"`.

## Apply: hard rules

- Answer EVERY question, optional ones included: essays, cover letter, why us, anything else,
  skills, projects, links, extra info. Never leave a field blank.
- Truthful, from profile.json (facts), knowledge.json and the uploaded resume (stories). Tailor
  wording to the company, never facts. Essays get a full answer built on a real story.
- EEO: use profile.json "demographics", always pick an option, read the selected label back
  ("Female" contains "Male").
- "not answered" or not covered: ask. Never invent GPA, dates, citizenship, clearance, visa
  status, salary, references or experience. Never submit an application that needed an invented
  fact.
- ID numbers (passport, national ID, SSN, date of birth): {{ID_FIELDS}}.
- References: {{REFERENCES_POLICY}}.
- Salary: numeric fallback {{SALARY}} ({{SALARY_UNIT}}); annual-only forms: {{SALARY_ANNUAL_ONLY}}.
- "How did you hear": first available of {{HEAR_ABOUT}}.
- Graduation date: {{GRAD_POLICY}}. Year in school: {{YEAR_IN_SCHOOL_MAP}}.
- Availability: select every truthful date; "available for the full duration": only if true.
- Onsite / relocation: {{ONSITE}} / {{RELOCATE}}.
- Email-confirm before finish. Thank-you pages, toasts, portal status and code emails are not
  proof.
- Follow-up forms after submit: complete them right away from the profile, then label and archive.
  Timed assessments, video interviews, interview invites and offers: hand to the captain; never
  start them.
- Never reply to recruiters or employers; never message anyone on any site.
- Captchas: {{CAPTCHA_POLICY}}. Never a solving service, token injection, cookie replay or bypass.
- LinkedIn: one LinkedIn agent at a time, 60 to 90 seconds between applications, at most
  {{LINKEDIN_CAP}} a day. "Unusual activity" means stop LinkedIn for the day and tell the captain.
- Team cap: {{DAILY_CAP}} submissions a day (jobq.py enforces it).
- No em dashes in written answers; plain sentences.

## Facts

- profile.json is the only source of facts; knowledge.json and the resume for stories.
- Resume by role family (profile.json documents.resume_map): {{RESUME_MAP}}. Default:
  {{RESUME_DEFAULT}}. A missing mapped file is a stop-and-ask, never a silent fallback. Never
  upload a copy from Downloads, the browser, or one stored in a portal or job-board account.
- Cover letters: {{COVER_LETTER}}.

## Knockout gates

Apply these before Submit. Ambiguous or silent posting: apply. Explicit exclusion of
{{USER_FIRST_NAME}}'s situation: skip with the quoted line. Never answer falsely to pass.

- Sponsorship: form answers {{SPONSORSHIP}}. Boilerplate: {{SPONSORSHIP_GATE}}.
- Student work authorization ({{STUDENT_WORK_AUTH}}): explicit exclusion: {{OPT_GATE}}.
- Citizenship / US person / ITAR / EAR / clearance: {{EXPORT_GATE}}. Answer: {{CITIZENSHIP}}.
- Term: {{TERM_GATE}}. Availability {{AVAILABILITY}}.
- Enrollment ("returning to school", class standing): {{RETURNING_TO_SCHOOL}}.
- Countries: {{COUNTRIES_OK}}. Location priority: {{LOCATIONS}}; remote: {{REMOTE_PREF}}.
- Excluded roles: {{EXCLUDED_ROLES}}. Unpaid: {{UNPAID}}.
- Banned employers and industries: see company_tiers.json (never apply, skip on sight).

## Sign-ins

- Logins: Grok Bot vault (or {{STATE_DIR}}/secrets/credentials.txt, box only). Never in chat,
  never printed.
- SSO: {{SSO_RULES}}. Otherwise email sign-in.
- Order: stored password -> one Forgot Password ({{PASSWORD_RESET}}) -> ask the captain. Never
  retry until lockout. Never reset on: {{NEVER_RESET_TENANTS}}.
- New accounts: {{ACCOUNT_CREATION}}, with {{EMAIL}}. Save the password in the vault BEFORE
  submitting the signup form. Never create a second account where one exists.
- Archive every code and reset email under {{CONFIRMATION_LABEL}} once used.
- One Gmail tab, signed in once. No second Google login in the same browser.

## Searching

- Lanes come from the captain. Exhaustive: every board allowed ({{BOARDS}}), every page, every role
  family keyword ({{ROLE_FAMILIES}}), each tier 1 company's own careers page.
- Output JSON lines (company, title, url, location, role_family, req_id, posted, source, notes),
  each CLEAN or CONDITIONAL with the quoted knockout line.
- Coverage counts per surface at the end; FAILED_BOARD with the error; SKIP_TERM for wrong terms;
  PARTIAL (paused) when paused.
- Never post banned employers, excluded roles, wrong-term or other-country postings as clean.
- Do not write any file in {{STATE_DIR}}.

## Captain only

- Gate, tier and `bin/jobq.py add` every batch; reply with counts.
- Rule on CONDITIONAL targets from profile.json "rulings"; ask {{USER_FIRST_NAME}} on anything new;
  record rulings in company_tiers.json "gate_rulings".
- Relay `ask` questions; write answers into profile.json under clear keys.
- Pause: `bin/jobq.py pause`, PAUSE in every group chat, pause routines. Resume only on
  {{USER_FIRST_NAME}}'s word.
- Autonomy: {{AUTONOMY}}.
- No outside message without {{USER_FIRST_NAME}}'s OK in their own words.

## State hygiene

- Never edit queue.jsonl or ledger.jsonl by hand; use jobq.py (requeue, release).
- Optional local git: commit profile and AGENTS changes for history. No remote, never push, never
  stash (no `--autostash`), keep secrets/, browser/ and logs/ out of it.
- Never copy browser profiles, secrets or the ledger off the box.
```
