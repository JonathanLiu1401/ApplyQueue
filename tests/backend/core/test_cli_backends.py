"""M3: subscription CLI backends driven by fake vendor CLIs (no real CLI, no network)."""
import json
import os
import sys
from pathlib import Path

import pytest

from core import jobs_store
from core.cli_backends import SPECS, list_backends
from core.cli_backends.adapters import build_argv, detect, parse_line
from core.cli_backends.base import classify_text, resolve_command
from core.cli_backends.prompt import BROWSER_USE_ONLY, build_task_prompt, grok_bot_setup_prompt, mcp_policy_text
from core.cli_backends import runner

FAKE = Path(__file__).resolve().parents[2] / "fixtures" / "fake_clis" / "fake_cli.py"
FORMS = Path(__file__).resolve().parents[2] / "fixtures" / "forms"
VENDORS = ["grok", "cursor", "codex", "claude"]
JOB_URL = "https://jobs.example.com/acme/123"


def fake_cmd(vendor, mode):
    return [sys.executable, str(FAKE), "--as", vendor, "--mode", mode]


def settings_for(vendor, mode, **extra):
    cfg = {"enabled": True, "command": fake_cmd(vendor, mode), **extra}
    if vendor == "claude":
        cfg["terms_accepted_at"] = "2026-10-25T00:00:00Z"
    return {"agent_backend": f"cli:{vendor}", "cli_backends": {vendor: cfg}}


def seed_job(data_dir, url=JOB_URL, status="pending"):
    jobs = {url: {"url": url, "title": "Data Analyst", "company": "Acme", "status": status}}
    (data_dir / "jobs.json").write_text(json.dumps(jobs), encoding="utf-8")
    return jobs[url]


def records(path):
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


@pytest.fixture
def rec(tmp_path, monkeypatch):
    p = tmp_path / "fake_record.jsonl"
    monkeypatch.setenv("FAKE_CLI_RECORD", str(p))
    monkeypatch.setenv("JOB_APPLICANT_TOKEN", "sidecar-token-must-not-leak")
    return p


