# Questionnaire: what the captain asks its user

Ask these a few at a time, in this order, in the user's private chat with the captain. Store each
answer where the last column says. Anything the user skips is recorded as `"not answered"` and
asked again the first time a form needs it. Never fill a default, never guess.

Sections A to H are facts. Section J is **policy rulings**: things one user decided for their own
team that another user may decide differently. This guide never answers them for the user.

**Never ask for a password in chat.** Ask the user to save logins in the Grok Bot vault (or, if
they prefer, the captain writes them into `{{STATE_DIR}}/secrets/credentials.txt` on the box,
mode 600, never shown in chat). If a password is pasted into chat anyway, do not repeat it.

Placeholders in the templates use the `{{KEY}}` in the first column. All examples are fake.

## A. Identity and contact (required)

| Key | Question | Example (fake) | Stored in |
|---|---|---|---|
| `FULL_NAME` | Full legal name as it should appear on applications? | Alex Example | profile.json `identity.legal_name` |
| `PREFERRED_NAME` | Preferred first name, if different? | Alex | `identity.preferred_name` |
| `PRONOUNS` | Pronouns to give if a form asks (or "decline")? | decline | `identity.pronouns` |
| `EMAIL` | Email for applications and new portal accounts? | alex@example.com | `identity.email` |
| `PHONE` | Phone number, and phone type if asked (mobile / home)? | +1 555-010-0199, mobile | `identity.phone`, `identity.phone_type` |
| `ADDRESS` | Mailing address (street, city, state, ZIP, country)? | 1 Example St, Springfield, ST 00000, US | `identity.address` |
| `LINKS` | LinkedIn, GitHub, portfolio or personal site URLs? (LinkedIn URLs are stored with `www.`; some portals reject them otherwise) | https://www.linkedin.com/in/alex-example | `links` |
| `AGE_18` | Are you at least 18? | Yes | `screening_answers.over_18` |

## B. Education (required for students and recent grads)

