"""M9: the model provider rejects the API key in the middle of a run (revoked, expired, out of credit).

Before: the job was marked failed (it was never the job's fault), and the worker went on to "fail"
every other queued job the same way in about 4 s each; failed jobs are never picked up again, so a
revoked key quietly emptied the whole queue. Now the job stays pending and the run stops."""
import asyncio
import http.server
import json
import threading

import pytest

from tests.e2e.test_agent_apply_e2e import env, run_job  # noqa: F401

pytest.importorskip("browser_use")


class _Revoked(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        self.rfile.read(int(self.headers.get("content-length") or 0))
        body = json.dumps({"error": {"message": "Incorrect API key provided: sk-...abcd.",
                                     "type": "invalid_request_error", "code": "invalid_api_key"}}).encode()
        self.send_response(401)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


@pytest.fixture
def revoked_llm(env, monkeypatch):
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Revoked)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    import core.shared_config as sc
    from browser_use.llm import ChatOpenAI
    monkeypatch.setattr(sc, "get_llm", lambda *a, **k: ChatOpenAI(
        model="gpt-x", base_url=f"http://127.0.0.1:{srv.server_address[1]}/v1", api_key="revoked", max_retries=0))
    yield
    srv.shutdown()


def test_rejected_key_keeps_the_job_pending(env, revoked_llm):
    url = env["ats"].job_url("gh")
    status, job, _ = run_job(env, url)
    assert status == "llm_auth"
    assert job["status"] == "pending" and job["error"] == "llm_key_rejected"
    assert env["ats"].submissions_for("gh") == []


def test_rejected_key_stops_the_run_instead_of_failing_the_queue(env, revoked_llm):
    from core import jobs_store
    aj = env["apply_jobs"]
    urls = [env["ats"].job_url(s) for s in ("gh", "lever", "ashby")]
    jf, _ = jobs_store._paths()
    jf.parent.mkdir(parents=True, exist_ok=True)
    jf.write_text(json.dumps({u: {"url": u, "title": "Platform Engineer", "company": "Example Corp",
                                  "status": "pending"} for u in urls}), "utf-8")
    from tests.fixtures.persona import PROFILE, QA

    async def go():
        q = asyncio.Queue()
        for u in urls:
            q.put_nowait({"url": u, "title": "Platform Engineer", "company": "Example Corp"})
        stats = {}
        await aj.worker("W1", 1, q, PROFILE, QA, [], False, stats)
        return stats, q.qsize()

    stats, left = asyncio.run(go())
    assert stats == {"llm_auth": 1} and left == 2
    assert {jobs_store.get_job(u)["status"] for u in urls} == {"pending"}


@pytest.mark.parametrize("text,expected", [
    ("Error code: 401 - {'error': {'code': 'invalid_api_key'}}", True),
    ("anthropic: authentication_error: invalid x-api-key", True),
    ("Your credit balance is too low to access the Anthropic API", True),
    ("insufficient_quota", True),
    ("Element 'Unauthorized access notice' not found on page", False),
    ("Navigation failed: 401 on https://ats.example/apply", False),
    ("", False),
])
def test_classifier(text, expected):
    from core.llm_factory import is_llm_auth_error
    assert is_llm_auth_error(text) is expected
