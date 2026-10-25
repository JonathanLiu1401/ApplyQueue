"""M7: saved searches, rules, resume routing, stats, ATS check, starter packs, outreach drafts."""
import json

import pytest

from core.searches import ats, outreach, packs, rules, stats, store


@pytest.fixture
def cfg_dir(aq_data_dir):
    return aq_data_dir


def _resume(tmp_path, name, text="Python developer"):
    p = tmp_path / f"{name}.txt"
    p.write_text(text, encoding="utf-8")
    return str(p)


def test_search_crud_and_validation(cfg_dir):
    with pytest.raises(store.ValidationError):
        store.upsert_search({"name": "x", "titles": []})
    with pytest.raises(store.ValidationError):
        store.upsert_search({"titles": ["Engineer"], "mode": "yolo"})
    s = store.upsert_search({"titles": "Backend Engineer, Backend Engineer, API Developer", "locations": ["Remote"]})
    assert s["name"] == "Backend Engineer" and s["titles"] == ["Backend Engineer", "API Developer"]
    assert s["mode"] == "review"  # review-first is the default
    s2 = store.upsert_search({"id": s["id"], "mode": "auto", "name": "Backend"})
    assert s2["id"] == s["id"] and s2["mode"] == "auto" and s2["titles"] == s["titles"]
    with pytest.raises(store.ValidationError):
        store.upsert_search({"id": "s_missing", "titles": ["x"]})
    with pytest.raises(store.ValidationError):
        store.upsert_search({"titles": ["x"], "resume_id": "r_missing"})
    assert store.delete_search(s["id"]) and store.list_searches() == []


def test_rules_bans_and_categories(cfg_dir):
    r = store.set_rules({
        "categories": [{"name": "Data", "keywords": ["sql", "data engineer"]}, {"name": "", "keywords": ["x"]}],
        "bans": {"companies": ["Example Staffing"], "title_keywords": ["senior"], "description_keywords": ["unpaid"]},
        "answers": [{"question": "How did you hear about us?", "answer": "Job board"}, {"question": "q", "answer": ""}],
    })
    assert [c["name"] for c in r["categories"]] == ["Data"] and len(r["answers"]) == 1
    assert store.answers_dict() == {"How did you hear about us?": "Job board"}
    job = {"title": "Senior Data Engineer", "company": "Example Co", "description": "SQL and pipelines"}
    assert rules.ban_reason(job, r) == "Banned title keyword: senior"
    assert rules.ban_reason({"title": "Data Engineer", "company": "Example Staffing"}, r).startswith("Banned company")
    assert rules.ban_reason({"title": "Analyst", "description": "This is an UNPAID role"}, r).endswith("unpaid")
    assert rules.ban_reason({"title": "Seniority analyst"}, r) is None  # whole words only
    assert rules.ban_reason({"title": "Analyst"}, r, {"bans": {"keywords": ["analyst"]}})  # per-search ban
    assert rules.category_for({"title": "Data Engineer"}, r)["name"] == "Data"
    assert rules.category_for({"title": "Chef", "description": "no sql here"}, r)["name"] == "Data"
    assert rules.category_for({"title": "Chef"}, r) is None


def test_resume_routing_order(cfg_dir, tmp_path):
    a = store.add_resume({"path": _resume(tmp_path, "general"), "name": "General"})
    b = store.add_resume({"path": _resume(tmp_path, "data"), "name": "Data", "keywords": ["sql", "spark"]})
    c = store.add_resume({"path": _resume(tmp_path, "fe"), "name": "Frontend"})
    assert a["default"] and not b["default"]
    store.set_rules({"categories": [{"name": "FE", "keywords": ["react"], "resume_id": c["id"]}]})
    s = store.upsert_search({"titles": ["Platform Engineer"], "resume_id": b["id"]})
    cfg = store.load()
    assert rules.route_resume({"search_id": s["id"], "title": "React dev"}, cfg) == (store.load()["resumes"][1], "search")
    assert rules.route_resume({"title": "React developer"}, cfg)[0]["id"] == c["id"]
    assert rules.route_resume({"title": "Analyst", "description": "Spark and SQL"}, cfg)[1] == "keywords"
    assert rules.route_resume({"title": "Chef"}, cfg) == (cfg["resumes"][0], "default")
    with pytest.raises(store.ValidationError):
        store.add_resume({"path": str(tmp_path / "missing.pdf")})
    bad = tmp_path / "x.exe"
    bad.write_text("x")
    with pytest.raises(store.ValidationError):
        store.add_resume({"path": str(bad)})
    # Deleting the routed resume clears references.
    store.delete_resume(b["id"])
    assert store.get_search(s["id"])["resume_id"] is None


