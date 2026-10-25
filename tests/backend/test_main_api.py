"""
Industry-grade API / integration tests for the ApplyQueue FastAPI backend.

These exercise ``backend/main.py`` in-process with FastAPI's ``TestClient``
(no real HTTP server, no subprocess, no browser, no network). Heavy
dependencies (LLM creation, resume tailoring, Playwright) are monkeypatched so
only the request/response and persistence layers are exercised.

Auth model (see ``_AuthMiddleware`` in ``backend/main.py``):
  * ``/health`` and ``/chromium/status`` are unauthenticated.
  * Every other path requires ``Authorization: Bearer <_API_TOKEN>``.
  * If an ``Origin`` header is present it must be in ``_ALLOWED_ORIGINS``,
    otherwise the request is rejected with 403 *before* the token is checked.

``_API_TOKEN`` is captured from ``$JOB_APPLICANT_TOKEN`` (or a random secret)
*at import time*, so we set that env var before importing ``main`` and reuse
the value the module actually loaded.
"""
import os

# Pin the auth token BEFORE importing the backend so the middleware uses a
# value we know. main.py reads JOB_APPLICANT_TOKEN at import time.
os.environ.setdefault("JOB_APPLICANT_TOKEN", "test-token-deadbeef")

import pytest
from fastapi.testclient import TestClient

import main  # noqa: E402  (import after env var is set)

TOKEN = main._API_TOKEN
AUTH = {"Authorization": f"Bearer {TOKEN}"}


# ── Fixtures ────────────────────────────────────────────────────────────────
@pytest.fixture(autouse=True)
def _isolate_state(data_dir, monkeypatch):
    """Repoint stateful module-level paths at the per-test tmp data dir.

    Several backend modules resolve their storage paths *once at import time*
    from the real ``$HOME`` (``core.shared_config.JOBS_FILE``,
    ``memory.store.DB_PATH``, ``memory.metrics.DB_PATH``). Because ``main`` is
    imported at module load — before the ``data_dir`` fixture repoints HOME —
    those constants still point at the developer's real data dir. We rebind
    them here so every test reads and writes the same isolated tmp storage and
    never touches (or asserts against) real local data.
    """
    import core.shared_config as shared_config
    import memory.store as mem_store
    import memory.metrics as mem_metrics

    jobs_file = data_dir / "jobs.json"
    monkeypatch.setattr(shared_config, "DATA_DIR", data_dir)
    monkeypatch.setattr(shared_config, "JOBS_FILE", jobs_file)
    monkeypatch.setattr(shared_config, "JOBS_LOCK", data_dir / "jobs.json.lock")
    monkeypatch.setattr(shared_config, "QA_FILE", data_dir / "qa_repository.json")
    monkeypatch.setattr(
        shared_config, "CANDIDATE_PROFILE", data_dir / "candidate_profile.json"
    )

    db_path = data_dir / "memory_store.db"
    monkeypatch.setattr(mem_store, "DB_PATH", db_path)
    monkeypatch.setattr(mem_metrics, "DB_PATH", db_path)
    # The store/metrics default arg captured the old DB_PATH at class-definition
    # time, and main._get_*_store() construct them with no args. Force both
    # helpers to build instances against the isolated db.
    monkeypatch.setattr(main, "_get_memory_store", lambda: mem_store.MemoryStore(db_path))
    monkeypatch.setattr(
        main, "_get_metrics_store", lambda: mem_metrics.MetricsStore(db_path)
    )


@pytest.fixture
def client(data_dir):
    """A TestClient bound to the FastAPI app with an isolated data dir.

    ``data_dir`` (from conftest) repoints HOME at a tmp dir so all config /
    profile / settings / memory writes go to throwaway storage. We do NOT enter
    the TestClient context manager because that runs the app lifespan, which
    kills browser processes, spawns a parent watchdog and triggers a background
    Chromium install — none of which the API tests need.
    """
    return TestClient(main.app)


@pytest.fixture
def auth_client(client):
    """A client whose every request carries the valid bearer token."""
    client.headers.update(AUTH)
    return client


# ── Health & Chromium (unauthenticated) ─────────────────────────────────────
class TestHealthAndChromium:
    """Endpoints that the auth middleware lets through without a token."""

    def test_health_no_auth_required(self, client):
        """/health responds 200 even without an Authorization header."""
        resp = client.get("/health")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] in ("ok", "degraded")
        assert "checks" in body
        for key in ("database", "chromium", "llm_configured", "worker_running"):
            assert key in body["checks"]

    def test_chromium_status_no_auth_required(self, client):
        """/chromium/status is reachable without a token and reports a known state."""
        resp = client.get("/chromium/status")
        assert resp.status_code == 200
        state = resp.json().get("state")
        assert state in ("ready", "installed", "installing", "not_installed", "checking", "failed")


# ── Auth / middleware ────────────────────────────────────────────────────────
class TestAuth:
    """Authorization, origin and rate-limit behaviour of the middleware."""

    def test_protected_endpoint_rejects_without_token(self, client):
        """A protected endpoint returns 401 when no token is supplied."""
        resp = client.get("/profile")
        assert resp.status_code == 401
        assert resp.json() == {"error": "unauthorized"}

    def test_protected_endpoint_rejects_bad_token(self, client):
        """A wrong bearer token is rejected with 401."""
        resp = client.get("/profile", headers={"Authorization": "Bearer nope"})
        assert resp.status_code == 401

    def test_protected_endpoint_accepts_valid_token(self, auth_client):
        """The valid bearer token lets the request through."""
        resp = auth_client.get("/profile")
        assert resp.status_code == 200

    def test_disallowed_origin_is_forbidden(self, client):
        """A non-allowlisted Origin is blocked with 403 before the token check."""
        resp = client.get(
            "/profile",
            headers={"Origin": "https://evil.example.com", **AUTH},
        )
        assert resp.status_code == 403
        assert resp.json() == {"error": "forbidden"}

    def test_allowed_origin_passes(self, auth_client):
        """An allowlisted Origin combined with a valid token succeeds."""
        resp = auth_client.get("/profile", headers={"Origin": "http://localhost:1420"})
        assert resp.status_code == 200

    def test_health_bypasses_origin_check(self, client):
        """/health is exempt from both the origin and token checks."""
        resp = client.get("/health", headers={"Origin": "https://evil.example.com"})
        assert resp.status_code == 200


