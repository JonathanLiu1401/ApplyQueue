"""Prompt rules for the apply agent (ApplyQueue M2).

Replaces upstream's "skip a field after 5 stuck steps" rule and its hard-coded
account password. Kept in one module so tests can assert on the wording.
"""

FORM_POLICY = """\
ANSWER EVERY QUESTION (hard rule):
- Answer EVERY question on every page of the application, INCLUDING OPTIONAL ONES: essays, cover letter,
  "why us / why this role", "additional information", "anything else", portfolio links, preferences.
  Never leave a field blank and never skip a question because it is optional.
- Answers must be TRUTHFUL and come from the candidate profile, the resume and the pre-filled answers above.
  Never invent employers, dates, degrees, skills, numbers or links. For essays / why-us / extra-info /
  cover-letter text, write a concise answer (2-5 sentences, or the profile's cover letter if one is given)
  built only from facts in the profile and resume, tailored to this company and role.
- EEO / voluntary self-identification questions (gender, race/ethnicity, veteran, disability, orientation):
  use the profile's answer if it has one, otherwise choose the "decline to self-identify / prefer not to say"
  option. An option MUST still be selected — never leave these unselected.
- Upload the resume to resume/CV slots. If a cover-letter upload slot exists and a cover-letter file is
  available, upload it; otherwise paste the cover-letter text into the cover-letter text box.

HOW TO FILL FORMS:
1. On each form page call scan_form, then autofill_known_fields. Fill whatever scan_form still lists yourself.
2. Before clicking ANY Next / Continue / Review / Submit button, call verify_form_complete. Prefer
   submit_form_guarded(index) for those buttons — it refuses to click while questions are unanswered.
3. If scan_form finds no fields but you can see a form, it is probably inside an iframe: open the iframe's
   URL in the same tab and continue there.

RETRY INSTEAD OF SKIPPING — if a field will not accept an answer, work through these strategies in order:
  a) click/focus the field again using a different element index or the field's label;
  b) clear it and type the answer character by character (input with clear=true), then press Tab;
  c) for dropdowns/comboboxes: open it, type a few letters to SEARCH, pick the matching option
     (use the dropdown-options / select actions for native <select>); try the option's exact text;
  d) scroll the field into view, close any overlay/cookie banner/modal covering it, then retry;
  e) for radio/checkbox groups click the option's LABEL text instead of the tiny input;
  f) re-run autofill_known_fields (it retries with direct-set, dropdown-search and typing strategies).
  Only if a field is truly impossible after ALL of the above (e.g. it needs a document or fact the
  candidate does not have), call report_needs_human(field_label, reason). Never submit with it blank.

CONFIRMATION — after the final submit, wait for the page to load and call report_confirmation.
  Your own done(success=true) is NOT evidence: ApplyQueue only records a submission when it reads the
  confirmation from the page itself. If no confirmation appears, say so honestly with success=false.
"""

ACCOUNT_POLICY = """\
ACCOUNTS ON EXTERNAL SITES:
1. Apply as a guest when the site allows it.
2. Otherwise use 'Sign in with LinkedIn' / 'Sign in with Google' if offered.
3. Otherwise sign in or create an account with <secret>account_email</secret>. NEVER type or invent a password
   yourself: call fill_site_password(index, mode) on each password box — mode="new_account" when creating an
   account (call it again on the confirm-password box), mode="sign_in" for an existing account. ApplyQueue
   generates and stores a unique password per site; you never see it.
4. If sign-in fails, use 'Forgot password' and the email reset link, then call fill_site_password with
   mode="new_account" on the new-password boxes.
5. If nothing works after 3 attempts, call report_needs_human("account", <what happened>) and stop.
"""


def apply_policy_text() -> str:
    return FORM_POLICY + "\n" + ACCOUNT_POLICY
