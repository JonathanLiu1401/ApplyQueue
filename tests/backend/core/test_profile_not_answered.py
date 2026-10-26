"""Untouched preset profile answers are "not answered" (TEST_REPORT §3, formerly §6).

Covers the schema-2 migration, the apply view, the setup checklist entries, the form guard's
preference gate, the MCP submit gate and the memory-context wording. The browser apply path is in
tests/e2e/test_agent_apply_e2e.py (test_unanswered_sponsorship_goes_to_a_person_not_a_default)."""
import json

import pytest

from core import form_guard as fg
from core import profile_presets as pp
from core import shared_config as sc

FILLED = {"name": "Alex Example", "email": "alex@example.com", "current_role": "Data Engineer"}
OLD_PRESETS = {"visa_sponsorship_needed": False, "willing_to_relocate": False, "preferred_work_mode": "hybrid",
               "salary_expectation": {"min": 50000, "max": 100000, "currency": "USD"},
               "years_of_experience": 0, "languages": ["English"], "country": "US",
               "address": {"city": "", "country": "USA"}, "phone_country_code": "+1"}


def F(label, kind="radio", **kw):
    return fg.Field(key=kw.pop("key", "k"), kind=kind, label=label, **kw)


# ── migration ────────────────────────────────────────────────────────────────
def test_missing_keys_become_unset():
    p, changed = pp.migrate(dict(FILLED))
    assert changed and p["_schema"] == 2 and "_confirm" not in p
    assert p["visa_sponsorship_needed"] is None and p["willing_to_relocate"] is None
    assert p["salary_expectation"] == {"min": None, "max": None}
    assert p["preferred_work_mode"] == "" and p["languages"] == [] and p["country"] == ""
    assert p["phone_country_code"] == "" and p["years_of_experience"] is None


def test_values_the_user_chose_are_kept_and_not_flagged():
    chosen = {**FILLED, "visa_sponsorship_needed": True, "willing_to_relocate": True,
              "preferred_work_mode": "remote", "salary_expectation": {"min": 140000, "max": 170000},
              "years_of_experience": 6, "languages": ["English", "Spanish"], "country": "DE",
              "address": {"country": "Germany"}, "phone_country_code": "+49"}
    p, _ = pp.migrate(chosen)
    assert "_confirm" not in p
    for k in ("visa_sponsorship_needed", "willing_to_relocate", "preferred_work_mode", "salary_expectation",
              "years_of_experience", "languages", "country", "phone_country_code"):
        assert p[k] == chosen[k], k
    assert pp.for_apply(chosen)["visa_sponsorship_needed"] is True


def test_value_equal_to_old_preset_is_kept_but_needs_confirming():
    # Ambiguous: "No" may be the user's real answer or the untouched preset. Kept, never deleted,
    # but listed for confirmation and withheld from the apply engine until confirmed.
    p, _ = pp.migrate({**FILLED, **OLD_PRESETS})
    assert p["visa_sponsorship_needed"] is False and p["salary_expectation"]["min"] == 50000
    assert set(p["_confirm"]) == {"visa_sponsorship_needed", "willing_to_relocate", "preferred_work_mode",
                                  "salary_expectation.min", "salary_expectation.max", "languages", "country",
                                  "address.country", "phone_country_code"}
    assert p["years_of_experience"] is None  # 0 years has meant "not answered" since M9
    a = pp.for_apply({**FILLED, **OLD_PRESETS})
    assert a["visa_sponsorship_needed"] is None and a["willing_to_relocate"] is None
    assert a["salary_expectation"]["min"] is None and a["preferred_work_mode"] == "" and "_confirm" not in a
    assert a["name"] == "Alex Example"


def test_never_filled_profile_presets_become_unset():
    p, _ = pp.migrate(dict(OLD_PRESETS))
    assert "_confirm" not in p
    assert p["visa_sponsorship_needed"] is None and p["salary_expectation"]["min"] is None
    assert p["languages"] == [] and p["address"]["country"] == ""


def test_migration_and_apply_view_are_idempotent():
    once, _ = pp.migrate({**FILLED, **OLD_PRESETS})
    twice, changed = pp.migrate(once)
    assert twice == once and changed is False
    a = pp.for_apply({**FILLED, **OLD_PRESETS})
    assert pp.for_apply(a) == a


