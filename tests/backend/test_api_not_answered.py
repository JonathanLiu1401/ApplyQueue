"""API side of the "not answered" profile state: GET/PUT keep null answers null, an old profile is
migrated on read, /setup/status lists what still needs the user, and résumé parsing never fills a
preference (sponsorship, relocation, work mode, salary)."""
import json

import main
from tests.backend.test_main_api import _isolate_state, client, AUTH  # noqa: F401
from tests.backend.test_api_breakit_m9 import _profile_after


def _write(profile):
    f = main.get_data_dir() / "candidate_profile.json"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(profile), "utf-8")
    return f


def test_null_answers_round_trip(client):
    body = client.get("/profile", headers=AUTH).json()
    assert body["visa_sponsorship_needed"] is None and body["willing_to_relocate"] is None
    assert body["salary_expectation"]["min"] is None and body["preferred_work_mode"] == ""
    body.update(name="Alex Example", willing_to_relocate=True)
    assert client.put("/profile", json=body, headers=AUTH).status_code == 200
    again = client.get("/profile", headers=AUTH).json()
    assert again["willing_to_relocate"] is True and again["visa_sponsorship_needed"] is None
    assert "_schema" not in again


def test_old_profile_is_migrated_on_read_and_flagged(client):
    f = _write({"name": "Alex Example", "email": "alex@example.com", "visa_sponsorship_needed": False,
                "willing_to_relocate": True, "salary_expectation": {"min": 50000, "max": 100000}})
    body = client.get("/profile", headers=AUTH).json()
    assert body["visa_sponsorship_needed"] is False and body["willing_to_relocate"] is True  # kept
    assert body["_confirm"] == ["visa_sponsorship_needed", "salary_expectation.min", "salary_expectation.max"]
    assert body["preferred_work_mode"] == ""  # never written: never answered
    disk = json.loads(f.read_text("utf-8"))
    assert disk["_schema"] == 2 and disk["_confirm"] == body["_confirm"]

    status = client.get("/setup/status", headers=AUTH).json()
    rows = {r["field"]: r for r in status["profile_unanswered"]}
    assert rows["visa_sponsorship_needed"]["state"] == "unconfirmed"
    assert rows["preferred_work_mode"]["state"] == "unset" and rows["preferred_work_mode"]["blocks_apply"]
    assert "willing_to_relocate" not in rows

    # "Keep these values": the UI saves without _confirm -> they become the user's answers.
    body.pop("_confirm")
    body["preferred_work_mode"] = "remote"
    assert client.put("/profile", json=body, headers=AUTH).status_code == 200
    rows = {r["field"] for r in client.get("/setup/status", headers=AUTH).json()["profile_unanswered"]}
    assert not rows & {"visa_sponsorship_needed", "willing_to_relocate", "preferred_work_mode",
                       "salary_expectation.min"}


def test_fresh_setup_status_lists_every_preference(client):
    rows = client.get("/setup/status", headers=AUTH).json()["profile_unanswered"]
    blocking = {r["field"] for r in rows if r["blocks_apply"]}
    assert blocking == {"visa_sponsorship_needed", "willing_to_relocate", "preferred_work_mode",
                        "salary_expectation.min"}
    assert all(r["state"] == "unset" for r in rows)


def test_resume_parse_never_answers_preferences(client, monkeypatch, tmp_path):
    reply = json.dumps({"name": "Alex Example", "visa_sponsorship_needed": False, "willing_to_relocate": True,
                        "preferred_work_mode": "remote", "salary_expectation": {"min": 50000, "max": 100000},
                        "years_of_experience": 6, "languages": ["English"]})
    r, p = _profile_after(client, monkeypatch, tmp_path, reply)
    assert r["success"]
    assert p["visa_sponsorship_needed"] is None and p["willing_to_relocate"] is None
    assert p["preferred_work_mode"] == "" and p["salary_expectation"]["min"] is None
    assert p["years_of_experience"] == 6 and p["languages"] == ["English"]  # facts from the résumé are fine
