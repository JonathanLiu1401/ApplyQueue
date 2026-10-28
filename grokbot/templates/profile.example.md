# profile.json (example)

Copy the block into `{{STATE_DIR}}/profile.json` and fill it from the questionnaire. Every value
here is fake. Anything the user did not answer stays `"not answered"`; agents treat that as
"stop and ask", never as a value to type.

Rules for agents:

- This file is the only source of facts for forms. Read it fresh before every job.
- `screening_answers` collects answers to recurring questions. When the user answers a new
  question, the captain adds it here under a clear key (the question text itself is fine as a
  key) so nobody asks twice.
- `rulings` holds the policy answers from questionnaire section J.
- `hard_rules` are read by every agent and override convenience.

```json
{
  "identity": {
    "legal_name": "Alex Example",
    "preferred_name": "Alex",
    "pronouns": "decline",
    "email": "alex@example.com",
    "phone": "+1 555-010-0199",
    "phone_type": "mobile",
    "address": {
      "street": "1 Example St",
      "city": "Springfield",
      "state": "ST",
      "postal_code": "00000",
      "country": "United States"
    }
  },
  "links": {
    "linkedin": "https://www.linkedin.com/in/alex-example",
    "github": "https://github.com/alex-example",
    "portfolio": "https://alex.example.com"
  },
  "education": {
    "school": "Example State University",
    "location": "Springfield, ST, United States",
    "degree": "Bachelor of Science",
    "major": "Computer Engineering",
    "minor": "not answered",
    "major_dropdown_fallbacks": ["Computer Engineering", "Computer Science and Engineering", "Engineering"],
    "start": "2023-09",
    "graduation": "2026-12",
    "standing": "Junior",
    "gpa": "3.50",
    "gpa_scale": "4.00",
    "coursework": ["Digital Logic", "Data Structures", "Signals and Systems"],
    "graduation_policy": "Give 2026-12 on one-date forms.",
    "second_education": null
  },
  "work_authorization": {
    "authorized": "Yes",
    "sponsorship_now": "No",
    "sponsorship_future": "No",
    "visa_status": "not answered",
    "student_work_auth": "not applicable",
    "citizenship": "ask me each time",
    "export_control_us_person": "ask me each time",
    "security_clearance": "None",
    "willing_to_obtain_clearance": "not answered"
  },
  "demographics": {
    "gender": "Decline to self-identify",
    "hispanic_latino": "Decline to self-identify",
    "race": "Decline to self-identify",
    "veteran": "I am not a protected veteran",
    "disability": "I do not wish to answer",
    "orientation": "Decline to self-identify"
  },
  "job_target": {
    "term": "Summer 2026",
    "start": "2026-06-01",
    "end": "2026-08-31",
    "role_families": {
      "embedded": ["embedded intern", "firmware intern"],
      "data": ["data intern", "data engineering intern"]
    },
    "desired_titles_tier1": ["Embedded Software Intern", "Firmware Intern"],
    "desired_titles_tier2": ["Data Engineering Intern"],
    "excluded_roles": ["sales", "unpaid"],
    "work_mode": "Yes to onsite, hybrid and remote",
    "relocate": "Yes",
    "relocation_assistance": "No",
    "salary_numeric_fallback": 30,
    "salary_unit": "hourly"
  },
  "location_policy": {
    "countries": ["United States"],
    "priority": ["West Coast", "anywhere in the US"],
    "remote_rank": "below onsite"
  },
  "documents": {
    "resume_map": {
      "embedded": "resumes/Resume_Embedded.pdf",
      "data": "resumes/Resume_Data.pdf",
      "default": "resumes/Resume_Embedded.pdf"
    },
    "selection_rule": "Pick by the card's role_family. If the title contains 'research', use the CV if one is mapped. If the mapped file is missing, stop and ask; never fall back silently.",
    "transcript": "documents/transcript.pdf",
    "cover_letter_policy": "tailored"
  },
  "common_free_text": {
    "summary": "Computer engineering student who likes building small embedded systems end to end.",
    "why_this_field": "not answered",
    "additional_info": "not answered"
  },
  "screening_answers": {
    "over_18": "Yes",
    "previously_employed": "No",
    "relatives_at_company": "No",
    "background": "No",
    "available_full_duration": "Yes",
    "willing_onsite": "Yes",
    "how_did_you_hear_priority": ["Company website", "LinkedIn", "Job board"],
    "non_compete": "No"
  },
  "references": {
    "policy": "ask each time"
  },
  "rulings": {
    "sponsorship_gate": "apply anyway on boilerplate; skip only on explicit exclusion",
    "opt_gate": "not applicable",
    "export_gate": "skip unless confirmed",
    "term_gate": "Summer only",
    "returning_to_school": "Yes",
    "year_in_school_map": "Junior now; 'rising senior' for forms asking about the internship period",
    "account_creation": "yes, vault",
    "password_reset": "yes, once per tenant",
    "never_reset_tenants": [],
    "captcha_policy": "ask me",
    "id_fields": "never fill; ask",
    "autonomy": "ask only for missing facts",
    "conditional_targets": "captain rules from these rulings, asks on anything new",
    "assessments": "hand to user",
    "unpaid": "skip",
    "salary_annual_only": "stop and ask"
  },
  "team": {
    "daily_cap": 40,
    "linkedin_daily_cap": 25,
    "boards": ["LinkedIn", "Indeed", "Handshake", "Glassdoor", "company sites"]
  },
  "hard_rules": [
    "Never falsify citizenship, work authorization, veteran or disability status, GPA, dates, or experience.",
    "Never submit an application that needed an invented fact; ask instead.",
    "Never mark a job submitted without the confirmation email.",
    "Answer every question, optional ones included.",
    "Never use a captcha-solving service, token injection or cookie replay.",
    "Never create a second account on a portal that already has one; recover the existing one.",
    "Never fill passport or national ID numbers unless the user ruled otherwise.",
    "Never give references without asking the user.",
    "Never reply to recruiters or message anyone.",
    "Log every attempt in the ledger with jobq.py."
  ]
}
```