def test_confirmed_list_drops_answered_or_bogus_entries():
    p, changed = pp.migrate({**FILLED, "_schema": 2, "visa_sponsorship_needed": None,
                             "_confirm": ["visa_sponsorship_needed", "not_a_field", 7, "country"], "country": "US"})
    assert p["_confirm"] == ["country"] and changed


def test_unanswered_lists_unset_and_unconfirmed():
    rows = {r["field"]: r for r in pp.unanswered({**FILLED, **OLD_PRESETS})}
    assert rows["visa_sponsorship_needed"]["state"] == "unconfirmed"
    assert rows["visa_sponsorship_needed"]["blocks_apply"] is True
    assert rows["years_of_experience"]["state"] == "unset" and rows["years_of_experience"]["blocks_apply"] is False
    done = {**FILLED, "_schema": 2, "visa_sponsorship_needed": False, "willing_to_relocate": True,
            "preferred_work_mode": "remote", "salary_expectation": {"min": 120000, "max": None},
            "years_of_experience": 6, "languages": ["English"], "country": "US",
            "address": {"country": "United States"}, "phone_country_code": "+1"}
    assert pp.unanswered(done) == []  # an explicit No is an answer; a minimum alone is a full salary answer


def test_parse_resume_template_never_carries_presets_into_the_profile():
    t = pp.typed_template({"visa_sponsorship_needed": None, "salary_expectation": {"min": None}})
    assert t["visa_sponsorship_needed"] is False  # a type hint for coercion only
    assert set(pp.NOT_FROM_RESUME) >= {"visa_sponsorship_needed", "willing_to_relocate", "salary_expectation"}


# ── config load/save ─────────────────────────────────────────────────────────
def test_load_profile_migrates_the_file_once(data_dir):
    from core import config
    f = config.get_data_dir() / "candidate_profile.json"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps({**FILLED, **OLD_PRESETS}), "utf-8")
    p = config.load_profile()
    on_disk = json.loads(f.read_text("utf-8"))
    assert on_disk["_schema"] == 2 and "visa_sponsorship_needed" in on_disk["_confirm"]
    assert on_disk["visa_sponsorship_needed"] is False and p["visa_sponsorship_needed"] is False
    assert config.load_profile() == p


def test_fresh_profile_has_no_preselected_answers(data_dir):
    from core import config
    p = config.load_profile()
    assert p["visa_sponsorship_needed"] is None and p["willing_to_relocate"] is None
    assert p["salary_expectation"]["min"] is None and p["salary_expectation"]["max"] is None
    assert p["preferred_work_mode"] == "" and p["years_of_experience"] is None


def test_saving_clears_answered_confirm_entries(data_dir):
    from core import config
    config.save_profile({**FILLED, **OLD_PRESETS, "_schema": 2, "_confirm": ["visa_sponsorship_needed"]})
    assert config.load_profile()["_confirm"] == ["visa_sponsorship_needed"]
    config.save_profile({**FILLED, "_schema": 2, "visa_sponsorship_needed": False})  # user confirmed: no list
    p = config.load_profile()
    assert "_confirm" not in p and p["visa_sponsorship_needed"] is False


def test_load_apply_profile_withholds_unconfirmed(tmp_path):
    f = tmp_path / "candidate_profile.json"
    f.write_text(json.dumps({**FILLED, **OLD_PRESETS}), "utf-8")
    a = sc.load_apply_profile(f)
    assert a["visa_sponsorship_needed"] is None and a["phone_country_code"] == ""


# ── form guard ───────────────────────────────────────────────────────────────
UNSET = pp.for_apply(dict(FILLED))
ANSWERED = {**UNSET, "visa_sponsorship_needed": False, "willing_to_relocate": True,
            "preferred_work_mode": "remote", "salary_expectation": {"min": 120000, "max": 150000}}


