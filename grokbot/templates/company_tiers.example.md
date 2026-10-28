# company_tiers.json (example)

Copy into `{{STATE_DIR}}/company_tiers.json`. The captain owns it. Every employer name below is a
placeholder; fill the real lists from the user's answers to questionnaire section D and the
captain's rulings. This file never leaves the box.

- `tier1` / `tier2`: apply to tier 1 first; tier 2 only when the user allows it and tier 1 is
  empty. Kinds of employer ("any mid-size device maker") are fine as entries; search agents
  expand them into names and post them as candidates.
- `candidates`: employers a search agent found that are not tiered yet. The captain tiers them
  (or asks the user) before their postings are queued. Nothing moves into a tier automatically.
- `banned` and `banned_industries`: never queue, never apply, skip on sight. Include parent
  companies and known subsidiaries.
- `gate_rulings`: captain rulings on CONDITIONAL targets, so the same question is never asked
  twice (for example "posting says 'must be returning to school': user is, apply").
- `already_applied`: optional summary for searchers; the ledger is the real record.
- `boards`: known job-board endpoints per employer, so searchers can enumerate every opening.
  Public board APIs exist for some ATSs: Greenhouse
  `https://boards-api.greenhouse.io/v1/boards/<board>/jobs`, Lever
  `https://api.lever.co/v0/postings/<company>?mode=json`, Ashby
  `https://api.ashbyhq.com/posting-api/job-board/<board>`. Workday has no public API; search its
  site in the browser.

```json
{
  "tier1": ["Example Robotics Co", "any mid-size consumer device maker"],
  "tier2": [],
  "candidates": [
    {"company": "Example Sensors Inc", "found_by": "Search 1", "why": "embedded intern posting", "status": "awaiting captain"}
  ],
  "banned": ["Example Corp", "Example Corp subsidiaries"],
  "banned_industries": ["gambling"],
  "filters": {"revenue_floor": null, "min_headcount": null},
  "gate_rulings": [
    {"company": "Example Robotics Co", "line": "Must be available 12 weeks", "ruling": "user available 13 weeks: apply", "date": "2026-10-20"}
  ],
  "already_applied": [],
  "boards": {
    "Example Robotics Co": {"ats": "greenhouse", "board": "examplerobotics"},
    "Example Sensors Inc": {"ats": "workday", "site": "https://example.wd1.myworkdayjobs.com/External"}
  }
}
```