# ── Profile CRUD roundtrip ────────────────────────────────────────────────────
class TestProfile:
    """GET/PUT /profile persistence."""

    def test_default_profile_shape(self, auth_client):
        """A fresh data dir returns the default profile skeleton."""
        body = auth_client.get("/profile").json()
        assert body["name"] == ""
        assert body["country"] == "US"
        assert isinstance(body["target_job_titles"], list)

    def test_profile_save_and_load_roundtrip(self, auth_client):
        """A saved profile is read back verbatim (key fields)."""
        profile = {
            "name": "Test User",
            "email": "test@example.com",
            "country": "IN",
            "notice_period": "30 days",
            "target_job_titles": ["Senior Engineer"],
            "skills": ["Python", "React"],
        }
        save = auth_client.put("/profile", json=profile)
        assert save.status_code == 200
        assert save.json() == {"success": True}

        loaded = auth_client.get("/profile").json()
        assert loaded["name"] == "Test User"
        assert loaded["country"] == "IN"
        assert loaded["notice_period"] == "30 days"
        assert loaded["skills"] == ["Python", "React"]


# ── App settings CRUD ─────────────────────────────────────────────────────────
class TestAppSettings:
    """GET/PUT /settings including server-side clamping."""

    def test_settings_default_shape(self, auth_client):
        body = auth_client.get("/settings").json()
        assert "resume_path" in body
        assert "blocked_domains" in body

    def test_settings_roundtrip(self, auth_client):
        settings = {"resume_path": "/tmp/resume.pdf", "blocked_domains": ["spam.com"]}
        assert auth_client.put("/settings", json=settings).json() == {"success": True}
        loaded = auth_client.get("/settings").json()
        assert loaded["resume_path"] == "/tmp/resume.pdf"
        assert loaded["blocked_domains"] == ["spam.com"]

    def test_settings_clamps_out_of_range_values(self, auth_client):
        """max_failures / stagger_delay are clamped to safe bounds on save."""
        auth_client.put("/settings", json={"max_failures": 9999, "stagger_delay": -5})
        loaded = auth_client.get("/settings").json()
        assert loaded["max_failures"] == 50  # clamped to upper bound
        assert loaded["stagger_delay"] == 0  # clamped to lower bound

    def test_settings_strips_blank_blocked_domains(self, auth_client):
        auth_client.put("/settings", json={"blocked_domains": ["  ", "real.com", ""]})
        loaded = auth_client.get("/settings").json()
        assert loaded["blocked_domains"] == ["real.com"]


# ── LLM settings CRUD ─────────────────────────────────────────────────────────
class TestLLMSettings:
    """GET/PUT /settings/llm roundtrip."""

    def test_llm_default_shape(self, auth_client):
        body = auth_client.get("/settings/llm").json()
        assert "provider" in body

    def test_llm_settings_roundtrip(self, auth_client):
        settings = {
            "provider": "openrouter",
            "openrouter": {"api_key": "sk-xxx", "model": "meta-llama/llama-3.1-8b-instruct"},
        }
        assert auth_client.put("/settings/llm", json=settings).json() == {"success": True}
        loaded = auth_client.get("/settings/llm").json()
        assert loaded["provider"] == "openrouter"
        assert loaded["openrouter"]["model"] == "meta-llama/llama-3.1-8b-instruct"


# ── Countries ─────────────────────────────────────────────────────────────────
class TestCountries:
    def test_countries_list(self, auth_client):
        """/countries returns >= 18 country configs plus notice-period options."""
        body = auth_client.get("/countries").json()
        assert body["success"] is True
        assert len(body["countries"]) >= 18
        assert "notice_period_options" in body

    def test_get_known_country(self, auth_client):
        body = auth_client.get("/countries").json()
        code = next(iter(body["countries"]))
        resp = auth_client.get(f"/countries/{code}")
        assert resp.status_code == 200
        assert resp.json()["success"] is True

    def test_get_unknown_country_404(self, auth_client):
        """An unsupported country code yields a structured 404."""
        resp = auth_client.get("/countries/ZZ")
        assert resp.status_code == 404
        assert resp.json()["ok"] is False
        assert resp.json()["error"]["code"] == "not_found"


# ── Plugins ───────────────────────────────────────────────────────────────────
class TestPlugins:
    EXPECTED = {"linkedin", "indeed", "seek", "naukri", "reed", "stepstone"}

    def test_plugins_list(self, auth_client):
        """/plugins returns at least the six built-in job sources with full schema."""
        body = auth_client.get("/plugins").json()
        assert body["success"] is True
        plugins = body["plugins"]
        assert len(plugins) >= 6
        names = {p["name"] for p in plugins}
        assert self.EXPECTED.issubset(names)
        sample = plugins[0]
        for field in ("name", "display_name", "version", "countries", "filters"):
            assert field in sample

    def test_plugins_filtered_by_country(self, auth_client):
        """Country filtering returns only global ('ALL') + matching-country plugins."""
        # Unknown country: only the global plugins (countries == ['ALL']) match.
        zz = {p["name"] for p in auth_client.get("/plugins", params={"country": "ZZ"}).json()["plugins"]}
        assert "indeed" in zz and "linkedin" in zz
        assert "naukri" not in zz  # naukri is India-only

        # India: global plugins plus the India-specific one.
        india = {p["name"] for p in auth_client.get("/plugins", params={"country": "IN"}).json()["plugins"]}
        assert "naukri" in india
        assert "reed" not in india  # reed is GB-only

    def test_toggle_unknown_plugin_404(self, auth_client):
        resp = auth_client.put("/plugins/does-not-exist/toggle", json={"enabled": False})
        assert resp.status_code == 404
        assert resp.json()["error"]["code"] == "not_found"

    def test_toggle_known_plugin(self, auth_client):
        resp = auth_client.put("/plugins/linkedin/toggle", json={"enabled": True})
        assert resp.status_code == 200
        assert resp.json() == {"success": True, "name": "linkedin", "enabled": True}

    def test_import_plugin_missing_file_400(self, auth_client):
        """Importing a non-existent YAML path returns a 400 import error."""
        resp = auth_client.post("/plugins/import", json={"file_path": "/nonexistent/plugin.yaml"})
        assert resp.status_code == 400
        assert resp.json()["error"]["code"] == "import_failed"

    def test_reload_plugins(self, auth_client):
        resp = auth_client.post("/plugins/reload")
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["count"] >= 6


