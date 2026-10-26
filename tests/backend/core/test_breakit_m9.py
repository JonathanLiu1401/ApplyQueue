"""M9 break-it: profile answers must never put the candidate's own details in someone else's field,
and wrong-typed profile/settings values must not crash or silently corrupt the app."""
import pytest

from core import config
from core.form_guard import Field, plan_answer

P = {
    "name": "Alex Example", "email": "alex@example.com", "phone": "+1 555-0100", "phone_country_code": "+1",
    "address": {"city": "Springfield", "state": "IL", "zip": "62701", "country": "USA"},
    "nationality": "Canadian", "years_of_experience": 6, "current_role": "Engineer",
    "current_company": "Acme", "education": {"school": "State U", "degree": "BS CS"},
}
CTX = {"source": "LinkedIn"}


def ans(label, kind="text", profile=P):
    return plan_answer(Field(key="k", kind=kind, label=label), profile, {}, CTX)


@pytest.mark.parametrize("label", [
    "Referrer's name", "Emergency contact name", "Reference email", "Reference phone number",
    "Hiring manager name", "Recruiter email", "Manager's phone", "Company name",
])
def test_third_party_fields_never_get_the_candidates_details(label):
    assert ans(label) is None  # agent composes it or a person answers; never Alex's own name/email/phone


def test_phone_already_international_is_not_prefixed_twice():
    assert ans("Phone") == "+1 555-0100"
    assert ans("Phone", profile={**P, "phone": "555-0100"}) == "+1555-0100"  # existing format


@pytest.mark.parametrize("label", ["How many years of Kubernetes experience do you have?",
                                   "Years of experience with Python"])
def test_per_skill_years_is_not_answered_with_total_years(label):
    assert ans(label) is None


@pytest.mark.parametrize("label", ["Years of experience", "How many years of professional experience do you have?"])
def test_generic_years_uses_total(label):
    assert ans(label) == "6"


def test_open_source_essay_is_not_the_lead_source():
    assert ans("Describe your open source contributions", kind="textarea") is None
    assert ans("How did you hear about us?") == "LinkedIn"
    assert ans("Source") == "LinkedIn"


def test_citizenship_country_code_school_employer():
    assert ans("Country of citizenship") == "Canadian"
    assert ans("Phone country code") == "+1"
    assert ans("School name") == "State U"
    assert ans("Name of school") == "State U"
    assert ans("Name of your current employer") == "Acme"


# ── wrong-typed profile / settings (hand-edited JSON, a bad PUT) ─────────

def test_load_profile_repairs_crashing_shapes(data_dir):
    config.save_profile({"name": ["Alex", "Example"], "skills": "Python, SQL", "education": "BS",
                         "target_job_titles": None, "phone": 5550100, "work_authorization": True,
                         "years_of_experience": "6", "custom": {"kept": 1}})
    p = config.load_profile()
    assert p["name"] == "Alex Example"
    assert p["skills"] == ["Python", "SQL"]
    assert p["education"] == {"degree": "", "school": "", "graduation": ""}
    assert p["target_job_titles"] == []
    assert p["phone"] == "5550100"
    assert p["work_authorization"] is True  # yes/no bool is legitimate here
    assert p["years_of_experience"] == "6"  # unusual but harmless: left alone
    assert p["custom"] == {"kept": 1}


def test_profile_text_field_holding_a_bool_falls_back(data_dir):
    config.save_profile({"name": True, "email": {"x": 1}})
    p = config.load_profile()
    assert p["name"] == "" and p["email"] == ""


def test_save_settings_survives_wrong_types(data_dir):
    config.save_settings({"resume_path": True, "max_failures": None, "stagger_delay": "abc",
                          "blocked_domains": "evil.com, ok.io", "sensitive_data": 12345, "theme": ["dark"]})
    s = config.load_settings()
    assert s["resume_path"] == ""
    assert s["max_failures"] == 8 and s["stagger_delay"] == 5
    assert s["blocked_domains"] == ["evil.com", "ok.io"]  # not one entry per character
    assert s["sensitive_data"] == {"email": ""}
    assert s["theme"] == "dark"


def test_hand_edited_settings_file_is_repaired_on_load(data_dir):
    (config.get_data_dir() / "settings.json").write_text('{"resume_path": null, "blocked_domains": null}')
    s = config.load_settings()
    assert s["resume_path"] == "" and s["blocked_domains"] == []


def test_untouched_zero_years_is_not_claimed():
    assert ans("Years of experience", profile={**P, "years_of_experience": 0}) is None
    assert ans("Years of experience", profile={**P, "years_of_experience": 1}) == "1"
