# NAME_SHORTLIST

**Picked: ApplyQueue** (2026-10-24). Domains and npm intentionally not registered.

Checked 2026-10-24 (about 10 PM PT) from the box, unauthenticated.
Re-run the checks on the day of the pick: domains and names move fast in this space.

## Method
- **GH user/org**: `GET api.github.com/users/<Name>`. 200 = taken, 404 = free.
- **Notable repos**: `search/repositories?q=<Name>+in:name`, sorted by stars. Shows the total count and the top stars.
- **JonathanLiu1401/<Name>**: `GET api.github.com/repos/...`. 404 = no public repo by that name. A private repo would also return 404 here, so the owner checked with an authenticated call (not found).
- **npm**: `registry.npmjs.org/<lowercase>`. 404 = name unclaimed. npm can still block names that look too similar to existing ones.
- **.com**: Verisign RDAP. **.app**: Google Registry RDAP.
  - 200 = registered. 404 = not registered.
  - A sanity check on a random name returned 404 on both, so a 404 means the domain looks free.
  - Premium or reserved pricing can't be seen through RDAP. Mark every 404 "likely free".
- **Clashes**: web search for products using the name in job/HR tech.
  - This was not a USPTO/EUIPO trademark search. Run TESS before committing to a name.

## Results

| Name | GH user/org | Repos named like it (top) | JonathanLiu1401/<name> | npm | .com | .app | Job/HR-tech clash (web) |
|---|---|---|---|---|---|---|---|
| AutoApply | taken | 967 (autoapply/autoapply 159 stars) | free | **taken** | taken | taken | Many auto-apply products and a GitHub org use the name. Generic |
| ApplyBot | taken | 69 | free | free | taken | taken | Severe: applybot.co, .ca, .cc and .ai are all AI auto-apply services |
| JobPilot | taken | 1,595 (jlifeng/JobPilot 146) | free | free | taken | taken | JobPilot (jobpilotapp.com) is an auto-fill extension |
| ApplyPilot | taken | 343 (Pickle-Pixel/ApplyPilot 1,755) | free | free | taken | taken | Severe: several commercial ApplyPilot products, plus a popular OSS repo |
| JobBot | taken | 518 | free | free | taken | taken | JobBot apply service; Jobot is a recruiting firm |
| JobAgent | taken | 389 | free | free | taken | taken | jobagent.sh, jobagent.work and getjobagent.com |
| ApplyAgent | free | 19 | free | free | taken | taken | applyagent.io and the ApplyAgent product at nolongerjobless.com |
| AutoApplier | free | 45 | free | free | taken | likely free | Severe: autoapplier.com is a direct AI job-agent competitor |
| JobApplier | taken | 77 | free | free | taken | taken | TheJobApplier is an autofill product |
| ApplyEngine | taken | 5 | free | free | taken | taken | ApplyEngine.ai job extension (now Destava) |
| OpenApply | taken | 36 | free | free | taken | taken | Severe: Faria's OpenApply admissions software; its terms claim the trade name |
| ApplyLoop (former placeholder) | free | 18 | free | free | taken | taken | **Severe: ApplyLoop is a paid AI auto-apply product (applyloop.pro, .app, .net). Must rename** |
| JobApplyBot | free | 11 | free | free | taken | likely free | Minor: a "Job Apply Bot" Chrome extension, last updated Jan 2024 |
| AutoJobApply | taken | 48 | free | free | taken | likely free | Generic phrase; no strong brand found |
| JobApplyAgent | free | 9 | free | free | taken | likely free | jobappagent.com, a similar but different name |
| ApplyWorker | free | 0 | free | free | taken | likely free | Weak: applyworker.com is an overseas job-listing site; ApplyWorks is a different name |
| **ApplyQueue** | **free** | 2 (0 stars) | free | **free** | **likely free** | **likely free** | Weak: only a Devpost hackathon project ("ApplyQueue"); JobWhat has an "apply queue" feature |

## Top 3

1. **ApplyQueue**: the only name free on every check (GitHub org, npm, .com, .app). It describes the core product: a queue that agents work through with next_job, skip_job and finish_job. The only clash is a hackathon project.
2. **ApplyWorker**: the GitHub org, npm and .app look free, with zero repos. It fits the "worker" model of the backends. The .com is taken by an unrelated listings site.
3. **JobApplyBot**: the most self-explanatory name for non-technical users. The GitHub org, npm and .app look free. The only clash is a stale Chrome extension. The .com is taken.

Avoid: ApplyLoop, ApplyBot, ApplyPilot, AutoApplier and OpenApply. Each is an active product in the same market or a claimed trade name.