| Key | Question | Example (fake) | Stored in |
|---|---|---|---|
| `SCHOOL` | School / university full official name (portals search by the full name)? | Example State University | `education.school` |
| `SCHOOL_LOCATION` | City, state, country of the school? | Springfield, ST, US | `education.location` |
| `DEGREE` | Degree, major (and minor)? | BS Computer Engineering | `education.degree`, `.major`, `.minor` |
| `DEGREE_ALIASES` | If a dropdown lacks your exact major or degree, which nearest real options may agents pick, in order? | Computer Engineering > Computer Science and Engineering > Engineering | `education.major_dropdown_fallbacks` |
| `START_DATE` | Start month and year? | September 2023 | `education.start` |
| `GRAD_DATE` | Expected graduation month and year? | December 2026 | `education.graduation` |
| `SECOND_DEGREE` | Any second or combined degree (e.g. a planned master's)? Name, dates, status? | none | `education.second_education` |
| `GPA` | GPA and scale exactly as on your transcript, or "do not give" (then leave optional GPA fields blank and ask on required ones)? | 3.50 / 4.00 | `education.gpa` |
| `CLASS_STANDING` | Current class standing? | Junior | `education.standing` |
| `COURSEWORK` | Relevant courses to list when asked? | Digital Logic, Data Structures, Signals | `education.coursework` |
| `TRANSCRIPT` | Unofficial transcript PDF to upload when asked? Please send it. | transcript.pdf | `{{STATE_DIR}}/documents/` |

## C. Work eligibility and logistics (required)

| Key | Question | Example (fake) | Stored in |
|---|---|---|---|
| `WORK_AUTH` | Are you legally authorized to work in {{COUNTRY}}? | Yes | `work_authorization.authorized` |
| `SPONSORSHIP` | Will you require visa sponsorship now? In the future? (These are the exact answers agents give.) | No / No | `work_authorization.sponsorship_now`, `.sponsorship_future` |
| `VISA_STATUS` | Current visa or status, if a form asks (or "decline" where allowed)? | citizen | `work_authorization.visa_status` |
| `STUDENT_WORK_AUTH` | Do you rely on student work authorization (for example OPT, STEM OPT or CPT in the US)? Which type should agents pick when a form lists them? | not applicable | `work_authorization.student_work_auth` |
| `CITIZENSHIP` | Citizenship and whether you are a "US person" for export control (ITAR/EAR), or "ask me each time"? | ask me each time | `work_authorization.citizenship`, `.export_control_us_person` |
| `CLEARANCE` | Any security clearance? Willing to obtain one? | None / No | `work_authorization.security_clearance` |
| `ONSITE` | Onsite / hybrid / remote: what may agents say yes to? | Yes to all | `job_target.work_mode` |
| `RELOCATE` | Willing to relocate? Need relocation assistance? | Yes / No | `job_target.relocate`, `.relocation_assistance` |
| `LOCATIONS` | Preferred locations in priority order, and places to avoid? | Anywhere in US; prefer West Coast | `location_policy.priority` |
| `REMOTE_PREF` | How should remote roles rank against onsite ones? | below onsite | `location_policy.remote_rank` |
| `AVAILABILITY` | Availability: start and end dates (internships) or earliest start (full-time)? Which seasons? | 2026-06-01 to 2026-08-31, Summer only | `job_target.term`, `.start`, `.end` |
| `SALARY` | Desired pay if a form requires a number, or "negotiable" where free text is allowed? | 30 | `job_target.salary_numeric_fallback` |
| `SALARY_UNIT` | Is that number hourly or annual (and which currency)? | hourly, USD | `job_target.salary_unit` |
| `PREVIOUS_EMPLOYEE` | Have you ever worked for, or applied to, any company you might target? (Agents answer "previously employed here" questions from this.) | No | `screening_answers.previously_employed` |
| `RELATIVES` | Any relatives at target companies (some forms ask)? | No | `screening_answers.relatives_at_company` |
| `CONVICTIONS` | Background-check style questions: how to answer? | No | `screening_answers.background` |

## D. Targets

| Key | Question | Example (fake) | Stored in |
|---|---|---|---|
| `TARGET_ROLES` | Which role types and seasons? | Summer internships: embedded, test, data | `job_target.desired_titles_tier1` |
| `ROLE_FAMILIES` | Name each role family and its search keywords. | embedded: "firmware intern", "embedded intern"; data: "data intern" | `job_target.role_families` |
| `TIER1` | Tier 1 employers or kinds of employer (apply first)? | Example Robotics Co; mid-size device makers | company_tiers.json `tier1` |
| `TIER2` | Tier 2 (only when tier 1 is empty), or "none"? | none | `tier2` |
| `BANNED` | Employers never to apply to (include parent companies)? | Example Corp | `banned` |
| `BANNED_INDUSTRIES` | Industries never to apply to? | gambling | `banned_industries` |
| `EXCLUDED_ROLES` | Role types never to apply to, even if the form is easy? | sales, unpaid | `job_target.excluded_roles` |
| `COUNTRIES_OK` | Which countries may agents apply in? | US, Canada | `location_policy.countries` |
| `COMPANY_FILTERS` | Any company size or revenue floor? (Default: none.) | none | `company_tiers.json filters` |
| `DAILY_CAP` | Max applications per day overall? | 40 | `team.daily_cap` |
| `LINKEDIN_CAP` | Max LinkedIn Easy Apply per day (keep at or under about 30)? | 25 | `team.linkedin_daily_cap` |
| `BOARDS` | Which boards to use: LinkedIn, Indeed, Handshake, Glassdoor, company sites? | all | `team.boards` |

## E. Resumes and documents

| Key | Question | Example (fake) | Stored in |
|---|---|---|---|
| `RESUMES` | Please send each resume PDF. | 2 files | `{{STATE_DIR}}/resumes/` |
| `RESUME_MAP` | Which resume goes with which role family? Any title keyword that always forces one resume? Any research CV for research titles? | embedded -> Resume_Embedded.pdf; data -> Resume_Data.pdf; "research" in title -> CV.pdf | `documents.resume_map`, `documents.selection_rule` |
| `RESUME_DEFAULT` | Which resume when the role family is unclear? (If a mapped file is missing, agents stop and ask; they never silently fall back.) | Resume_Embedded.pdf | `documents.resume_map.default` |
| `COVER_LETTER` | Cover letters: write a tailored one each time from the resume and knowledge file, or use a base letter you send? | tailored | `documents.cover_letter_policy` |
| `HEAR_ABOUT` | "How did you hear about us": which options, in priority order? | company website > LinkedIn > job board | `screening_answers.how_did_you_hear_priority` |

## F. Voluntary self-identification (EEO)

Agents must still pick an option for each. "Decline to answer" is always acceptable.

| Key | Question | Example | Stored in |
|---|---|---|---|
| `EEO_GENDER` | Gender: your answer or decline? | decline | `demographics.gender` |
| `EEO_RACE` | Race / ethnicity (and Hispanic/Latino yes/no): your answer or decline? | decline | `demographics.race`, `.hispanic_latino` |
| `EEO_VETERAN` | Veteran status: your answer or decline? | not a veteran | `demographics.veteran` |
| `EEO_DISABILITY` | Disability status: your answer or decline? (Some forms also ask for a name and date; agents use today's date.) | decline | `demographics.disability` |
| `EEO_LGBTQ` | Sexual orientation / LGBTQ+ (some forms ask): your answer or decline? | decline | `demographics.orientation` |

## G. Accounts and inbox

| Key | Question | Example (fake) | Stored in |
|---|---|---|---|
| `WATCH_INBOX` | Which inbox receives confirmations? Gmail or Outlook? | alex@example.com (Gmail) | AGENTS.md, routines |
| `CONFIRMATION_LABEL` | Label or folder for confirmations, codes and resets? | J*bs | AGENTS.md, routines |
| `PORTAL_LOGINS` | Which job sites do you already have accounts on (LinkedIn, Indeed, Handshake, Workday tenants, others)? Please save each login in the vault. | LinkedIn via Google | vault (or `secrets/credentials.txt`) |
| `SSO_RULES` | For "Sign in with Google" or other single sign-on buttons, which account? Any site that uses your school's single sign-on (you sign in once, agents reuse the session)? | Google: alex@example.com; Handshake: school SSO | AGENTS.md |
| `PASSWORD_STORE` | Where should new portal passwords go: the vault (recommended) or the box-local file? | vault | AGENTS.md |
| `STATE_DIR` | Folder on the box for everything (default `/workspace/JobAutomation`)? | /workspace/JobAutomation | SETUP |

## H. Team size and schedule

| Key | Question | Default |
|---|---|---|
| `N_APPLY` | How many apply agents? | 2 |
| `M_SEARCH` | How many search agents? | 1 |
| `HOURS` | Hours the team may run (routines fire inside these)? | weekdays 8am to 8pm |
| `SUMMARY_TIME` | When do you want the daily summary? | 7pm weekdays |

## I. Free-text facts for essays (optional, but ask)

- Two or three projects you are proudest of: what you built, your part, a number that shows impact.
- Work, research or teaching experience not fully on the resume.
- Why this field? What do you want to learn next?
- Awards, leadership, languages, anything a recruiter should know.
- Things about the kinds of company you like (for "why us" answers).

These go into `knowledge.json` and, with the resume, are the only material for essays.

## J. Policy rulings (ask each; record the exact ruling)

Each answer goes into `profile.json` `rulings` (and the relevant line of AGENTS.md). If the user
says "you decide", propose the conservative option in brackets and get a yes.

| Key | Question | Conservative option |
|---|---|---|
| `SPONSORSHIP_GATE` | When a posting has generic "we may not sponsor" boilerplate, apply anyway or skip? | apply anyway; skip only if your situation is explicitly excluded |
| `OPT_GATE` | If a posting explicitly excludes your student work authorization (e.g. "no OPT/CPT"), skip? | skip |
| `EXPORT_GATE` | Postings requiring US citizenship, US-person status, ITAR/EAR access or clearance: skip, or apply when you qualify? | skip unless you confirmed you qualify |
| `TERM_GATE` | Which seasons or terms only (e.g. Summer only)? Skip postings for other terms even if dates overlap? | your seasons only |
| `RETURNING_TO_SCHOOL` | "Must be returning to school after the internship": what is the true answer, and skip if it is no? | answer truthfully; skip if no |
| `GRAD_POLICY` | With two possible dates (e.g. bachelor's and a planned master's), which single date for one-date forms? When to give both? | give the degree the role is for |
| `YEAR_IN_SCHOOL_MAP` | How to map your year to dropdowns that use different schemes (year 3 vs "Junior" vs "rising senior")? | the one that matches the next academic year if the form says "as of the internship" |
| `ACCOUNT_CREATION` | May agents create new portal accounts themselves (with `EMAIL`), and store the password? | yes, vault |
| `PASSWORD_RESET` | May agents use "Forgot password" on a portal when the stored password fails? | yes, once per portal |
| `NEVER_RESET_TENANTS` | Any portal or employer tenant where agents must never reset the password (for example one you also use by hand)? | none |
| `CAPTCHA_POLICY` | Visible captchas: may agents complete them the way a person would, or always ask you? (Solving services and bypasses are never used.) | ask me |
| `REFERENCES_POLICY` | References: give names (which), or "available on request", or ask each time? | ask each time |
| `ID_FIELDS` | Passport, national ID, SSN or date-of-birth fields: never fill, or fill only when required (from where)? | never fill; ask |
| `AUTONOMY` | How hands-off do you want to be? (a) agents ask only for missing facts, or (b) agents also check in before each new employer. | (a) |
| `WHO_DECIDES_CONDITIONAL` | For CONDITIONAL targets (possible knockout), should the captain rule from your rulings, or ask you each time? | captain rules from these rulings, asks on anything new |
| `ASSESSMENTS` | Timed online assessments and video interviews: always hand to you? | always hand to you |
| `UNPAID` | Unpaid roles? | skip |
| `SALARY_ANNUAL_ONLY` | If a form only accepts an annual number and your fallback is hourly: stop and ask, or convert? | stop and ask |

## Derived placeholders (no question needed)

| Key | Value |
|---|---|
| `USER_FIRST_NAME` | First word of `PREFERRED_NAME`, else of `FULL_NAME`. |
| `COUNTRY` | Country of `ADDRESS` unless the user says they are applying elsewhere (ask if unclear). |
| `CAPTAIN_NAME` | The captain agent's display name. |
| `K` | The agent's number (Apply 1, Apply 2, ... / Search 1, ...). |
| `APPLY_ROOM` | `Apply A` for the first half of apply agents, `Apply B` for the rest. |
| `AGENT_NAME` | The agent's exact display name, used with `jobq.py --agent`. |
