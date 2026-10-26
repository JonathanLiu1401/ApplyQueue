"""M9 break-it at the API: bad resume files get an actionable message, sloppy LLM replies can't corrupt
the profile, and wrong-typed request bodies get a 4xx (never a 500) and never poison later reads."""
import json
import re
import zipfile

from fastapi.routing import APIRoute

import main
from tests.backend.test_main_api import _isolate_state, client, AUTH  # noqa: F401
from tests.fixtures.persona import write_resume_pdf


class _Reply:
    def __init__(self, text):
        self.completion = text


class _FakeLLM:
    def __init__(self, text):
        self.text = text

    async def ainvoke(self, msgs):
        return _Reply(self.text)


def _parse(client, monkeypatch, pdf, reply="{}"):
    monkeypatch.setattr(main, "load_settings", lambda: {"resume_path": str(pdf)})
    monkeypatch.setattr(main, "_resume_location_ok", lambda p: True)
    monkeypatch.setattr(main, "load_llm_settings", lambda: {"provider": "openai"})
    import core.llm_factory as lf
    monkeypatch.setattr(lf, "create_llm", lambda s: _FakeLLM(reply))
    return client.post("/profile/parse-resume", headers=AUTH).json()


def _bad_files(tmp_path):
    from reportlab.pdfgen import canvas
    enc = tmp_path / "enc.pdf"
    c = canvas.Canvas(str(enc), encrypt="secret")
    c.drawString(72, 720, "Alex Example")
    c.showPage()
    c.save()
    zero = tmp_path / "zero.pdf"
    zero.write_bytes(b"")
    docx = tmp_path / "docx.pdf"
    with zipfile.ZipFile(docx, "w") as z:
        z.writestr("word/document.xml", "<w:p>Alex</w:p>")
    good = write_resume_pdf(tmp_path / "good.pdf")
    data = good.read_bytes() if hasattr(good, "read_bytes") else open(good, "rb").read()
    trunc = tmp_path / "trunc.pdf"
    trunc.write_bytes(data[: len(data) // 2])
    html = tmp_path / "html.pdf"
    html.write_text("<html>Alex Example resume</html>")
    folder = tmp_path / "dir.pdf"
    folder.mkdir()
    return {"password-protected": enc, "empty": zero, "Word document": docx, "damaged": trunc,
            "web page": html, "folder": folder}


def test_bad_resume_files_get_actionable_messages(client, monkeypatch, tmp_path):
    for expect, path in _bad_files(tmp_path).items():
        r = _parse(client, monkeypatch, path)
        assert r.get("success") is not True, expect
        msg = r.get("message") or r.get("error") or json.dumps(r)
        assert expect in msg, (expect, msg)


def _profile_after(client, monkeypatch, tmp_path, reply):
    good = write_resume_pdf(tmp_path / "good.pdf")
    r = _parse(client, monkeypatch, good, reply)
    return r, json.loads((main.get_data_dir() / "candidate_profile.json").read_text()) if r.get("success") else None


def test_fenced_json_with_trailing_braces_parses(client, monkeypatch, tmp_path):
    r, p = _profile_after(client, monkeypatch, tmp_path,
                          '```json\n{"name": "Alex Example", "skills": ["Python"]}\n```\nNote: {phone} was missing.')
    assert r["success"] and p["name"] == "Alex Example" and "Python" in p["skills"]


def test_wrong_typed_llm_fields_cannot_corrupt_the_profile(client, monkeypatch, tmp_path):
    r, p = _profile_after(client, monkeypatch, tmp_path, json.dumps({
        "name": ["Alex", "Example"], "phone": 5550100, "address": "Springfield, IL", "skills": None,
        "education": "BS", "years_of_experience": "6+", "visa_sponsorship_needed": "no",
        "salary_expectation": {"min": "150k"}}))
    assert r["success"]
    assert p["name"] == "Alex Example" and p["phone"] == "5550100"
    assert isinstance(p["address"], dict) and isinstance(p["education"], dict)
    assert isinstance(p["skills"], list)
    assert p["years_of_experience"] == 6 and p["visa_sponsorship_needed"] is False
    assert p["salary_expectation"]["min"] == 150000
    assert client.get("/setup/status", headers=AUTH).status_code == 200


def test_prose_reply_is_a_clear_error(client, monkeypatch, tmp_path):
    r, _ = _profile_after(client, monkeypatch, tmp_path, "Sorry, I cannot help with that.")
    assert r.get("success") is not True
    assert "valid JSON" in json.dumps(r)


# Endpoints that would launch browsers, model downloads, network calls or long runs.
_SKIP = ("/jobs/collect", "/apply/start", "/local-models/pull", "/connect", "/run", "/chromium", "/sync",
         "/email/sweep", "/browser", "/install", "/shutdown", "/restart", "/llm/test", "/llm/ollama",
         "/cover-letter", "/agents/setup", "/login", "/open", "/parse-resume")
_KEYS = ["max_failures", "stagger_delay", "blocked_domains", "theme", "telemetry_enabled", "sensitive_data",
         "name", "titles", "locations", "resume_path", "job_description", "job_title", "company", "provider",
         "mode", "url", "urls", "job_url", "job_urls", "resume_id", "description", "id", "path", "start",
         "search_id", "backend", "enabled", "model", "base_url", "runtime", "settings", "text", "label",
         "answer", "question", "value", "schedule", "timezone", "days", "action", "approve", "instruction",
         "options", "evidence", "email", "phone", "skills", "education", "address", "title", "status"]
_VALUES = [None, 12345, True, ["a", {"b": 1}], {"x": None}, "\u202e\u0000" + "\U0001D518" * 3000]


def test_wrong_typed_bodies_never_500_or_poison_reads(client, monkeypatch):
    monkeypatch.setattr(main, "start_collection", lambda *a, **k: None, raising=False)
    bad = []
    for r in main.app.routes:
        if not isinstance(r, APIRoute) or any(s in r.path for s in _SKIP):
            continue
        path = re.sub(r"\{[^}]+\}", "x", r.path)
        for m in r.methods & {"POST", "PUT"}:
            for v in _VALUES:
                code = client.request(m, path, headers=AUTH, json={k: v for k, v in zip(_KEYS, [v] * len(_KEYS))})
                if code.status_code >= 500:
                    bad.append((m, path, repr(v)[:20], code.status_code))
    for r in main.app.routes:
        if isinstance(r, APIRoute) and "GET" in r.methods and "{" not in r.path and "stream" not in r.path:
            code = client.get(r.path, headers=AUTH).status_code
            if code >= 500:
                bad.append(("GET", r.path, "after fuzz", code))
    assert not bad, bad
