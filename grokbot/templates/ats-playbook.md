# ATS and job-board playbook

Field-tested quirks, generalized. Apply agents read the section for the portal on the card before
starting. When a portal behaves differently, the agent reports it to the captain, who adds a line
here (in `{{STATE_DIR}}/AGENTS.md` or a local copy of this file).

General browser habits that prevent most failures:

- Take a fresh look at the page (snapshot or screenshot) before each click and after each
  step. The screenshot is the check, not the agent's memory of what it typed.
- A dropdown is not answered until the chosen option shows as selected. Typing text into a
  searchable dropdown is not a selection; click the option.
- Press Escape to close a leftover open dropdown before moving on; it often covers the next field.
- Date fields: focus, select all, type, then Tab out so the field commits. Segmented dates
  (MM / DD / YYYY boxes) are typed one segment at a time.
- Read the Review page before Submit and compare every answer with `profile.json`.
- Submit only from the final Review step, and only after `jobq.py check` returns OK.

## Accounts and passwords (every ATS)

- One account per portal (per employer tenant for Workday, Oracle, iCIMS, SuccessFactors,
  Taleo). **Never create a second account** where one exists; recover the existing one.
- Before signing in, look up the stored login for that exact tenant in the Grok Bot vault (or
  `secrets/credentials.txt`). Order: stored password, then "Forgot password" once (if
  `rulings.password_reset` allows and the tenant is not in `never_reset_tenants`), then ask the
  captain. Never retry a wrong password until the account locks.
- New account (only if `rulings.account_creation` allows): use `{{EMAIL}}`, generate a strong
  unique password, save it in the vault **before** submitting the signup form, then verify the
  email. If the user chose box storage, append to `secrets/credentials.txt`:

  ```
  [workday:example.wd1]  login=alex@example.com  password=<stored, never shown in chat>  created=2026-10-27
  ```

- After a reset, save the new password right away, and archive the reset email under
  `{{CONFIRMATION_LABEL}}`.
- Use "Sign in with Google" only where the user chose SSO for that site, with the account they
  named. Use email sign-in otherwise.
- Never print passwords, codes, cookies or session tokens in chat or logs.
- Email codes: read the newest code email, type it, then archive that email. Some portals use
  one box per character: type one character into each box. If attempts are exhausted, do not keep
  re-requesting; `fail` with `account` and tell the captain.

## Workday

- Start: "Apply" then **Apply Manually**. For a second job at the same employer, **Use My Last
  Application** reuses the saved profile.
- Sign in with email and password, not SSO, unless the user's login for that tenant is SSO.
- Wrong password: one Forgot Password, set a new one, store it. Never create a new account on a
  tenant that already has one.
- Honeypot: some tenants include a hidden field meant to catch bots (often with an odd name, not
  visible on screen). Never fill a field a person cannot see.
- Resume autofill sometimes fails with a server error. Reload once and continue manually; do not
  loop on autofill.
- Address and other fields can be locked by the saved profile; edit them in the profile section
  if possible. Never delete the candidate profile to get around it.
- **Degree** is a button listbox: click it, then click the option. Labels vary by tenant
  ("Bachelor's Degree", "Bachelors", "BS"); pick the one that means the user's degree.
- **School** search needs the full official name; try the full name before abbreviations.
- **Field of study** is a virtualized list: scroll the list and click the exact item. Typing does
  not select it. If the exact major is missing, use `education.major_dropdown_fallbacks` in order.
- **How did you hear about us** is often a two-level pill: pick the parent, then the child, in the
  priority order from `screening_answers.how_did_you_hear_priority`.
- **Location** typeahead: type the city, wait for suggestions, press Enter or click to commit.
- Date sections (education, experience): focus each segment, select all, type, Tab.
- Yes/No questions are listbox options, not radio buttons; confirm the selected text.
- **Self-identify** page: the date field needs a blur (Tab) to save; gender and other options
  must be exact clicks ("Female" contains "Male").
- Some answers reveal extra questions (residency, export control, sponsorship detail). Scroll and
  answer the newly revealed ones before Next.
- LinkedIn URL fields often require the `www.` form.
- Salary: if a numeric field is required, give `job_target.salary_numeric_fallback` and pick the
  matching currency and period. If only annual is accepted and the fallback is hourly, follow
  `rulings.salary_annual_only` (default: stop and ask, do not convert).
- "My Applications: Under Review" in the portal is not a confirmation; wait for the email.

## Greenhouse

- Fields are mostly plain inputs; custom questions sit at the bottom. Answer all of them.
- Some employers send an email verification code before Submit. The code has several
  characters, one input box per character: type them one per box.
