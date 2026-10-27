"""xAI / Grok as a first-class provider: routing to https://api.x.ai/v1, the live model list
with its static fallback, the sidecar endpoints, and the e2e loopback override.

Network is never real: a loopback HTTP server stands in for xAI, so the requests the OpenAI
client and httpx actually send (path, Authorization header, model) are asserted end to end.
"""
from __future__ import annotations

import asyncio
import http.server
import json
import threading

import pytest

from core import e2e_hooks, llm_factory
from browser_use.llm import ChatOpenAI

FAKE_KEY = "xai-unit-test-not-a-real-key"


class FakeXai:
    """GET /v1/models and POST /v1/chat/completions, recording what reached it."""

    def __init__(self, models=None, status=200, raw=None):
        self.requests: list[dict] = []
        outer = self

        class H(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, code, obj=None, raw_body=None):
                data = raw_body if raw_body is not None else json.dumps(obj).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                outer.requests.append({"method": "GET", "path": self.path,
                                       "auth": self.headers.get("Authorization")})
                if status != 200:
                    return self._send(status, {"error": "nope"})
                if raw is not None:
                    return self._send(200, raw_body=raw)
                self._send(200, {"object": "list", "data": models or []})

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
                outer.requests.append({"method": "POST", "path": self.path, "body": body,
                                       "auth": self.headers.get("Authorization")})
                self._send(200, {"id": "x", "object": "chat.completion", "created": 0, "model": body.get("model"),
                                 "choices": [{"index": 0, "finish_reason": "stop",
                                              "message": {"role": "assistant", "content": "hello"}}],
                                 "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}})

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.root = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()


@pytest.fixture
def fake_xai(monkeypatch):
    servers = []

    def make(**kw):
        srv = FakeXai(**kw)
        servers.append(srv)
        monkeypatch.setattr(llm_factory, "xai_base_url", lambda: srv.root + "/v1")
        return srv

    yield make
    for s in servers:
        s.close()


MODELS = [
    {"id": "grok-4.20-reasoning", "output_modalities": ["text"]},
    {"id": "grok-imagine-image", "output_modalities": ["image"]},
    {"id": "grok-imagine-video"},
    {"id": "grok-4.7", "output_modalities": ["text"], "aliases": ["grok-latest"]},
    {"id": "grok-code-fast-9", "output_modalities": ["text"]},
    {"id": "grok-voice-1"},
]


# ── create_llm routing ───────────────────────────────────────────────────

def test_create_llm_xai_targets_api_x_ai():
    llm = llm_factory.create_llm({"provider": "xai", "xai": {"api_key": f"  {FAKE_KEY} ", "model": "grok-4.6"}})
    assert isinstance(llm, ChatOpenAI)
    assert llm.model == "grok-4.6"
    assert str(llm.base_url).rstrip("/") == "https://api.x.ai/v1"
    assert llm.api_key == FAKE_KEY


def test_create_llm_xai_default_model():
    llm = llm_factory.create_llm({"provider": "xai"})
    assert llm.model == llm_factory.XAI_DEFAULT_MODEL == "grok-4.7"


def test_xai_chat_goes_to_xai_chat_completions(fake_xai):
    srv = fake_xai()
    llm = llm_factory.create_llm({"provider": "xai", "xai": {"api_key": FAKE_KEY, "model": "grok-4.7"}})
    out = asyncio.run(llm_factory.test_connection(llm))
    assert out == "Model responded: hello"
    post = [r for r in srv.requests if r["method"] == "POST"][-1]
    assert post["path"] == "/v1/chat/completions"
    assert post["auth"] == f"Bearer {FAKE_KEY}"
    assert post["body"]["model"] == "grok-4.7"


# ── model list ───────────────────────────────────────────────────────────

def test_models_from_api_filtered_and_sorted(fake_xai):
    srv = fake_xai(models=MODELS)
    res = asyncio.run(llm_factory.list_xai_models(FAKE_KEY))
    assert res["success"] is True and res["source"] == "api"
    # known models first in the fallback order, then the rest; image/video/voice dropped
    assert res["models"] == ["grok-4.7", "grok-4.20-reasoning", "grok-code-fast-9"]
    assert srv.requests[-1]["path"] == "/v1/models"
    assert srv.requests[-1]["auth"] == f"Bearer {FAKE_KEY}"
    assert FAKE_KEY not in json.dumps(res)


def test_models_no_key_uses_fallback_without_calling(fake_xai):
    srv = fake_xai(models=MODELS)
    res = asyncio.run(llm_factory.list_xai_models("  "))
    assert res["success"] is False and res["source"] == "fallback"
    assert res["models"] == llm_factory.XAI_FALLBACK_MODELS
    assert srv.requests == []


@pytest.mark.parametrize("status,needle", [(401, "did not accept"), (403, "did not accept"), (500, "HTTP 500")])
def test_models_http_errors_fall_back(fake_xai, status, needle):
    fake_xai(status=status)
    res = asyncio.run(llm_factory.list_xai_models(FAKE_KEY))
    assert res["source"] == "fallback" and needle in res["message"]
    assert res["models"] == llm_factory.XAI_FALLBACK_MODELS
    assert FAKE_KEY not in res["message"]


@pytest.mark.parametrize("raw", [b"not json", b'{"data": []}', b'{"data": [{"id": "grok-imagine-image"}]}'])
def test_models_bad_or_empty_reply_falls_back(fake_xai, raw):
    fake_xai(raw=raw)
    res = asyncio.run(llm_factory.list_xai_models(FAKE_KEY))
    assert res["success"] is False and res["models"] == llm_factory.XAI_FALLBACK_MODELS


def test_models_unreachable_falls_back(monkeypatch):
    monkeypatch.setattr(llm_factory, "xai_base_url", lambda: "http://127.0.0.1:9/v1")
    res = asyncio.run(llm_factory.list_xai_models(FAKE_KEY, timeout=2))
    assert res["source"] == "fallback" and "Cannot reach" in res["message"]


# ── e2e loopback override ────────────────────────────────────────────────

def test_e2e_hook_points_xai_at_loopback_only(monkeypatch):
    original = llm_factory.xai_base_url
    monkeypatch.setattr(llm_factory, "xai_base_url", original)  # restored after the test
    monkeypatch.setenv("APPLYQUEUE_E2E_MOCK_XAI", "https://evil.example.com")
    assert "xai" not in e2e_hooks.apply()
    assert llm_factory.xai_base_url() == "https://api.x.ai/v1"
    monkeypatch.setenv("APPLYQUEUE_E2E_MOCK_XAI", "http://127.0.0.1:4567")
    assert e2e_hooks.apply()["xai"] == "http://127.0.0.1:4567"
    assert llm_factory.xai_base_url() == "http://127.0.0.1:4567/v1"
