"""M3: ApplyQueue's stdio MCP server (protocol, queue tools, guards)."""
import json
import subprocess
import sys
from pathlib import Path

import pytest

from core import jobs_store
from core.mcp_server import SUPPORTED_PROTOCOLS, ApplyQueueTools, StdioServer, tool_names
from core.run_state import RunState

MAIN = Path(__file__).resolve().parents[3] / "backend" / "main.py"
URL = "https://jobs.example.com/acme/1"


def make_state(d, urls=(URL,), **options):
    (d / "jobs.json").write_text(json.dumps({u: {"url": u, "status": "pending", "title": "Analyst"} for u in urls}))
    return RunState.create(d / "runs" / "r1", backend="cli:test", job_urls=list(urls), options=options)


def text(result):
    return json.loads(result["content"][0]["text"])


async def test_protocol_and_queue(aq_data_dir):
    state = make_state(aq_data_dir)
    server = StdioServer(ApplyQueueTools(state, aq_data_dir))
    init = await server.handle({"id": 1, "method": "initialize", "params": {"protocolVersion": "2025-03-26"}})
    assert init["result"]["protocolVersion"] == "2025-03-26"
    old = await server.handle({"id": 2, "method": "initialize", "params": {"protocolVersion": "1999-01-01"}})
    assert old["result"]["protocolVersion"] == SUPPORTED_PROTOCOLS[0]
    assert await server.handle({"method": "notifications/initialized"}) is None
    assert (await server.handle({"id": 3, "method": "nope"}))["error"]["code"] == -32601
    tools = (await server.handle({"id": 4, "method": "tools/list"}))["result"]["tools"]
    assert {t["name"] for t in tools} == set(tool_names())
    assert all(t["inputSchema"]["type"] == "object" for t in tools)
    t = server.tools
    job = text(await t.call("next_job", {}))
    assert job["job"]["url"] == URL
    assert text(await t.call("next_job", {}))["job"]["url"] == URL  # unfinished current job is repeated
    await t.call("skip_job", {"reason": "not a real application"})
    assert text(await t.call("next_job", {}))["done"] is True
    assert jobs_store.get_job(URL)["status"] == "blocked"


async def test_url_guard_and_secrets(aq_data_dir):
    state = make_state(aq_data_dir, blocked_domains=["blocked.example.com"])
    t = ApplyQueueTools(state, aq_data_dir)
    await t.call("next_job", {})
    for bad in ("file:///etc/passwd", "http://127.0.0.1:8743/settings", "http://169.254.169.254/",
                "https://blocked.example.com/job"):
        res = await t.call("browser_navigate", {"url": bad})
        assert res.get("isError"), bad
    res = await t.call("fill_secret", {"ref": "0:1", "secret": "site_password_raw"})
    assert res.get("isError")
    bad = await t.call("finish_job", {"result": "maybe"})
    assert bad.get("isError")


async def test_finish_without_evidence_is_not_submitted(aq_data_dir):
    state = make_state(aq_data_dir)
    t = ApplyQueueTools(state, aq_data_dir)
    t.browser.page = None  # no browser opened: no page evidence
    await t.call("next_job", {})
    out = text(await t.call("finish_job", {"result": "submitted", "summary": "trust me"}))
    job = jobs_store.get_job(URL)
    assert job["status"] in ("needs_human", "failed") and job["agent_claimed_success"] is True
    assert out["status"] == job["status"]


async def test_finish_needs_human_records_reason(aq_data_dir):
    state = make_state(aq_data_dir)
    t = ApplyQueueTools(state, aq_data_dir)
    await t.call("next_job", {})
    await t.call("finish_job", {"result": "needs_human", "summary": "Site asks for a portfolio PDF"})
    job = jobs_store.get_job(URL)
    assert job["status"] == "needs_human"
    assert any("portfolio" in f["reason"] for f in job["needs_human_fields"])


def test_main_dispatches_before_web_app(aq_data_dir):
    """`main.py --mcp-server` answers MCP on stdout without starting the web app."""
    state = make_state(aq_data_dir)
    msgs = [{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}]
    cp = subprocess.run([sys.executable, str(MAIN), "--mcp-server", "--run-file", str(state.path),
                         "--data-dir", str(aq_data_dir)], input="".join(json.dumps(m) + "\n" for m in msgs),
                        capture_output=True, text=True, timeout=60)
    lines = [json.loads(line) for line in cp.stdout.splitlines()]
    assert lines[0]["result"]["serverInfo"]["name"] == "applyqueue"
    assert len(lines[1]["result"]["tools"]) == len(tool_names())
    assert not (aq_data_dir / ".api_token").exists()


def test_missing_run_file_exits_2(aq_data_dir):
    cp = subprocess.run([sys.executable, str(MAIN), "--mcp-server", "--run-file", str(aq_data_dir / "nope.json")],
                        capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL)
    assert cp.returncode == 2 and cp.stdout == ""