- "Discipline" or major dropdowns may not include the exact major; pick the nearest real option
  from `major_dropdown_fallbacks`, never an unrelated one.
- Location fields use autocomplete: pick the suggested entry.
- Confirmations often come from a Greenhouse mail domain with "Thank you for applying" in the
  subject.

## Lever and Ashby

- Single page. Resume upload may autofill fields incorrectly: check every autofilled value
  against the profile.
- Additional-information boxes are optional but always answered (short tailored paragraph).

## Oracle (Cloud Recruiting / Taleo)

- Email-first flow: enter the email, receive a PIN code by email, read it, type it, archive the
  email. If PIN attempts are exhausted, do not re-request repeatedly; `fail` with `account`.
- Multi-page wizard; each page must be saved before Next.

## iCIMS, SuccessFactors, Avature

- Usually require an account per tenant: follow the account rules above.
- Avature and iCIMS sometimes have a sign-in wall in front of the posting; sign in, then return
  to the posting URL.

## Dayforce and other portals without clear confirmation pages

- The proof is the notification email. Wait for it before `finish`.

## Company email applications

- Some postings say "send your resume to ...". Sending an email is an outside message: the
  captain asks the user first, with the exact text. The proof is the recruiter's reply or receipt.

## LinkedIn

- Sign in once in the box browser (SSO if the user chose it). One browser and one LinkedIn
  worker only: **never run LinkedIn on two agents at once**. A ban costs the user their network.
- Pace: one application every 60 to 90 seconds, at most `team.linkedin_daily_cap` (about 30) a
  day. No scraping, no bulk messaging, no connection requests, no messages to recruiters.
- Search URLs: `https://www.linkedin.com/jobs/search/?keywords=<kw>&location=<loc>&f_E=1&f_TPR=r604800`
  (`f_E=1` internship level, `f_TPR=r604800` past week, add `&f_AL=true` for Easy Apply only).
- **Easy Apply** stays on LinkedIn: answer every question, upload the card's resume (not one stored
  on LinkedIn), Review, Submit. The "application sent" toast is not proof; wait for the email.
- "Apply" (not Easy) leaves LinkedIn: follow the employer's ATS section; the job counts as that ATS.
- A puzzle or verification challenge: skip that posting (`fail --reason captcha`) unless
  `rulings.captcha_policy` says to complete it.
- "Unusual activity" or a restriction warning: stop LinkedIn for the day and tell the captain, who
  tells the user.

## Indeed

- Search URL: `https://www.indeed.com/jobs?q=<kw>&l=<loc>&fromage=14`.
- Detail pane is on the right: scroll the pane, not the result list.
- "Easily apply" applies on Indeed (often in a new tab); "Apply on company site" redirects to the
  employer ATS.
- Indeed blocks plain HTTP clients from datacenter IPs (403). Use the signed-in browser, not a
  script.
- Cloudflare check: go to the home page, then re-open the URL. Do not loop. Never use a solving
  service.
- Never trust a resume already stored on Indeed; upload the card's file.

## Glassdoor

- Same patterns as Indeed (easy apply vs company site). Use the browser.

## Handshake

- Usually signed in with the school's single sign-on: the user signs in once in the box browser,
  agents reuse the session. If the session expires, ask the captain to get the user to sign in.
- Use the browser only, no scraping. Easy Apply stays on Handshake; otherwise follow the company
  site.
- Handshake often lists the same job under its own id; send the employer's `req_id` so it dedupes
  with the company-site posting.
- If a posting is not visible or not applicable for the user's school account, skip it with that
  reason.
- Handshake is often the best channel for student roles: give its targets priority when the user
  says so.

## Captchas (all sites)

- Never use a captcha-solving service, token injection, cookie replay or any bypass.
- Visible checkbox or image challenge: follow `rulings.captcha_policy` (complete it as a person
  would, or ask the user). Still blocked: `fail --reason captcha`.

## Search agents: coverage and output

- Exhaustive within the lane: every page of results, every listed location, every role family
  keyword. Report coverage counts: boards checked, queries run, postings seen, kept, rejected
  (with reasons).
- Output one JSON target per line (format in [jobq.md](jobq.md)), marked `CLEAN` or `CONDITIONAL`
  with the quoted posting line that might be a knockout.
- Dedupe slugs and ids must be whole URL tokens (e.g. the full requisition id), never a prefix.
- A board that errors or blocks (403, Cloudflare): report it as `FAILED_BOARD` with the error,
  never as "no results".
- Never post banned employers, excluded role types, wrong-term postings (report as `SKIP_TERM`)
  or other-country postings as clean.
- When paused mid-sweep, report `PARTIAL (paused)` with what was covered.