# ── Jobs (CRUD over jobs.json in tmp data dir) ────────────────────────────────
class TestJobs:
    def test_jobs_empty(self, auth_client):
        """A fresh data dir has no jobs."""
        assert auth_client.get("/jobs").json() == []

    def test_job_stats_empty(self, auth_client):
        stats = auth_client.get("/jobs/stats").json()
        assert stats["total"] == 0
        for k in ("pending", "applied", "failed", "blocked", "in_progress"):
            assert stats[k] == 0

    def test_add_job_and_list(self, auth_client):
        """A manually added job appears in listing and stats."""
        url = "https://www.linkedin.com/jobs/view/123456"
        resp = auth_client.post("/jobs/add", json={"url": url, "title": "Dev", "company": "Acme"})
        assert resp.status_code == 200
        assert resp.json()["success"] is True

        jobs = auth_client.get("/jobs").json()
        assert any(j["url"] == url for j in jobs)
        assert auth_client.get("/jobs/stats").json()["pending"] == 1

    def test_add_job_missing_url_400(self, auth_client):
        resp = auth_client.post("/jobs/add", json={"title": "Dev"})
        assert resp.status_code == 400
        assert resp.json()["error"]["code"] == "missing_field"

    def test_export_jobs_csv_empty(self, auth_client):
        """Export on an empty data dir returns a CSV with only the header row."""
        resp = auth_client.get("/jobs/export")
        assert resp.status_code == 200
        assert "text/csv" in resp.headers["content-type"]
        assert "attachment" in resp.headers["content-disposition"]
        assert "applyqueue-jobs.csv" in resp.headers["content-disposition"]
        lines = [ln for ln in resp.text.splitlines() if ln.strip()]
        assert len(lines) == 1  # header only
        assert "Job Title" in lines[0]
        assert "Company" in lines[0]
        assert "URL" in lines[0]

    def test_export_jobs_csv_with_data(self, auth_client):
        """Added jobs appear as CSV rows with the expected columns."""
        url = "https://www.linkedin.com/jobs/view/555000"
        auth_client.post("/jobs/add", json={"url": url, "title": "Engineer", "company": "Globex", "location": "Remote"})
        resp = auth_client.get("/jobs/export")
        assert resp.status_code == 200
        body = resp.text
        assert "Engineer" in body
        assert "Globex" in body
        assert "Remote" in body
        assert url in body
        # header + at least one data row
        assert len([ln for ln in body.splitlines() if ln.strip()]) >= 2

    def test_export_jobs_csv_requires_auth(self, client):
        """Export endpoint is behind auth like the rest of the API."""
        resp = client.get("/jobs/export")  # no Authorization header
        assert resp.status_code == 401

    def test_add_duplicate_job_409(self, auth_client):
        url = "https://www.linkedin.com/jobs/view/777"
        auth_client.post("/jobs/add", json={"url": url})
        resp = auth_client.post("/jobs/add", json={"url": url})
        assert resp.status_code == 409
        assert resp.json()["error"]["code"] == "duplicate"

    def test_update_status_unknown_job_404(self, auth_client):
        resp = auth_client.put(
            "/jobs/status",
            json={"url": "https://example.com/nope", "status": "applied"},
        )
        assert resp.status_code == 404
        assert resp.json()["error"]["code"] == "not_found"

    def test_update_status_invalid_status_400(self, auth_client):
        url = "https://www.linkedin.com/jobs/view/888"
        auth_client.post("/jobs/add", json={"url": url})
        resp = auth_client.put("/jobs/status", json={"url": url, "status": "bogus"})
        assert resp.status_code == 400
        assert resp.json()["error"]["code"] == "invalid_status"

    def test_update_status_success(self, auth_client):
        url = "https://www.linkedin.com/jobs/view/999"
        auth_client.post("/jobs/add", json={"url": url})
        # Older clients still send "applied"; it is stored as submitted_unconfirmed (#89).
        resp = auth_client.put("/jobs/status", json={"url": url, "status": "applied"})
        assert resp.status_code == 200
        assert resp.json()["status"] == "submitted_unconfirmed"
        stats = auth_client.get("/jobs/stats").json()
        assert stats["submitted_unconfirmed"] == 1 and stats["submitted"] == 1 and stats["applied"] == 1

    def test_delete_jobs(self, auth_client):
        url = "https://www.linkedin.com/jobs/view/555"
        auth_client.post("/jobs/add", json={"url": url})
        resp = auth_client.request("DELETE", "/jobs", json={"urls": [url]})
        assert resp.status_code == 200
        assert resp.json()["deleted"] == 1

    def test_delete_jobs_missing_array_400(self, auth_client):
        resp = auth_client.request("DELETE", "/jobs", json={})
        assert resp.status_code == 400
        assert resp.json()["error"]["code"] == "missing_field"

    def test_jobs_status_filter(self, auth_client):
        """The ?status filter excludes non-matching jobs."""
        url = "https://www.linkedin.com/jobs/view/4242"
        auth_client.post("/jobs/add", json={"url": url})  # pending
        assert auth_client.get("/jobs", params={"status": "applied"}).json() == []
        assert len(auth_client.get("/jobs", params={"status": "pending"}).json()) == 1


# ── Memory / Q&A / dashboard (SQLite-backed, empty store) ─────────────────────
class TestMemoryAndQA:
    def test_memory_stats(self, auth_client):
        stats = auth_client.get("/memory/stats").json()
        assert stats["total_memories"] == 0
        assert "by_category" in stats

    def test_memory_domains_empty(self, auth_client):
        assert auth_client.get("/memory/domains").json() == []

    def test_memory_search_blank_query(self, auth_client):
        """A blank query returns an empty list rather than erroring."""
        assert auth_client.get("/memory/search", params={"q": ""}).json() == []

    def test_memory_export(self, auth_client):
        assert auth_client.get("/memory/export").json() == []

    def test_memory_decay(self, auth_client):
        resp = auth_client.post("/memory/decay", json={"days": 30, "factor": 0.9})
        assert resp.status_code == 200
        assert resp.json()["success"] is True

    def test_memory_decay_validation_error(self, auth_client):
        """factor > 1.0 violates the pydantic model -> 422."""
        resp = auth_client.post("/memory/decay", json={"factor": 5.0})
        assert resp.status_code == 422

    def test_memory_cleanup(self, auth_client):
        resp = auth_client.post("/memory/cleanup", json={"threshold": 0.3})
        assert resp.status_code == 200
        assert resp.json()["success"] is True

    def test_qa_empty(self, auth_client):
        assert auth_client.get("/qa").json() == []

    def test_qa_stats(self, auth_client):
        stats = auth_client.get("/qa/stats").json()
        assert stats == {"total": 0, "answered": 0, "unanswered": 0}

    def test_qa_auto_squash(self, auth_client):
        resp = auth_client.post("/qa/auto-squash")
        assert resp.status_code == 200
        assert resp.json()["success"] is True

    def test_dashboard_aggregates(self, auth_client):
        """/dashboard merges jobs + memory (+ metrics) into one payload."""
        body = auth_client.get("/dashboard").json()
        assert "jobs" in body
        assert "memory" in body
        assert "metrics" in body
        assert body["jobs"]["total"] == 0