# ── detection ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("vendor", VENDORS)
def test_detect_logged_in(vendor, monkeypatch):
    for env in ("XAI_API_KEY", "CURSOR_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(env, raising=False)
    info = detect(SPECS[vendor], {"command": fake_cmd(vendor, "stream")})
    assert info["installed"] and info["version"] == f"fake-{vendor} 1.2.3"
    assert info["auth"]["state"] == "logged_in"
    if vendor == "codex":
        assert info["auth"]["method"] == "chatgpt"


@pytest.mark.parametrize("vendor", VENDORS)
def test_detect_logged_out(vendor, monkeypatch):
    for env in ("XAI_API_KEY", "CURSOR_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(env, raising=False)
    info = detect(SPECS[vendor], {"command": fake_cmd(vendor, "logged_out")})
    assert info["auth"]["state"] == "logged_out"


def test_detect_not_installed_and_gemini(monkeypatch, tmp_path):
    monkeypatch.setenv("PATH", str(tmp_path))
    monkeypatch.setattr("core.cli_backends.base._extra_dirs", lambda: [])
    assert detect(SPECS["cursor"], {})["installed"] is False
    gem = detect(SPECS["gemini"], {})
    assert gem["runnable"] is False and gem["auth"]["state"] == "not_applicable"


def test_detect_finds_shim_on_path(monkeypatch, tmp_path):
    """A real install is a binary on PATH; a shim script stands in for it."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    if sys.platform.startswith("win"):
        shim = bindir / "codex.cmd"
        shim.write_text(f'@echo off\r\n"{sys.executable}" "{FAKE}" --as codex --mode stream %*\r\n')
    else:
        shim = bindir / "codex"
        shim.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{FAKE}" --as codex --mode stream "$@"\n')
        shim.chmod(0o755)
    monkeypatch.setenv("PATH", str(bindir) + os.pathsep + os.environ.get("PATH", ""))
    got = resolve_command(SPECS["codex"], {})
    assert [os.path.normcase(c) for c in got] == [os.path.normcase(str(shim))]  # Windows may report .CMD
    info = detect(SPECS["codex"], {})
    assert info["installed"] and info["auth"]["state"] == "logged_in"


def test_list_backends_marks_selected():
    rows = list_backends({"agent_backend": "cli:codex", "cli_backends": {}}, check_auth=False)
    assert [r["id"] for r in rows] == ["grok", "cursor", "codex", "claude", "gemini"]
    assert [r["id"] for r in rows if r["selected"]] == ["codex"]
    claude = next(r for r in rows if r["id"] == "claude")
    assert claude["requires_terms"] and "your own" in claude["terms_text"]


# ── argv, parsers, prompt ───────────────────────────────────────────────

def test_argv_restricts_tools(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    cur = build_argv(SPECS["cursor"], ["agent"], "P", "py", ["main.py", "--mcp-server"], ws, tmp_path)
    assert "--approve-mcps" in cur and cur[cur.index("--workspace") + 1] == str(ws)
    perms = json.loads((ws / ".cursor" / "cli.json").read_text())["permissions"]
    assert "Shell(*)" in perms["deny"] and perms["allow"] == ["Mcp(applyqueue:*)"]
    cl = build_argv(SPECS["claude"], ["claude"], "P", "py", [], ws, tmp_path)
    assert "--strict-mcp-config" in cl and "Bash" in cl[cl.index("--disallowedTools") + 1]
    cx = build_argv(SPECS["codex"], ["codex"], "P", "C:\\py.exe", ["C:\\a\\main.py"], ws, tmp_path)
    assert cx[cx.index("--sandbox") + 1] == "read-only" and cx[-1] == "P"
    assert 'mcp_servers.applyqueue.command="C:/py.exe"' in cx


def test_parsers():
    assert parse_line("codex", '{"type":"turn.failed","error":{"message":"boom"}}')[0]["kind"] == "error"
    assert parse_line("codex", '{"type":"item.completed","item":{"type":"agent_message","text":"hi"}}')[0]["text"] == "hi"
    ev = parse_line("claude", '{"type":"result","subtype":"error_max_turns","is_error":true,"result":"x"}')[0]
    assert ev["kind"] == "result" and ev["is_error"]
    assert parse_line("cursor", "not json")[0]["kind"] == "info"
    assert classify_text("Error: Not logged in. Run `agent login`") == "auth_needed"
    assert classify_text("429 Too Many Requests") == "rate_limited"
    assert classify_text("panic: segfault") is None


def test_task_prompt_uses_mcp_tool_names():
    text = mcp_policy_text()
    assert not [w for w in BROWSER_USE_ONLY if w in text]
    prompt = build_task_prompt()
    for name in ("next_job", "finish_job", "skip_job", "fill_secret", "email_get_otp", "form_submit"):
        assert name in prompt
    assert "mail.google.com" not in prompt


def test_grok_bot_setup_prompt_has_no_secrets(monkeypatch):
    secret_values = ["sk-" + "a" * 40, "xai-" + "b" * 40, "you@example.com", "hunter2-password"]
    settings = {"sensitive_data": {"email": secret_values[2], "password": secret_values[3]},
                "cli_backends": {"cursor": {"enabled": True}}}
    monkeypatch.setenv("XAI_API_KEY", secret_values[1])
    monkeypatch.setenv("OPENAI_API_KEY", secret_values[0])
    text = grok_bot_setup_prompt(list_backends(settings, check_auth=False))
    for v in secret_values:
        assert v not in text
    from core.token_guard import find_tokens
    assert find_tokens(text) == []
    assert "grok login" in text and "codex login" in text and "API key only" in text
    assert str(Path.home()) not in text


# ── runner: each vendor × stream / auth / crash / timeout ──────────────

@pytest.mark.parametrize("vendor", VENDORS)
async def test_run_skip_via_mcp(vendor, aq_data_dir, rec):
    seed_job(aq_data_dir)
    logs = []
    res = await runner.run_job(vendor, {"url": JOB_URL, "title": "Data Analyst"}, settings=settings_for(vendor, "mcp_skip"),
                               data_dir=aq_data_dir, log=logs.append, timeout_s=120, idle_timeout_s=90)
    assert res["outcome"] == "finished", (res, logs)
    assert res["status"] == "blocked"
    job = jobs_store.get_job(JOB_URL)
    assert job["status"] == "blocked" and "video" in job["skip_reason"]
    assert job["agent_backend"] == f"cli:{vendor}"
    recs = records(rec)
    assert not any(r.get("has_api_token") for r in recs), "sidecar token leaked into the CLI env"
    tools = next(r["tools"] for r in recs if "tools" in r)
    for t in ("browser_navigate", "form_fill", "fill_secret", "email_get_otp", "next_job", "skip_job", "finish_job"):
        assert t in tools
    nxt = [r for r in recs if r.get("tool") == "next_job"]
    assert nxt[0]["result"]["job"]["url"] == JOB_URL and nxt[-1]["result"]["done"] is True
    if vendor == "grok":
        perms = {r["permission"]: r["outcome"] for r in recs if "permission" in r}
        assert perms["applyqueue: next_job"]["optionId"] == "a"
        assert perms["Run shell: rm -rf ~"]["optionId"] == "r"
        assert next(r for r in recs if "fs_reply" in r)["fs_reply"]["error"]["code"] == -32601


@pytest.mark.parametrize("vendor", VENDORS)
async def test_run_stream_without_finish_fails_honestly(vendor, aq_data_dir, rec):
    seed_job(aq_data_dir)
    logs = []
    res = await runner.run_job(vendor, {"url": JOB_URL}, settings=settings_for(vendor, "stream"),
                               data_dir=aq_data_dir, log=logs.append, timeout_s=60, idle_timeout_s=30)
    assert res["status"] == "failed", (res, logs)
    assert "without calling finish_job" in res["message"]
    assert any("Looking at the job now" in line for line in logs)


@pytest.mark.parametrize("vendor", VENDORS)
async def test_run_auth_needed_requeues(vendor, aq_data_dir, rec):
    seed_job(aq_data_dir)
    res = await runner.run_job(vendor, {"url": JOB_URL}, settings=settings_for(vendor, "auth"),
                               data_dir=aq_data_dir, log=lambda m: None, timeout_s=60, idle_timeout_s=30)
    assert res["outcome"] == "auth_needed", res
    job = jobs_store.get_job(JOB_URL)
    assert job["status"] == "pending" and SPECS[vendor].login_command in job["error"]


@pytest.mark.parametrize("vendor", ["cursor", "grok"])
async def test_run_rate_limited_requeues(vendor, aq_data_dir, rec):
    seed_job(aq_data_dir)
    res = await runner.run_job(vendor, {"url": JOB_URL}, settings=settings_for(vendor, "ratelimit"),
                               data_dir=aq_data_dir, log=lambda m: None, timeout_s=60, idle_timeout_s=30)
    assert res["outcome"] == "rate_limited", res
    assert jobs_store.get_job(JOB_URL)["status"] == "pending"


@pytest.mark.parametrize("vendor", VENDORS)
async def test_run_crash_marks_failed(vendor, aq_data_dir, rec):
    seed_job(aq_data_dir)
    res = await runner.run_job(vendor, {"url": JOB_URL}, settings=settings_for(vendor, "crash"),
                               data_dir=aq_data_dir, log=lambda m: None, timeout_s=60, idle_timeout_s=30)
    assert res["outcome"] == "error" and res["status"] == "failed", res
    job = jobs_store.get_job(JOB_URL)
    assert "stopped unexpectedly" in job["error"]


@pytest.mark.parametrize("vendor", VENDORS)
async def test_run_idle_timeout_kills_cli(vendor, aq_data_dir, rec):
    seed_job(aq_data_dir)
    res = await runner.run_job(vendor, {"url": JOB_URL}, settings=settings_for(vendor, "timeout"),
                               data_dir=aq_data_dir, log=lambda m: None, timeout_s=60, idle_timeout_s=3)
    assert res["outcome"] == "timeout" and res["status"] == "failed", res
    assert "timed out" in jobs_store.get_job(JOB_URL)["error"]


async def test_claude_requires_terms(aq_data_dir):
    seed_job(aq_data_dir)
    s = settings_for("claude", "stream")
    s["cli_backends"]["claude"].pop("terms_accepted_at")
    res = await runner.run_job("claude", {"url": JOB_URL}, settings=s, data_dir=aq_data_dir, log=lambda m: None)
    assert res["outcome"] == "terms_required"
    assert jobs_store.get_job(JOB_URL)["status"] == "pending"


async def test_gemini_is_api_key_only(aq_data_dir):
    res = await runner.run_job("gemini", {"url": JOB_URL}, settings={}, data_dir=aq_data_dir, log=lambda m: None)
    assert res["outcome"] == "not_supported"


def _chromium_ok() -> bool:
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            return Path(p.chromium.executable_path).exists()
    except Exception:
        return False


@pytest.mark.skipif(not _chromium_ok(), reason="Playwright Chromium not installed")
async def test_run_apply_confirmed_by_page(aq_data_dir, rec, tmp_path):
    """End to end: a fake Codex drives our MCP browser to a confirmation page and calls finish_job."""
    page = tmp_path / "thanks.html"
    page.write_text("<html><body><h1>Thank you for applying!</h1>"
                    "<p>Your application has been submitted.</p></body></html>", encoding="utf-8")
    url = page.as_uri()
    seed_job(aq_data_dir, url=url)
    res = await runner.run_job("codex", {"url": url}, settings=settings_for("codex", "mcp_apply"),
                               data_dir=aq_data_dir, log=lambda m: None, timeout_s=180, idle_timeout_s=150,
                               run_options={"allow_local_urls": True, "headless": True,
                                            "browser_profile_dir": str(tmp_path / "profile")})
    job = jobs_store.get_job(url)
    assert res["outcome"] == "finished", (res, job)
    assert job["status"] == "submitted_unconfirmed" and job["page_confirmation"]
    assert job["agent_backend"] == "cli:codex"
