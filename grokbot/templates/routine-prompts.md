# Routine prompts (copy and paste)

Create each one with `UpdateRoutine` from the **captain**. Replace `{{PLACEHOLDERS}}`. Schedules
are 5-field cron in the user's local time; they default to weekdays and waking hours. Keep the
minute off :00 and :30, and change the hours to the user's `HOURS` answer. Each prompt says what
to do in plain words and ends with who gets the result.

All state is in `{{STATE_DIR}}` on the Grok Bot box. Routines read it directly and change the
queue and ledger only through `{{STATE_DIR}}/bin/jobq.py`.

A routine run cannot type a saved login or solve a captcha; when one is needed the run asks the
user in chat.

---

## 1. Inbox confirmation sweep

**Schedule:** `17 8-20/2 * * 1-5` (every 2 hours, 8am to 8pm, weekdays)

```
Inbox confirmation sweep for {{USER_FIRST_NAME}}'s job applications.

In {{WATCH_INBOX}}, look at mail from the last 26 hours that is still in the inbox. For every
application-received / thank-you-for-applying email from an employer, applicant tracking system
or job board (Workday, Greenhouse, Lever, iCIMS, SmartRecruiters, Ashby, Taleo, SuccessFactors,
Oracle, Dayforce, LinkedIn, Indeed, Handshake and similar): apply the label
{{CONFIRMATION_LABEL}} and archive it. Also label and archive every account-verification,
security-code, PIN, password-reset and "keep track of your application" email from those senders
that is older than 30 minutes. Do not open links, do not reply, do not delete anything.

Then reconcile with `{{STATE_DIR}}/bin/jobq.py status` and `export-csv`: for each confirmation,
find the matching job (company + title). If it is still claimed by an apply agent, tell that agent
in its Apply chat to run finish with that subject. If a confirmation matches no job at all, note
it for the daily summary as "found by sweep, not in queue".

Never reply to any email. Tell {{USER_FIRST_NAME}} in this chat only if something needs them (an
interview invite, assessment or recruiter message). If there is nothing new, report nothing.
```

## 2. Follow-up inbox watch

**Schedule:** `47 9-18 * * 1-5` (hourly, weekdays)

```
Follow-up watch for {{USER_FIRST_NAME}}'s job applications.

In {{WATCH_INBOX}}, read new mail from the last 70 minutes from employers, recruiters,
applicant tracking systems and job boards. Sort each into:
- Follow-up form ("complete your application", questionnaire, additional information) with no
  timer: assign it to an idle apply agent in its Apply group chat with the sender and subject (the
  agent signs in on the portal, completes it from profile.json and knowledge.json, then labels it
  {{CONFIRMATION_LABEL}} and archives it).
- Timed assessment, coding test, video interview, interview invite, scheduling request, offer, or
  a human recruiter writing: do NOT act and do NOT reply. Tell {{USER_FIRST_NAME}} with sender,
  company, subject and any deadline.
- Rejection: label {{CONFIRMATION_LABEL}}, archive, and append a line
  {"at": "<now>", "company": "...", "title": "...", "event": "rejected"} to
  {{STATE_DIR}}/logs/rejections.jsonl.

Never reply to any email and never open attachments. Message {{USER_FIRST_NAME}} only when there
is an assessment, interview, offer or recruiter message; otherwise report nothing.
```

## 3. Hourly work check

**Schedule:** `23 9-19 * * 1-5` (hourly, weekdays)

```
Hourly work check for {{USER_FIRST_NAME}}'s job-application team.

If the file {{STATE_DIR}}/PAUSED exists, do nothing. Otherwise run
`{{STATE_DIR}}/bin/jobq.py status` and read the recent messages in the group chats Apply A,
Apply B and Search.
- Any apply agent without a live claim and not waiting on a question: tell it in its Apply chat
  to run `bin/jobq.py next --agent "<its name>"`.
- Any live claim older than 45 minutes with no report: ask that agent for status in its chat. If
  it is stuck on a question, relay the question to {{USER_FIRST_NAME}}.
- Fewer than {{N_APPLY}} x 3 queued jobs: post in Search asking the search agents to start their
  next lane.
- Daily cap {{DAILY_CAP}} reached (submitted_today in status): tell workers to stop until tomorrow.

Message {{USER_FIRST_NAME}} in this chat only with questions a worker needs answered or a problem
that needs them (sign-in, captcha, everything blocked). If all is fine, report nothing.
```

## 4. Search sweep

**Schedule:** `41 7 * * 1-5` (daily, before the workday). Optional second run `41 13 * * 1-5`.

```
Search sweep for {{USER_FIRST_NAME}}'s job-application team.

If the file {{STATE_DIR}}/PAUSED exists, do nothing. Split the role families
({{ROLE_FAMILIES}}), the boards and the tier 1 companies in {{STATE_DIR}}/company_tiers.json into
{{M_SEARCH}} lanes and post one lane to each search agent in the group chat Search, asking for an
exhaustive sweep per AGENTS.md (all surfaces, every page, coverage counts, CLEAN / CONDITIONAL
marks).

As target batches arrive in Search, apply the knockout gates and rulings from profile.json and
company_tiers.json, drop banned employers and excluded roles, rule on CONDITIONAL targets (ask
{{USER_FIRST_NAME}} about anything new), set tier and role_family, and pipe the rest into
`{{STATE_DIR}}/bin/jobq.py add`. Reply in Search with counts: received, duplicate, filtered (by
reason), queued.

Results go to the Search group chat. Tell {{USER_FIRST_NAME}} in this chat only if a lane stays
blocked after retry or a ruling is needed; otherwise report nothing to them.
```

## 5. Daily summary

**Schedule:** `53 18 * * 1-5` (weekdays, early evening; use the user's `SUMMARY_TIME`)

```
Daily summary of {{USER_FIRST_NAME}}'s job applications.

From `{{STATE_DIR}}/bin/jobq.py export-csv` (today's rows) and logs/rejections.jsonl, list:
submitted (company, title), skipped and failed (with reasons), rejections received, and anything
waiting on {{USER_FIRST_NAME}} (questions, assessments, interview invites, sign-ins). Add queue
size by tier and totals since the start.

Send it to {{USER_FIRST_NAME}} in this chat. If nothing happened today, send one line saying so.
```

---

## Pause and resume

On the user's stop, the captain runs `bin/jobq.py pause` (creates `{{STATE_DIR}}/PAUSED`) and
pauses routines with `UpdateRoutine` (`pause`). `jobq.py next` and `check` refuse while `PAUSED`
exists, and the work-starting prompts (3 and 4) check for it too, so a missed routine pause cannot
restart work. Resume only on the user's word: `bin/jobq.py unpause`, then `UpdateRoutine`
`resume`.