@pytest.mark.parametrize("label,key", [
    ("Will you now or in the future require sponsorship for employment visa status? *", "visa_sponsorship_needed"),
    ("Do you need visa sponsorship to work in this country?", "visa_sponsorship_needed"),
    ("Are you willing to relocate?", "willing_to_relocate"),
    ("What are your salary expectations?", "salary_expectation.min"),
    ("Desired annual salary", "salary_expectation.min"),
    ("Preferred work arrangement", "preferred_work_mode"),
    ("Are you open to working on-site or hybrid?", "preferred_work_mode"),
])
def test_unanswered_preference_detected(label, key):
    assert fg.unanswered_preference(label, UNSET)[0] == key
    assert fg.unanswered_preference(label, ANSWERED) is None


def test_explicit_no_is_an_answer_and_answer_bank_counts():
    assert fg.unanswered_preference("Will you require visa sponsorship?", {"visa_sponsorship_needed": False}) is None
    qa = {"Are you willing to relocate?": "Yes"}
    assert fg.unanswered_preference("Are you willing to relocate?", UNSET, qa) is None
    assert fg.unanswered_preference("City", UNSET) is None
    assert fg.unanswered_preference("Why do you want this job?", UNSET) is None


def test_unset_sponsorship_is_never_answered_from_another_rule():
    # Before: "...work in this country?" fell through to the "country" rule and answered "US".
    q = "Will you need visa sponsorship to work in this country?"
    assert fg._profile_answer(q, {**UNSET, "country": "US"}, {}) is None
    assert fg._profile_answer(q, {**ANSWERED, "country": "US"}, {}) == "No"


def test_gate_flags_prefilled_fields_and_skips_eeo():
    fields = [F("Will you require visa sponsorship?", empty=False, value="No", key="s"),
              F("Gender", "select", key="g"), F("City", "text", key="c")]
    gated = fg.preference_gate(fields, UNSET)
    assert [g["key"] for g in gated] == ["s"] and gated[0]["question"] == "Visa sponsorship"
    assert fg.preference_gate(fields, ANSWERED) == []
    assert "Profile page" in fg.preference_reason("Visa sponsorship")


# ── MCP agent tools (Claude Code / Codex backends) ───────────────────────────
async def test_mcp_submit_refused_and_recorded_when_preference_unset(aq_data_dir):
    from core import jobs_store
    from core.mcp_server import ApplyQueueTools
    from core.run_state import RunState
    url = "https://jobs.example.com/acme/1"
    (aq_data_dir / "jobs.json").write_text(json.dumps({url: {"url": url, "status": "pending", "title": "Analyst"}}))
    (aq_data_dir / "candidate_profile.json").write_text(json.dumps({**FILLED, **OLD_PRESETS}))  # old file
    state = RunState.create(aq_data_dir / "runs" / "r1", backend="cli:test", job_urls=[url])
    t = ApplyQueueTools(state, aq_data_dir)
    await t.call("next_job", {})
    fields = [F("Will you now or in the future require visa sponsorship? *", empty=False, value="No",
                key="0:5", required=True)]

    async def pick():
        return None, fields
    t._pick_form_frame = pick
    res = await t.call("form_submit", {"ref": "0:9"})
    assert res.get("isError") and "Visa sponsorship" in json.dumps(res)
    led = state.ledger()
    assert led["blocked_submits"] == 1 and led.get("submit_clicks", 0) == 0
    assert any("Not answered in your profile" in e["reason"] for e in led["needs_human"])
    await t.call("finish_job", {"result": "needs_human", "summary": "profile answer missing"})
    assert jobs_store.get_job(url)["status"] == "needs_human"


# ── memory context for the agent ─────────────────────────────────────────────
def test_memory_context_names_unanswered_preferences(monkeypatch):
    class Store:
        def qa_get_all_for_prompt(self):
            return {}

        def get_domain_memories(self, url, limit=20):
            return []

        def extract_domain(self, url):
            return "example.com"

        def format_for_prompt(self, m):
            return ""
    monkeypatch.setattr(sc, "_memory_store", Store())
    ctx = sc.build_memory_context(UNSET, qa={})
    assert "NOT ANSWERED by the candidate" in ctx and "report_needs_human" in ctx
    assert "Visa sponsorship" in ctx or "visa" in ctx.lower()
    assert "50000" not in ctx and "50,000" not in ctx
    ctx2 = sc.build_memory_context(ANSWERED, qa={})
    assert "NOT ANSWERED by the candidate" not in ctx2
