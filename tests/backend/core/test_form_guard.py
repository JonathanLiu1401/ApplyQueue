"""Unit tests for form_guard planning (no browser)."""
from core import form_guard as fg


def F(label, kind="text", **kw):
    return fg.Field(key="k", kind=kind, label=label, **kw)


def test_classify():
    assert fg.classify(F("Gender")) == "eeo"
    assert fg.classify(F("Are you a protected veteran?", "radio")) == "eeo"
    assert fg.classify(F("Cover letter", "textarea")) == "cover_letter"
    assert fg.classify(F("Why Example Corp?")) == "essay"
    assert fg.classify(F("Resume/CV", "file")) == "resume"
    assert fg.classify(F("City")) == "generic"


def test_unanswered_counts_optional_but_not_optional_misc_files():
    fields = [F("Why us?", "textarea"), F("Portfolio attachment", "file"),
              F("Resume", "file", required=True), F("Name", empty=False)]
    assert [f.label for f in fg.unanswered(fields)] == ["Why us?", "Resume"]
    assert [f.label for f in fg.unanswered(fields, include_optional=False)] == ["Resume"]


def test_eeo_without_answer_picks_decline():
    opts = [{"label": "Select..."}, {"label": "Yes"}, {"label": "No"},
            {"label": "I don't wish to answer"}]
    assert fg.plan_answer(F("Disability status", "select", options=opts), {}, {}) == "I don't wish to answer"


def test_eeo_uses_profile_answer_when_given():
    opts = [{"label": "Female"}, {"label": "Male"}, {"label": "Decline to self-identify"}]
    got = fg.plan_answer(F("Gender", "select", options=opts), {"eeo": {"gender": "Female"}}, {})
    assert got == "Female"


def test_answer_bank_fuzzy_and_options():
    qa = {"Will you require visa sponsorship?": "No"}
    opts = [{"label": "Yes"}, {"label": "No"}]
    assert fg.plan_answer(F("Will you require visa sponsorship", "radio", options=opts), {}, qa) == "No"


def test_profile_answers_and_unknown_essay():
    p = {"name": "Alex Example", "email": "alex@example.com"}
    assert fg.plan_answer(F("First name"), p, {}) == "Alex"
    assert fg.plan_answer(F("Last name"), p, {}) == "Example"
    assert fg.plan_answer(F("Email address"), p, {}) == "alex@example.com"
    # Essays the bank does not cover are composed by the agent, not invented here.
    assert fg.plan_answer(F("Why do you want this role?", "textarea"), p, {}) is None


def test_accuracy_attestation_ticked_but_not_consents():
    assert fg.plan_answer(F("I certify the information above is true", "checkbox"), {}, {}) == "true"
    assert fg.plan_answer(F("I agree to the Terms of Service", "checkbox"), {}, {}) is None
    assert fg.plan_answer(F("Send me marketing emails", "checkbox"), {}, {}) is None


# ── M8 regressions found by the mock-ATS e2e suite ───────────────────────────

def test_bank_containment_is_whole_words_not_substrings():
    qa = {"Are you open to relocation?": "Yes"}
    assert fg.answer_bank_lookup("Location", qa) is None          # "location" is inside "relocation"
    assert fg.answer_bank_lookup("Are you open to relocation? *", qa) == "Yes"
    assert fg.answer_bank_lookup("Why do you want to work here", {"why do you want to work": "Because"}) == "Because"


def test_essay_never_gets_a_bare_keyword_yes_no():
    p = {"willing_to_relocate": True}
    essay = fg.Field(key="f1", kind="textarea", label="Tell us about your relocation timeline")
    assert fg.plan_answer(essay, p, {}) is None
    radio = fg.Field(key="g:r", kind="radio", label="Are you open to relocation?",
                     options=[{"value": "Yes", "label": "Yes"}, {"value": "No", "label": "No"}])
    assert fg.plan_answer(radio, p, {}) == "Yes"


def test_work_authorization_question_mentioning_country_is_not_an_address():
    p = {"work_authorization": True, "address": {"country": "United States of America"}}
    f = fg.Field(key="g:a", kind="radio",
                 label="Are you legally authorized to work in the country for which you are applying? *",
                 options=[{"value": "Yes", "label": "Yes"}, {"value": "No", "label": "No"}])
    assert fg.plan_answer(f, p, {}) == "Yes"