# ── Metrics & logs (empty store) ──────────────────────────────────────────────
class TestMetricsAndLogs:
    def test_metric_runs_empty(self, auth_client):
        assert auth_client.get("/metrics/runs").json() == []

    def test_metric_domains_empty(self, auth_client):
        assert auth_client.get("/metrics/domains").json() == []

    def test_recent_logs_empty(self, auth_client):
        assert auth_client.get("/logs/recent").json() == []

    def test_runs_with_logs_empty(self, auth_client):
        assert auth_client.get("/logs/runs").json() == []


# ── Setup status & onboarding ─────────────────────────────────────────────────
class TestSetup:
    def test_setup_status_fresh(self, auth_client):
        """A fresh install reports nothing configured / done."""
        body = auth_client.get("/setup/status").json()
        assert body["profile"] is False
        assert body["resume"] is False
        assert body["onboarding_completed"] is False
        assert body["all_required_done"] is False

    def test_complete_onboarding_persists(self, auth_client):
        assert auth_client.post("/setup/complete-onboarding").json() == {"success": True}
        body = auth_client.get("/setup/status").json()
        assert body["onboarding_completed"] is True

    def test_setup_status_llm_done_for_gemini(self, auth_client):
        """A configured Gemini key marks the LLM step done (#40 provider)."""
        auth_client.put("/settings/llm", json={"provider": "gemini", "gemini": {"api_key": "AIza-x", "model": "gemini-2.5-pro"}})
        assert auth_client.get("/setup/status").json()["llm"] is True

    def test_setup_status_llm_done_for_openai_compatible(self, auth_client):
        """A configured base_url marks the LLM step done for openai_compatible."""
        auth_client.put("/settings/llm", json={"provider": "openai_compatible", "openai_compatible": {"base_url": "https://api.together.xyz/v1", "api_key": "", "model": "m"}})
        assert auth_client.get("/setup/status").json()["llm"] is True

    def test_setup_status_llm_not_done_when_gemini_key_blank(self, auth_client):
        auth_client.put("/settings/llm", json={"provider": "gemini", "gemini": {"api_key": "", "model": "gemini-2.5-pro"}})
        assert auth_client.get("/setup/status").json()["llm"] is False


# ── Status endpoints for the long-running workers (no work triggered) ─────────
class TestWorkerStatus:
    def test_collection_status_idle(self, auth_client):
        body = auth_client.get("/jobs/collect/status").json()
        assert body["running"] is False
        assert isinstance(body["log"], list)

    def test_apply_status_idle(self, auth_client):
        body = auth_client.get("/apply/status").json()
        assert body["running"] is False
        assert body["workers"] == 1


# ── Auth/login status (cookie inspection, no browser launched) ────────────────
class TestAuthStatus:
    def test_auth_status_no_cookies(self, auth_client):
        """With no browser profile, both services report logged_in=False."""
        body = auth_client.get("/auth/status").json()
        assert body["linkedin"]["logged_in"] is False
        assert body["gmail"]["logged_in"] is False

    def test_login_unknown_service(self, auth_client):
        """An unknown login service is rejected without launching anything."""
        body = auth_client.post("/auth/login/myspace").json()
        assert body["success"] is False
        assert "Unknown service" in body["message"]