def test_stats_and_ab(cfg_dir):
    cfg = {"searches": [{"id": "s1", "name": "One", "mode": "auto"}],
           "resumes": [{"id": "rA", "name": "A"}, {"id": "rB", "name": "B"}]}
    jobs = {
        "u1": {"search_id": "s1", "status": "confirmed", "resume_id": "rA", "response": "interview", "response_at": "2"},
        "u2": {"search_id": "s1", "status": "submitted_unconfirmed", "resume_id": "rA", "response": "rejection"},
        "u3": {"search_id": "s1", "status": "submitted_unconfirmed", "resume_id": "rB"},
        "u4": {"search_id": "s1", "status": "blocked", "skip_reason": "Banned company: X"},
        "u5": {"search_id": "s1", "status": "pending", "review": "pending", "title": "T"},
        "u6": {"status": "applied"},
    }
    t = stats.tracker(jobs, cfg)
    s1 = t["searches"][0]
    assert (s1["found"], s1["applied"], s1["skipped"], s1["confirmed"], s1["interviews"], s1["review"]) == (5, 3, 1, 1, 1, 1)
    a, b = t["resumes"]
    assert a["applied"] == 2 and a["interview_rate"] == 0.5 and a["response_rate"] == 1.0
    assert b["applied"] == 1 and b["responses"] == 0
    assert t["totals"]["applied"] == 4  # legacy "applied" counts as submitted
    assert t["unassigned"]["found"] == 1
    assert [r["url"] for r in t["review_queue"]] == ["u5"]
    assert t["recent_responses"][0]["url"] == "u1"


def test_ats_check():
    jd = ("We need a Python engineer with Kubernetes experience. Kubernetes and Terraform daily. "
          "Project management skills; project management of releases. Python, PostgreSQL.")
    r = ats.check("Python developer. PostgreSQL, Docker. Led project management.", jd)
    assert "kubernetes" in r["missing"] and "python" in r["matched"] and "project management" in r["matched"]
    assert 0 < r["score"] < 100
    assert ats.check("x", "")["score"] == 0
    assert "experience" not in ats.keywords(jd)


def test_starter_packs_are_fictional_and_mergeable(cfg_dir):
    blob = json.dumps(packs.PACKS).lower()
    assert "@" not in blob and "http" not in blob
    assert {p["id"] for p in packs.public()} >= {"software", "healthcare", "retail", "new-grad"}
    r = packs.apply("software")
    assert r["searches_added"] == 2
    assert all(s["mode"] == "review" for s in store.list_searches())
    assert packs.apply("software")["searches_added"] == 0  # idempotent
    rules_ = store.load()["rules"]
    assert len([c for c in rules_["categories"] if c["name"] == "Backend"]) == 1
    assert rules_["bans"]["title_keywords"].count("director") == 1
    with pytest.raises(store.ValidationError):
        packs.apply("nope")


def test_outreach_off_by_default_and_drafts_only(cfg_dir):
    assert store.load()["outreach"]["enabled"] is False
    job = {"url": "https://jobs.example.com/1", "title": "Data Analyst", "company": "Acme Widgets"}
    with pytest.raises(store.ValidationError):
        outreach.draft(job, {"name": "Alex Example"})
    store.set_outreach({"enabled": True, "template": "Hi {recruiter}, {title} at {company}. {0} {name.__class__} -{name}"})
    d = outreach.draft(job, {"name": "Alex Example", "skills": ["SQL"]})
    assert d["sent"] is False and "Data Analyst at Acme Widgets" in d["body"] and d["body"].endswith("-Alex Example")
    assert "{0}" in d["body"] and "{name.__class__}" in d["body"]  # no str.format attribute access
    assert store.list_drafts()[0]["id"] == d["id"]
    assert outreach.delete(d["id"]) and store.list_drafts() == []


def test_resumes_with_identical_names_and_file_names_stay_distinct(cfg_dir, tmp_path):
    # M8: two "Resume" entries, both files called resume.pdf in different folders
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    pa, pb = tmp_path / "a" / "resume.txt", tmp_path / "b" / "resume.txt"
    pa.write_text("Python developer", encoding="utf-8")
    pb.write_text("Data analyst SQL", encoding="utf-8")
    a = store.add_resume({"path": str(pa), "name": "Resume"})
    b = store.add_resume({"path": str(pb), "name": "Resume", "keywords": ["sql"]})
    assert a["id"] != b["id"] and a["name"] == b["name"] == "Resume"
    s = store.upsert_search({"titles": ["Analyst"], "resume_id": b["id"]})
    cfg = store.load()
    assert rules.route_resume({"search_id": s["id"], "title": "Analyst"}, cfg)[0]["path"] == str(pb)
    assert rules.route_resume({"title": "Chef"}, cfg)[0]["path"] == str(pa)
    store.delete_resume(a["id"])                      # deleting one never takes the other with it
    left = store.load()["resumes"]
    assert [r["id"] for r in left] == [b["id"]] and left[0]["default"]
    assert store.get_search(s["id"])["resume_id"] == b["id"]


def test_resume_name_unicode_and_overlong(cfg_dir, tmp_path):
    p = tmp_path / "résumé-ñ.txt"
    p.write_text("x", encoding="utf-8")
    r = store.add_resume({"path": str(p), "name": "Résumé 📄 " + "ي" * 200})
    assert r["name"].startswith("Résumé 📄") and len(r["name"]) <= 80
    assert store.add_resume({"path": str(p)})["name"] == "résumé-ñ"
