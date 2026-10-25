"""Per-vendor CLI adapters: specs, login detection, argv builders, stream parsers.

Rules every adapter follows (PLAN §3):
- We start the vendor's own, unmodified CLI under the user's own login. We never read,
  copy, store or proxy its token files; detection only runs the CLI's status command.
- The CLI talks to the job site only through our MCP server (`applyqueue`), with its
  own shell/file tools disabled where the CLI allows it.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from .base import CliSpec, classify_text, first_json_object, resolve_command, run_quick, strip_ansi

MCP_NAME = "applyqueue"

GROK = CliSpec(
    id="grok", name="Grok Build CLI", vendor="xAI", binaries=("grok",), login_command="grok login",
    install_hint="Install the Grok Build CLI, then run `grok login` once in a terminal "
                 "(`grok login --device-auth` on a remote machine).",
    docs_url="https://docs.x.ai/", transport="acp", api_key_env=("XAI_API_KEY",),
    auth_args=("models",),
    login_methods=("SuperGrok / X Premium login", "XAI_API_KEY"),
)
CURSOR = CliSpec(
    id="cursor", name="Cursor CLI", vendor="Cursor", binaries=("agent", "cursor-agent"),
    login_command="agent login", install_hint="curl https://cursor.com/install -fsS | bash, then `agent login`.",
    docs_url="https://cursor.com/docs/cli/overview", transport="stream-json", auth_args=("status",),
    api_key_env=("CURSOR_API_KEY",), login_methods=("Cursor account", "CURSOR_API_KEY"),
)
CODEX = CliSpec(
    id="codex", name="Codex CLI", vendor="OpenAI", binaries=("codex",), login_command="codex login",
    install_hint="npm install -g @openai/codex, then `codex login` (Sign in with ChatGPT or an API key).",
    docs_url="https://developers.openai.com/codex/cli", transport="codex-json", auth_args=("login", "status"),
    api_key_env=("OPENAI_API_KEY",), login_methods=("Sign in with ChatGPT", "OpenAI API key"),
    notes="Sign in with ChatGPT tokens stay on this device; ApplyQueue never reads or syncs them.",
)
CLAUDE = CliSpec(
    id="claude", name="Claude Code", vendor="Anthropic", binaries=("claude",), login_command="claude auth login",
    install_hint="npm install -g @anthropic-ai/claude-code, then `claude auth login`.",
    docs_url="https://code.claude.com/docs/en/cli-reference", transport="stream-json", auth_args=("auth", "status"),
    api_key_env=("ANTHROPIC_API_KEY",), requires_terms=True,
    login_methods=("Claude subscription login", "ANTHROPIC_API_KEY"),
    notes="Opt-in. Runs the unmodified Claude Code CLI under your own login; an Anthropic API key "
          "(Settings → AI model) stays the default way to use Claude.",
)
GEMINI = CliSpec(
    id="gemini", name="Gemini", vendor="Google", binaries=(), login_command="",
    install_hint="Use a Gemini API key under Settings → AI model.", docs_url="https://ai.google.dev/",
    transport="none", runnable=False,
    notes="API key only: ApplyQueue does not drive the Gemini CLI with a consumer Google login.",
)

SPECS: dict[str, CliSpec] = {s.id: s for s in (GROK, CURSOR, CODEX, CLAUDE, GEMINI)}

CLAUDE_TERMS = (
    "Claude Code is Anthropic's product and is governed by Anthropic's terms for your plan. "
    "ApplyQueue only starts the unmodified `claude` CLI on this computer, signed in with your own "
    "account; it never reads, stores or forwards your Claude login or token. Automated job "
    "applications may count against your plan's usage limits, and you are responsible for "
    "complying with Anthropic's terms. An Anthropic API key remains the default way to use Claude."
)


# ── Login detection ─────────────────────────────────────────────────────

def detect_auth(spec: CliSpec, cmd: list[str]) -> dict:
    """{'state': logged_in|logged_out|unknown, 'method': str|None, 'detail': str}. Never returns account ids."""
    for env in spec.api_key_env:
        if os.environ.get(env):
            return {"state": "logged_in", "method": "api_key", "detail": f"{env} is set"}
    if not spec.auth_args:
        return {"state": "unknown", "method": None, "detail": ""}
    code, out = run_quick(cmd + list(spec.auth_args))
    text = strip_ansi(out)
    low = text.lower()
    if spec.id == "claude":
        data = first_json_object(text) or {}
        if "loggedIn" in data:
            return {"state": "logged_in" if data.get("loggedIn") else "logged_out",
                    "method": data.get("authMethod") or None, "detail": ""}
    if code is None:
        return {"state": "unknown", "method": None, "detail": "status command did not finish"}
    if any(s in low for s in ("not logged in", "not authenticated", "logged out", "no credentials")):
        return {"state": "logged_out", "method": None, "detail": ""}
    if code == 0 and ("logged in" in low or "authenticated" in low or spec.id in ("codex", "claude", "grok")):
        method = None
        if "chatgpt" in low:
            method = "chatgpt"
        elif "api key" in low or "api_key" in low:
            method = "api_key"
        return {"state": "logged_in", "method": method, "detail": ""}
    if code != 0:
        return {"state": "logged_out" if classify_text(text) == "auth_needed" or code == 1 else "unknown",
                "method": None, "detail": ""}
    return {"state": "unknown", "method": None, "detail": ""}


def detect(spec: CliSpec, cfg: dict | None, check_auth: bool = True) -> dict:
    cfg = cfg or {}
    out = {
        "id": spec.id, "name": spec.name, "vendor": spec.vendor, "transport": spec.transport,
        "runnable": spec.runnable, "requires_terms": spec.requires_terms,
        "terms_text": CLAUDE_TERMS if spec.requires_terms else None,
        "terms_accepted": bool(cfg.get("terms_accepted_at")), "enabled": bool(cfg.get("enabled")),
        "login_command": spec.login_command, "install_hint": spec.install_hint, "docs_url": spec.docs_url,
        "login_methods": list(spec.login_methods), "notes": spec.notes, "custom_path": cfg.get("path") or "",
        "installed": False, "path": None, "version": None, "auth": {"state": "unknown", "method": None},
    }
    if not spec.runnable:
        out["auth"] = {"state": "not_applicable", "method": "api_key"}
        return out
    cmd = resolve_command(spec, cfg)
    if not cmd:
        return out
    out["installed"] = True
    out["path"] = cmd[-1] if len(cmd) == 1 else " ".join(Path(c).name for c in cmd)
    code, ver = run_quick(cmd + list(spec.version_args), timeout=10)
    if code == 0 and ver.strip():
        out["version"] = ver.strip().splitlines()[0][:80]
    if check_auth:
        out["auth"] = detect_auth(spec, cmd)
    return out


# ── argv builders ───────────────────────────────────────────────────────

def _fwd(p) -> str:
    return str(p).replace("\\", "/")


def write_cursor_config(workspace: Path, mcp_cmd: str, mcp_args: list[str]) -> None:
    d = workspace / ".cursor"
    d.mkdir(parents=True, exist_ok=True)
    (d / "mcp.json").write_text(json.dumps({"mcpServers": {MCP_NAME: {"command": mcp_cmd, "args": mcp_args}}},
                                           indent=2), encoding="utf-8")
    (d / "cli.json").write_text(json.dumps({"permissions": {
        "allow": [f"Mcp({MCP_NAME}:*)"], "deny": ["Shell(*)", "Write(**)"]}}, indent=2), encoding="utf-8")


def build_argv(spec: CliSpec, cmd: list[str], prompt: str, mcp_cmd: str, mcp_args: list[str],
               workspace: Path, run_dir: Path, options: dict | None = None) -> list[str]:
    options = options or {}
    model = (options.get("model") or "").strip()
    if spec.id == "cursor":
        write_cursor_config(workspace, mcp_cmd, mcp_args)
        argv = cmd + ["-p", prompt, "--output-format", "stream-json", "--force", "--trust", "--approve-mcps",
                      "--workspace", str(workspace)]
        return argv + (["--model", model] if model else [])
    if spec.id == "claude":
        cfg_path = run_dir / "claude-mcp.json"
        cfg_path.write_text(json.dumps({"mcpServers": {MCP_NAME: {"command": mcp_cmd, "args": mcp_args}}}),
                            encoding="utf-8")
        argv = cmd + ["-p", prompt, "--output-format", "stream-json", "--verbose", "--mcp-config", str(cfg_path),
                      "--strict-mcp-config", "--allowedTools", f"mcp__{MCP_NAME}",
                      "--disallowedTools", "Bash Edit Write MultiEdit NotebookEdit WebFetch WebSearch Read Glob Grep Task",
                      "--max-turns", str(int(options.get("max_turns") or 150))]
        return argv + (["--model", model] if model else [])
    if spec.id == "codex":
        argv = cmd + ["exec", "--json", "--skip-git-repo-check", "--sandbox", "read-only", "-C", str(workspace),
                      "-c", 'approval_policy="never"',
                      "-c", f"mcp_servers.{MCP_NAME}.command={json.dumps(_fwd(mcp_cmd))}",
                      "-c", f"mcp_servers.{MCP_NAME}.args={json.dumps([_fwd(a) for a in mcp_args])}",
                      "-c", f"mcp_servers.{MCP_NAME}.tool_timeout_sec=180",
                      "-c", f"mcp_servers.{MCP_NAME}.startup_timeout_sec=60"]
        if model:
            argv += ["-m", model]
        return argv + [prompt]
    raise ValueError(f"{spec.id} has no one-shot argv (transport {spec.transport})")


# ── stream parsers ──────────────────────────────────────────────────────
# Each returns a list of events: {"kind": text|tool|result|error|info, "text": str, "is_error": bool}

def _texts(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(c.get("text", "") for c in content if isinstance(c, dict) and c.get("type") == "text")
    return ""


def parse_line(spec_id: str, line: str) -> list[dict]:
    line = line.strip()
    if not line:
        return []
    try:
        ev = json.loads(line)
    except ValueError:
        return [{"kind": "info", "text": strip_ansi(line)[:500]}]
    if not isinstance(ev, dict):
        return []
    t = ev.get("type")
    if spec_id == "codex":
        if t == "item.completed" or t == "item.started":
            item = ev.get("item") or {}
            it = item.get("type") or item.get("item_type")
            if it in ("agent_message", "assistant_message") and t == "item.completed":
                return [{"kind": "text", "text": item.get("text", "")}]
            if it == "mcp_tool_call":
                return [{"kind": "tool", "text": f"{item.get('server', '')}.{item.get('tool', '')}",
                         "phase": "completed" if t == "item.completed" else "started"}]
            if it == "error":
                return [{"kind": "error", "text": item.get("message", "")}]
            return []
        if t == "turn.failed":
            return [{"kind": "error", "text": ((ev.get("error") or {}).get("message") or "turn failed")}]
        if t == "error":
            return [{"kind": "error", "text": ev.get("message", "error")}]
        if t == "turn.completed":
            return [{"kind": "result", "text": "", "is_error": False}]
        return []
    # cursor + claude share the stream-json shape
    if t == "assistant":
        msg = ev.get("message") or {}
        out = []
        text = _texts(msg.get("content"))
        if text:
            out.append({"kind": "text", "text": text})
        for c in msg.get("content") or []:
            if isinstance(c, dict) and c.get("type") == "tool_use":
                out.append({"kind": "tool", "text": c.get("name", ""), "phase": "started"})
        return out
    if t == "tool_call":
        call = ev.get("tool_call") or {}
        name = next(iter(call.keys()), "") if isinstance(call, dict) else ""
        inner = call.get(name) if isinstance(call, dict) else None
        if isinstance(inner, dict) and inner.get("args", {}).get("toolName"):
            name = inner["args"]["toolName"]
        return [{"kind": "tool", "text": name, "phase": ev.get("subtype", "")}]
    if t == "result":
        is_err = bool(ev.get("is_error")) or (ev.get("subtype") not in (None, "success"))
        return [{"kind": "result", "text": str(ev.get("result") or "")[:2000], "is_error": is_err}]
    if t == "system" and ev.get("subtype") == "init":
        return [{"kind": "info", "text": f"session started ({ev.get('model', '')})".strip()}]
    if t == "error":
        return [{"kind": "error", "text": str(ev.get("message") or ev.get("error") or "error")}]
    return []