# ── LLM-dependent endpoints: validation branches + mocked success ─────────────
class TestLLMEndpoints:
    """These endpoints call create_llm / network. We never invoke the real
    dependency: we either hit the input-validation branch, or monkeypatch
    create_llm + the LLM's ainvoke to assert the success path."""

    def test_llm_test_invalid_key_message(self, auth_client, monkeypatch):
        """/llm/test surfaces a friendly message when create_llm raises an auth error."""
        from core import llm_factory

        def _boom(_settings):
            raise RuntimeError("Invalid api_key provided")

        monkeypatch.setattr(llm_factory, "create_llm", _boom)
        resp = auth_client.post("/llm/test", json={"provider": "openai"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is False
        assert "Invalid API key" in body["message"]

    def test_llm_test_success_mocked(self, auth_client, monkeypatch):
        """/llm/test returns success when create_llm + test_connection are stubbed."""
        from core import llm_factory

        async def _ok(_llm):
            return "pong"

        monkeypatch.setattr(llm_factory, "create_llm", lambda s: object())
        monkeypatch.setattr(llm_factory, "test_connection", _ok)
        resp = auth_client.post("/llm/test", json={"provider": "openai"})
        assert resp.status_code == 200
        assert resp.json() == {"success": True, "message": "pong"}

    def test_cover_letter_requires_description(self, auth_client):
        """/cover-letter/generate validates that a job description is present."""
        resp = auth_client.post("/cover-letter/generate", json={"job_title": "Dev"})
        assert resp.status_code == 400
        assert resp.json()["error"]["code"] == "missing_field"

    def test_cover_letter_requires_llm_configured(self, auth_client):
        """With no LLM provider configured the endpoint errors clearly (no network)."""
        # Persist an LLM settings file with an empty provider.
        auth_client.put("/settings/llm", json={"provider": ""})
        resp = auth_client.post(
            "/cover-letter/generate",
            json={"job_description": "Build things.", "job_title": "Dev", "company": "Acme"},
        )
        assert resp.status_code == 400
        assert resp.json()["error"]["code"] == "no_llm"

    def test_cover_letter_success_mocked(self, auth_client, monkeypatch):
        """Happy path: a configured + mocked LLM yields a cover letter string."""
        auth_client.put(
            "/settings/llm",
            json={"provider": "openai", "openai": {"api_key": "sk", "model": "gpt-4o"}},
        )

        class _Resp:
            content = "Dear Hiring Manager, ..."

        class _LLM:
            async def ainvoke(self, _messages):
                return _Resp()

        from core import llm_factory

        monkeypatch.setattr(llm_factory, "create_llm", lambda s: _LLM())
        resp = auth_client.post(
            "/cover-letter/generate",
            json={"job_description": "Build things.", "job_title": "Dev", "company": "Acme"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["cover_letter"].startswith("Dear Hiring Manager")


# ── Resume tailoring: validation branch (no LLM / no tailoring invoked) ───────
class TestResumeTailor:
    def test_tailor_requires_job_urls(self, auth_client):
        resp = auth_client.post("/resume/tailor", json={})
        assert resp.status_code == 400
        assert resp.json()["error"]["code"] == "missing_field"

    def test_tailor_refine_requires_job_url(self, auth_client):
        resp = auth_client.post("/resume/tailor/refine", json={"instruction": "shorter"})
        assert resp.status_code == 400
        assert resp.json()["error"]["code"] == "missing_field"

    def test_get_tailored_by_url_requires_url(self, auth_client):
        resp = auth_client.post("/resume/tailor/get-by-url", json={})
        assert resp.status_code == 400
        assert resp.json()["error"]["code"] == "missing_field"

    def test_get_tailored_by_hash_not_found(self, auth_client):
        resp = auth_client.get("/resume/tailor/deadbeef")
        assert resp.status_code == 404
        assert resp.json()["error"]["code"] == "not_found"

    def test_tailor_unknown_job_reported_per_url(self, auth_client):
        """tailor returns per-URL error status for jobs not in the queue (no LLM call)."""
        resp = auth_client.post(
            "/resume/tailor", json={"job_urls": ["https://example.com/missing"]}
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["results"][0]["status"] == "error"
        assert body["results"][0]["message"] == "Job not found"


# ── Job-collection / apply request validation (no worker thread started) ──────
class TestCollectApplyValidation:
    def test_collect_rejects_out_of_range_max_jobs(self, auth_client):
        """max_jobs > 500 violates the pydantic CollectRequest model -> 422."""
        resp = auth_client.post("/jobs/collect", json={"max_jobs": 10000})
        assert resp.status_code == 422

    def test_apply_rejects_invalid_mode(self, auth_client):
        """An unknown apply mode is rejected by the Literal field -> 422."""
        resp = auth_client.post("/apply/start", json={"mode": "telepathy"})
        assert resp.status_code == 422

    def test_apply_rejects_too_many_workers(self, auth_client):
        resp = auth_client.post("/apply/start", json={"mode": "easy", "workers": 99})
        assert resp.status_code == 422


# ── Generic 404 ───────────────────────────────────────────────────────────────
class TestNotFound:
    def test_unknown_route_404(self, auth_client):
        resp = auth_client.get("/this/route/does/not/exist")
        assert resp.status_code == 404

    def test_ollama_models_unreachable_server(self, auth_client):
        """/llm/ollama-models gracefully reports failure for an unreachable host."""
        resp = auth_client.post(
            "/llm/ollama-models", json={"base_url": "http://127.0.0.1:1"}
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is False
        assert body["models"] == []


# ── #89: submission statuses, email confirmation hook, site vault ───────────
def _seed(auth_client, url, **fields):
    from core.shared_config import update_job

    assert auth_client.post("/jobs/add", json={"url": url}).status_code == 200
    if fields:
        update_job(url, **fields)


class TestSubmissionStatuses:
    def test_manual_status_rejects_unknown(self, auth_client):
        url = "https://boards.example.com/jobs/1"
        _seed(auth_client, url)
        resp = auth_client.put("/jobs/status", json={"url": url, "status": "in_progress"})
        assert resp.status_code == 400

    def test_manual_confirmed_sets_timestamps(self, auth_client):
        url = "https://boards.example.com/jobs/2"
        _seed(auth_client, url)
        assert auth_client.put("/jobs/status", json={"url": url, "status": "confirmed"}).status_code == 200
        job = next(j for j in auth_client.get("/jobs").json() if j["url"] == url)
        assert job["status"] == "confirmed" and job["status_set_manually"] is True
        assert job.get("submitted_at")

    def test_stats_and_filters_cover_every_status(self, auth_client):
        _seed(auth_client, "https://boards.example.com/a", status="submitted_unconfirmed",
              page_confirmation={"matched": "url"})
        _seed(auth_client, "https://boards.example.com/b", status="confirmed")
        _seed(auth_client, "https://boards.example.com/c", status="needs_human",
              needs_human_fields=[{"field": "Why us?", "reason": "no answer"}])
        _seed(auth_client, "https://boards.example.com/d", status="failed")
        stats = auth_client.get("/jobs/stats").json()
        assert stats["submitted_unconfirmed"] == 1 and stats["confirmed"] == 1
        assert stats["needs_human"] == 1 and stats["failed"] == 1 and stats["submitted"] == 2
        submitted = {j["url"] for j in auth_client.get("/jobs", params={"status": "submitted"}).json()}
        assert submitted == {"https://boards.example.com/a", "https://boards.example.com/b"}
        attention = {j["url"] for j in auth_client.get("/jobs", params={"status": "needs_human,failed"}).json()}
        assert attention == {"https://boards.example.com/c", "https://boards.example.com/d"}

    def test_confirm_email_requires_page_confirmation(self, auth_client):
        url = "https://boards.example.com/jobs/3"
        _seed(auth_client, url, status="submitted_unconfirmed")  # no page evidence
        resp = auth_client.post("/jobs/confirm-email", json={"url": url, "evidence": {"message_id": "m1"}})
        assert resp.status_code == 409

    def test_confirm_email_upgrades_and_strips_body(self, auth_client):
        url = "https://boards.example.com/jobs/4"
        _seed(auth_client, url, status="submitted_unconfirmed", page_confirmation={"matched": "text"})
        resp = auth_client.post("/jobs/confirm-email", json={
            "url": url,
            "evidence": {"message_id": "m2", "from_domain": "example.com", "body": "full email text"},
        })
        assert resp.status_code == 200 and resp.json()["status"] == "confirmed"
        job = next(j for j in auth_client.get("/jobs").json() if j["url"] == url)
        assert job["status"] == "confirmed" and job["confirmed_at"]
        assert job["email_confirmation"] == {"message_id": "m2", "from_domain": "example.com"}

    def test_confirm_email_validation(self, auth_client):
        assert auth_client.post("/jobs/confirm-email", json={"url": ""}).status_code == 400
        resp = auth_client.post("/jobs/confirm-email",
                                json={"url": "https://boards.example.com/none", "evidence": {"message_id": "x"}})
        assert resp.status_code == 404


class TestVaultSites:
    def test_lists_hosts_without_passwords(self, auth_client, data_dir):
        from core.config import get_data_dir
        from core.credential_vault import CredentialVault

        vault = CredentialVault(get_data_dir() / "vault" / "site_credentials.json")
        entry, created = vault.get_or_create("www.Example.com", "alex@example.com")
        assert created
        body = auth_client.get("/vault/sites").json()
        assert body["legacy_shared_password"] is False
        assert [s["host"] for s in body["sites"]] == ["example.com"]
        assert entry["password"] not in str(body)

    def test_settings_never_return_or_store_password(self, auth_client, data_dir):
        settings = auth_client.get("/settings").json()
        settings["sensitive_data"] = {"email": "alex@example.com", "password": "LegacyShared1!"}
        assert auth_client.put("/settings", json=settings).status_code == 200
        again = auth_client.get("/settings").json()
        assert "password" not in again.get("sensitive_data", {})
        assert auth_client.get("/vault/sites").json()["legacy_shared_password"] is True


class TestAgentBackends:
    """M3: subscription CLI backend cards (detection is local; nothing is installed in the tmp HOME)."""

    def test_lists_all_backends(self, auth_client, monkeypatch):
        monkeypatch.setenv("PATH", "/nonexistent")
        body = auth_client.get("/agents/backends").json()
        assert body["selected"] == "browser_use"
        rows = {b["id"]: b for b in body["backends"]}
        assert list(rows) == ["grok", "cursor", "codex", "claude", "gemini"]
        assert rows["gemini"]["runnable"] is False and rows["claude"]["requires_terms"] is True
        assert all(r["installed"] is False for r in rows.values())

    def test_claude_needs_terms_and_revoking_unselects(self, auth_client):
        assert auth_client.put("/agents/backends/claude", json={"select": True}).status_code == 400
        assert auth_client.put("/agents/backends/gemini", json={"select": True}).status_code == 400
        assert auth_client.put("/agents/backends/nope", json={}).status_code == 404
        ok = auth_client.put("/agents/backends/claude", json={"accept_terms": True, "select": True}).json()
        assert ok["selected"] == "cli:claude" and ok["config"]["terms_accepted_at"]
        # A stale Settings form cannot change the backend behind the user's back.
        settings = auth_client.get("/settings").json()
        settings["agent_backend"] = "browser_use"
        auth_client.put("/settings", json=settings)
        assert auth_client.get("/settings").json()["agent_backend"] == "cli:claude"
        off = auth_client.put("/agents/backends/claude", json={"accept_terms": False}).json()
        assert off["selected"] == "browser_use"
        assert auth_client.put("/agents/selected", json={"backend": "cli:codex"}).json()["selected"] == "cli:codex"
        assert auth_client.put("/agents/selected", json={"backend": "browser_use"}).json()["selected"] == "browser_use"

    def test_bad_custom_path_rejected(self, auth_client):
        assert auth_client.put("/agents/backends/codex", json={"path": "/no/such/codex"}).status_code == 400

    def test_setup_prompt_has_no_secrets(self, auth_client):
        prompt = auth_client.get("/agents/setup-prompt").json()["prompt"]
        assert "ApplyQueue" in prompt and TOKEN not in prompt and "Bearer" not in prompt


class TestEmailApi:
    """M4 endpoints. The OAuth callback is reachable without the bearer token but needs our one-time state."""

    def test_status_never_returns_secrets(self, auth_client):
        r = auth_client.put("/email/gmail/config", json={"client_id": "client-1.apps.example.com",
                                                          "client_secret": "desktop-secret-x"})
        assert r.status_code == 200
        body = auth_client.get("/email/status").json()
        assert body["providers"]["gmail"]["configured"] and not body["providers"]["gmail"]["linked"]
        assert "desktop-secret-x" not in str(body)
        assert body["settings"]["confirm_label"] == "ApplyQueue/Applied"

    def test_connect_url_and_forged_callback(self, auth_client):
        assert auth_client.post("/email/outlook/connect").status_code == 400  # no client yet
        auth_client.put("/email/outlook/config", json={"client_id": "00000000-0000-0000-0000-00000000abcd"})
        url = auth_client.post("/email/outlook/connect").json()["url"]
        assert "code_challenge=" in url and "state=" in url
        anon = TestClient(main.app)
        r = anon.get("/email/oauth/callback", params={"state": "forged", "code": "x"})
        assert r.status_code == 400 and "expired" in r.text
        assert anon.get("/email/status").status_code == 401
        assert auth_client.put("/email/nope/config", json={"client_id": "x"}).status_code == 404

    def test_settings_roundtrip_and_sweep_needs_link(self, auth_client):
        r = auth_client.put("/email/settings", json={"confirm_label": "  Job   Hunt ", "sweep_minutes": 1,
                                                      "archive_confirmations": False}).json()
        assert r["settings"]["confirm_label"] == "Job Hunt" and r["settings"]["sweep_minutes"] == 2
        assert auth_client.get("/email/settings").json()["archive_confirmations"] is False
        assert auth_client.post("/email/sweep").status_code == 400


class TestSyncApi:
    """M5 endpoints against the mocked GitHub API and a local bare remote."""

    @pytest.fixture
    def gh(self, tmp_path, monkeypatch):
        import shutil
        import subprocess
        import sys as _sys
        from pathlib import Path as _P
        if shutil.which("git") is None:
            pytest.skip("git not installed")
        _sys.path.insert(0, str(_P(__file__).resolve().parents[1] / "fixtures"))
        from mock_github import MockGitHub
        from core.sync import github
        m = MockGitHub()
        monkeypatch.setattr(github, "WEB", m.url)
        monkeypatch.setattr(github, "API", m.url)
        bare = tmp_path / "state.git"
        subprocess.run(["git", "init", "--bare", "-q", str(bare)], check=True)
        monkeypatch.setattr(github, "https_remote", lambda o, r: str(bare))
        yield m
        m.close()

    def test_device_flow_create_and_sync(self, auth_client, gh):
        assert auth_client.post("/sync/github/device").status_code == 400  # no client id
        auth_client.put("/sync/config", json={"client_id": "Iv1.example", "device_name": "laptop"})
        r = auth_client.post("/sync/github/device").json()
        assert r["user_code"] == "ABCD-1234"
        assert auth_client.post("/sync/github/poll").json()["status"] == "pending"
        assert auth_client.post("/sync/github/poll").json()["status"] == "ok"
        st = auth_client.get("/sync/status").json()
        assert st["github_linked"] and not st["configured"]
        assert "access_token" not in str(st)
        r = auth_client.post("/sync/repo", json={"name": "applyqueue-state"})
        assert r.status_code == 200, r.text
        assert r.json()["repo"]["private"] is True
        assert gh.repos["alex-example/applyqueue-state"]["private"] is True
        st = auth_client.get("/sync/status").json()
        assert st["configured"] and st["cloned"] and st["owner"] == "alex-example"
        assert auth_client.post("/sync/now").json()["ok"] is True

    def test_public_repo_refused(self, auth_client, gh):
        from mock_github import FAKE_TOKEN
        from core.sync import store
        store.save_token({"access_token": FAKE_TOKEN})
        gh.repos["alex-example/oops"] = {"name": "oops", "private": False, "visibility": "public",
                                         "permissions": {"push": True}}
        r = auth_client.post("/sync/repo", json={"name": "oops", "create": False})
        assert r.status_code == 409 and "public" in r.json()["error"]
        assert auth_client.get("/sync/status").json()["configured"] is False

    def test_key_export_import_and_unlink(self, auth_client, gh):
        k = auth_client.get("/sync/key").json()["key"]
        assert k.startswith("aqk1.")
        assert auth_client.post("/sync/key", json={"key": "nope"}).status_code == 400
        assert auth_client.post("/sync/key", json={"key": k}).json()["success"]
        assert auth_client.post("/sync/unlink").json()["success"]
        assert auth_client.post("/sync/now").status_code == 400


class TestLocalModelsApi:
    """M6 endpoints against mocked local runtimes."""

    @pytest.fixture
    def ollama(self, tmp_path, monkeypatch):
        import sys as _sys
        from pathlib import Path as _P
        _sys.path.insert(0, str(_P(__file__).resolve().parents[1] / "fixtures"))
        from mock_local_llm import MockRuntime, dead_url
        from core.local_models import detect
        rt = MockRuntime("ollama", ["qwen3-vl:4b"], vision={"qwen3-vl:4b"})
        dead = dead_url()
        monkeypatch.setattr(detect, "default_endpoints", lambda: {
            "ollama": rt.base, "lmstudio": dead, "llamacpp": dead, "openai": dead})
        monkeypatch.setenv("APPLYQUEUE_GGUF_DIRS", str(tmp_path))
        yield rt
        rt.close()

    def test_overview(self, auth_client, ollama):
        r = auth_client.get("/local-models").json()
        assert [x["id"] for x in r["runtimes"]] == ["ollama"]
        assert r["runtimes"][0]["models"][0]["vision"] is True
        assert r["hardware"]["ram_bytes"] >= 0 and r["expectations"]
        assert "speed_note" in r["recommendation"]

    def test_pull_and_use(self, auth_client, ollama):
        import time
        r = auth_client.post("/local-models/pull", json={"model": "qwen3-vl:8b"}).json()
        assert r["success"]
        for _ in range(50):
            job = auth_client.get(f"/local-models/pull/{r['job']['id']}").json()["job"]
            if job["state"] != "running":
                break
            time.sleep(0.05)
        assert job["state"] == "done" and ollama.pulls == ["qwen3-vl:8b"]
        assert auth_client.post("/local-models/pull", json={"model": "../x"}).status_code == 400
        assert auth_client.get("/local-models/pull/nope").status_code == 404

        u = auth_client.post("/local-models/use", json={"runtime": "ollama", "base_url": ollama.base,
                                                        "model": "qwen3-vl:8b"}).json()
        assert u["provider"] == "ollama"
        llm = auth_client.get("/settings/llm").json()
        assert llm["provider"] == "ollama" and llm["ollama"]["model"] == "qwen3-vl:8b"
        assert auth_client.get("/settings").json()["agent_backend"] == "browser_use"

    def test_use_openai_compatible_and_validation(self, auth_client, ollama):
        u = auth_client.post("/local-models/use", json={"runtime": "lmstudio", "base_url": "http://127.0.0.1:1234",
                                                        "model": "google/gemma-3-12b"}).json()
        assert u["provider"] == "openai_compatible"
        assert auth_client.post("/local-models/use", json={"runtime": "ollama", "base_url": "file:///etc",
                                                           "model": "x"}).status_code == 400
        assert auth_client.post("/local-models/use", json={"runtime": "nope", "base_url": "http://127.0.0.1:1",
                                                           "model": "x"}).status_code == 400

    def test_pull_when_ollama_missing(self, auth_client, ollama, monkeypatch):
        from mock_local_llm import dead_url
        r = auth_client.post("/local-models/pull", json={"model": "qwen3-vl:2b", "base_url": dead_url()}).json()
        assert r["success"] is False and "ollama.com" in r["message"]


# ── M7: saved searches, tracker, resumes, packs, outreach ───────────────────
class TestSearchesApi:
    """LoopCV-style saved searches and tracker endpoints."""

    def _jobs(self, data_dir, jobs):
        import json as _json
        (data_dir / "jobs.json").write_text(_json.dumps(jobs))

    def test_quickstart_creates_review_search(self, auth_client, data_dir, monkeypatch, tmp_path):
        cv = tmp_path / "cv.txt"
        cv.write_text("Python SQL", encoding="utf-8")
        started = {}

        async def fake_start(req):
            started["id"] = req.search_id
            return {"success": True}
        monkeypatch.setattr(main, "start_collection", fake_start)
        r = auth_client.post("/searches/quickstart", json={
            "resume_path": str(cv), "titles": ["Data Analyst"], "locations": ["Springfield"], "start": True})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["search"]["mode"] == "review" and body["resume"]["default"] is True
        assert started["id"] == body["search"]["id"]
        prof = auth_client.get("/profile").json()
        prof = prof.get("profile", prof)
        assert prof.get("target_job_titles") == ["Data Analyst"]
        listing = auth_client.get("/searches").json()
        assert listing["searches"][0]["stats"]["found"] == 0
        assert listing["outreach"]["enabled"] is False and listing["starter_packs"]

    def test_schedule_endpoints(self, auth_client, data_dir, monkeypatch):
        s = auth_client.post("/searches", json={"titles": ["Analyst"]}).json()["search"]
        assert s["schedule"] == {"kind": "off"}
        assert auth_client.put(f"/searches/{s['id']}/schedule", json={"kind": "custom", "cron": "* * * * *"}).status_code == 400
        assert auth_client.put("/searches/s_nope/schedule", json={"kind": "hourly"}).status_code == 404
        r = auth_client.put(f"/searches/{s['id']}/schedule", json={"kind": "daily", "time": "08:30", "tz": "UTC"})
        assert r.status_code == 200, r.text
        view = r.json()["schedule"]
        assert view["cron"] == "30 8 * * *" and view["next_run_at"] and not view["paused"]
        assert auth_client.post(f"/searches/{s['id']}/pause").json()["schedule"]["paused"] is True
        listing = auth_client.get("/schedules").json()
        assert listing["schedules"][0]["paused"] and listing["min_gap_minutes"] == 15
        assert auth_client.get("/searches").json()["searches"][0]["schedule_state"]["paused"] is True
        assert auth_client.post(f"/searches/{s['id']}/resume").json()["schedule"]["paused"] is False
        assert auth_client.post("/searches/s_nope/pause").status_code == 404
        # setup prompt + agent prompt carry the schedule, never secrets
        setup = auth_client.get("/agents/setup-prompt").json()["prompt"]
        assert s["id"] in setup and "30 8 * * *" in setup
        p = auth_client.get("/schedules/agent-prompt", params={"target": "codex"}).json()["prompt"]
        assert "--run-schedules-once" in p and "Analyst" in p
        assert auth_client.get("/schedules/agent-prompt", params={"target": "x"}).status_code == 400
        # tick: due search starts through the collector with the search id
        from core.searches import schedule
        schedule._mutate(s["id"], lambda st: st.update(next_run_at=1.0))
        monkeypatch.setattr(schedule, "next_after", lambda *a, **k: 4102444800.0)
        seen = {}

        async def fake_start(req, on_done=None):
            seen["id"] = req.search_id
            on_done([], ["Collection complete!"], False)
            return {"success": True}
        monkeypatch.setattr(main, "_start_collection", fake_start)
        res = auth_client.post("/schedules/tick").json()["results"]
        assert res == [{"id": s["id"], "action": "started"}] and seen["id"] == s["id"]
        assert schedule.load_state()[s["id"]]["last_outcome"] == "ok"

    def test_headless_runner_runs_due_searches_one_by_one(self, monkeypatch, capsys):
        seq = [[{"id": "s_a", "action": "started"}, ], [{"id": "s_b", "action": "started"}],
               [{"id": "s_c", "action": "skipped_other_device", "holder": "Laptop"}]]
        monkeypatch.setattr(main, "_schedule_tick", lambda: seq.pop(0))
        monkeypatch.setattr(main, "_collection_thread", None)
        assert main.run_schedules_once() == 2
        assert "skipped_other_device" in capsys.readouterr().out

    def test_validation_is_400(self, auth_client):
        assert auth_client.post("/searches", json={"titles": []}).status_code == 400
        assert auth_client.post("/resumes", json={"path": "/nope/cv.pdf"}).status_code == 400
        assert auth_client.delete("/searches/s_nope").status_code == 404
        assert auth_client.post("/searches/s_nope/run").status_code == 404

    def test_tag_review_and_tracker(self, auth_client, data_dir):
        s = auth_client.post("/searches", json={"titles": ["Analyst"], "mode": "review"}).json()["search"]
        auth_client.put("/searches/rules", json={"bans": {"companies": ["Example Staffing Agency"]}})
        self._jobs(data_dir, {
            "u1": {"url": "u1", "title": "Analyst", "company": "Acme", "status": "pending"},
            "u2": {"url": "u2", "title": "Analyst", "company": "Example Staffing Agency", "status": "pending"},
        })
        main._tag_new_jobs(s, ["u1", "u2"])
        from core import jobs_store
        assert jobs_store.get_job("u1")["review"] == "pending"
        assert jobs_store.get_job("u2")["status"] == "blocked"
        assert jobs_store.claim_next_pending() is None  # review-pending jobs are not claimable
        t = auth_client.get("/tracker").json()
        assert [q["url"] for q in t["review_queue"]] == ["u1"] and t["searches"][0]["skipped"] == 1
        assert auth_client.post("/jobs/review", json={"urls": ["u1"], "approve": True}).json()["updated"] == 1
        assert jobs_store.claim_next_pending()["url"] == "u1"

    def test_review_skip(self, auth_client, data_dir):
        self._jobs(data_dir, {"u1": {"url": "u1", "title": "A", "status": "pending", "review": "pending"}})
        auth_client.post("/jobs/review", json={"urls": ["u1"], "approve": False})
        from core import jobs_store
        j = jobs_store.get_job("u1")
        assert j["status"] == "blocked" and j["skip_reason"] == "Skipped in review"

    def test_ats_check_and_packs(self, auth_client, data_dir, tmp_path):
        cv = tmp_path / "cv.md"
        cv.write_text("Python and PostgreSQL developer", encoding="utf-8")
        rid = auth_client.post("/resumes", json={"path": str(cv)}).json()["resume"]["id"]
        r = auth_client.post("/resumes/ats-check", json={
            "resume_id": rid, "description": "Python, Kubernetes, Kubernetes, PostgreSQL"}).json()
        assert "kubernetes" in r["missing"] and "python" in r["matched"]
        assert auth_client.post("/starter-packs/retail/apply").json()["searches_added"] >= 1
        assert auth_client.post("/starter-packs/nope/apply").status_code == 404
        assert auth_client.delete(f"/resumes/{rid}").json()["success"]

    def test_outreach_drafts_only(self, auth_client, data_dir):
        self._jobs(data_dir, {"u1": {"url": "u1", "title": "Data Analyst", "company": "Acme Widgets", "status": "pending"}})
        assert auth_client.post("/outreach/draft", json={"job_url": "u1"}).status_code == 403
        auth_client.put("/outreach", json={"enabled": True})
        d = auth_client.post("/outreach/draft", json={"job_url": "u1"}).json()["draft"]
        assert d["sent"] is False and "Acme Widgets" in d["body"]
        assert len(auth_client.get("/outreach/drafts").json()) == 1
        assert auth_client.delete(f"/outreach/drafts/{d['id']}").json()["success"]


def test_resume_location_rules(tmp_path, monkeypatch):
    """M8: the old string-prefix check rejected macOS temp/external drives and accepted sibling
    home folders (/Users/al vs /Users/alex)."""
    from pathlib import Path
    home, sibling, vol = tmp_path / "al", tmp_path / "alex", tmp_path / "Volumes"
    for d in (home, sibling, vol):
        d.mkdir()
    (vol / "USB").symlink_to(home, target_is_directory=True) if os.name != "nt" else None
    if os.name == "nt":
        assert main._resume_location_ok((home / "cv.pdf").resolve())
        assert not main._resume_location_ok(Path(os.environ.get("WINDIR", r"C:\Windows")).resolve() / "cv.pdf")
        return
    monkeypatch.setattr(main, "_resume_roots", lambda: [home, vol])
    assert main._resume_location_ok((home / "cv.pdf").resolve())
    assert main._resume_location_ok((vol / "USB" / "cv.pdf").resolve())  # symlinked roots
    assert not main._resume_location_ok((sibling / "cv.pdf").resolve())   # sibling prefix
    assert not main._resume_location_ok(Path("/etc/cv.pdf"))
    assert main._resume_roots is not None
