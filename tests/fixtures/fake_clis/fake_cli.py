#!/usr/bin/env python3
"""Fake vendor CLI for ApplyQueue's M3 tests. Not a real CLI; never contacts a network.

    fake_cli.py --as {grok,cursor,codex,claude} --mode MODE [vendor args...]

MODE: stream | mcp_skip | mcp_apply | auth | ratelimit | crash | timeout | logged_out
Every run appends JSON lines to $FAKE_CLI_RECORD (argv, env checks, tool results) so tests
can assert on what ApplyQueue passed in.
"""
import json
import os
import subprocess
import sys
import time


def record(**kw):
    path = os.environ.get("FAKE_CLI_RECORD")
    if path:
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(kw) + "\n")


def out(obj):
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


class McpClient:
    def __init__(self, command, args):
        self.p = subprocess.Popen([command, *args], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  stderr=subprocess.DEVNULL, text=True, encoding="utf-8")
        self.n = 0
        init = self.call_raw("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                            "clientInfo": {"name": "fake", "version": "0"}})
        self.p.stdin.write(json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n")
        self.p.stdin.flush()
        self.server_info = init.get("result", {}).get("serverInfo")

    def call_raw(self, method, params):
        self.n += 1
        self.p.stdin.write(json.dumps({"jsonrpc": "2.0", "id": self.n, "method": method, "params": params}) + "\n")
        self.p.stdin.flush()
        while True:
            line = self.p.stdout.readline()
            if not line:
                return {"error": {"message": "mcp server exited"}}
            msg = json.loads(line)
            if msg.get("id") == self.n:
                return msg

    def tool(self, name, args=None):
        res = self.call_raw("tools/call", {"name": name, "arguments": args or {}}).get("result") or {}
        text = "".join(c.get("text", "") for c in res.get("content", []) if c.get("type") == "text")
        try:
            data = json.loads(text)
        except ValueError:
            data = {"text": text}
        record(tool=name, is_error=bool(res.get("isError")), result=data)
        return data

    def close(self):
        try:
            self.p.stdin.close()
            self.p.wait(timeout=20)
        except Exception:
            self.p.kill()


def drive_mcp(mode, command, args):
    c = McpClient(command, args)
    tools = [t["name"] for t in c.call_raw("tools/list", {}).get("result", {}).get("tools", [])]
    record(server=c.server_info, tools=tools)
    job = c.tool("next_job")
    if mode == "mcp_skip":
        c.tool("skip_job", {"reason": "video interview funnel"})
    elif mode == "mcp_apply":
        c.tool("get_candidate_context")
        c.tool("browser_navigate", {"url": job["job"]["url"]})
        c.tool("finish_job", {"result": "submitted", "summary": "Applied (fake)"})
    c.tool("next_job")
    c.close()


# ── one-shot vendors (cursor / claude / codex) ────────────────────────────

def mcp_from_argv(vendor, argv):
    if vendor == "cursor":
        ws = argv[argv.index("--workspace") + 1]
        with open(os.path.join(ws, ".cursor", "mcp.json"), encoding="utf-8") as f:
            s = json.load(f)["mcpServers"]["applyqueue"]
        return s["command"], s["args"]
    if vendor == "claude":
        with open(argv[argv.index("--mcp-config") + 1], encoding="utf-8") as f:
            s = json.load(f)["mcpServers"]["applyqueue"]
        return s["command"], s["args"]
    cmd = args = None
    for i, a in enumerate(argv):
        if a == "-c":
            k, _, v = argv[i + 1].partition("=")
            if k == "mcp_servers.applyqueue.command":
                cmd = json.loads(v)
            elif k == "mcp_servers.applyqueue.args":
                args = json.loads(v)
    return cmd, args


def emit_text(vendor, text):
    if vendor == "codex":
        out({"type": "item.completed", "item": {"id": "i1", "type": "agent_message", "text": text}})
    else:
        out({"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": text}]}})


def emit_result(vendor, ok, text=""):
    if vendor == "codex":
        if ok:
            out({"type": "turn.completed", "usage": {}})
        else:
            out({"type": "error", "message": text})
            out({"type": "turn.failed", "error": {"message": text}})
    else:
        out({"type": "result", "subtype": "success" if ok else "error", "is_error": not ok, "result": text})


def status_command(vendor, mode, argv):
    logged_out = mode in ("logged_out", "auth")
    if vendor == "claude":
        out({"loggedIn": not logged_out, "authMethod": None if logged_out else "claude.ai"})
        return 1 if logged_out else 0
    if vendor == "codex":
        print("Not logged in" if logged_out else "Logged in using ChatGPT")
        return 1 if logged_out else 0
    if vendor == "grok":
        print("You are not authenticated.\n" if logged_out else "", "Available models:\n  * grok-fake")
        return 0
    print("Not logged in" if logged_out else "Logged in")
    return 0


def one_shot(vendor, mode, argv):
    record(vendor=vendor, argv=argv, has_api_token=("JOB_APPLICANT_TOKEN" in os.environ), cwd=os.getcwd())
    if vendor == "cursor":
        out({"type": "system", "subtype": "init", "model": "fake"})
    if mode == "auth":
        msg = {"cursor": "Error: Not logged in. Run `agent login` first.",
               "claude": "Invalid API key · Please run /login",
               "codex": "401 Unauthorized: not logged in, run codex login"}[vendor]
        if vendor == "cursor":
            sys.stderr.write(msg + "\n")
        else:
            emit_result(vendor, False, msg)
        return 1
    if mode == "ratelimit":
        emit_result(vendor, False, "429 Too Many Requests: usage limit reached")
        return 1
    emit_text(vendor, "Looking at the job now.")
    if mode == "crash":
        sys.stderr.write("panic: fake crash in the CLI\n")
        return 3
    if mode == "timeout":
        time.sleep(600)
        return 0
    if mode.startswith("mcp"):
        cmd, args = mcp_from_argv(vendor, argv)
        drive_mcp(mode, cmd, args)
    emit_text(vendor, "Done.")
    emit_result(vendor, True, "done")
    return 0


# ── Grok (ACP over stdio) ─────────────────────────────────────────────────

def acp_server(mode):
    record(vendor="grok", argv=sys.argv[1:], has_api_token=("JOB_APPLICANT_TOKEN" in os.environ))
    mcp = None
    for line in sys.stdin:
        msg = json.loads(line)
        mid, method, params = msg.get("id"), msg.get("method"), msg.get("params") or {}
        if method == "initialize":
            out({"jsonrpc": "2.0", "id": mid, "result": {
                "protocolVersion": 1, "agentCapabilities": {}, "authMethods": [{"id": "grok.com", "name": "Grok"}]}})
        elif method == "authenticate":
            out({"jsonrpc": "2.0", "id": mid, "error": {"code": -32000, "message": "Not logged in"}})
        elif method == "session/new":
            if mode == "auth":
                out({"jsonrpc": "2.0", "id": mid, "error": {"code": -32000, "message": "Authentication required"}})
                continue
            mcp = (params.get("mcpServers") or [None])[0]
            record(session_new=params)
            out({"jsonrpc": "2.0", "id": mid, "result": {"sessionId": "s1"}})
        elif method == "session/prompt":
            def upd(text):
                out({"jsonrpc": "2.0", "method": "session/update", "params": {"sessionId": "s1", "update": {
                    "sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": text}}}})
            upd("Looking at the job now.\n")
            if mode == "crash":
                sys.stderr.write("panic: fake crash in grok\n")
                sys.exit(3)
            if mode == "timeout":
                time.sleep(600)
            if mode == "ratelimit":
                out({"jsonrpc": "2.0", "id": mid, "error": {"code": -32603, "message": "429 rate limit reached"}})
                continue
            opts = [{"optionId": "a", "name": "Allow", "kind": "allow_once"},
                    {"optionId": "r", "name": "Reject", "kind": "reject_once"}]
            for i, title in enumerate(["applyqueue: next_job", "Run shell: rm -rf ~"]):
                out({"jsonrpc": "2.0", "id": 900 + i, "method": "session/request_permission", "params": {
                    "sessionId": "s1", "toolCall": {"toolCallId": f"t{i}", "title": title}, "options": opts}})
                reply = json.loads(sys.stdin.readline())
                record(permission=title, outcome=reply.get("result", {}).get("outcome"))
            out({"jsonrpc": "2.0", "id": 950, "method": "fs/read_text_file", "params": {"path": "/etc/hosts"}})
            record(fs_reply=json.loads(sys.stdin.readline()))
            if mode.startswith("mcp") and mcp:
                drive_mcp(mode, mcp["command"], mcp["args"])
            upd("Done.\n")
            out({"jsonrpc": "2.0", "id": mid, "result": {"stopReason": "end_turn"}})
    return 0


def main():
    argv = sys.argv[1:]
    vendor = argv[argv.index("--as") + 1]
    mode = argv[argv.index("--mode") + 1] if "--mode" in argv else os.environ.get("FAKE_CLI_MODE", "stream")
    rest = argv[4:] if "--mode" in argv else argv[2:]
    if rest[:1] == ["--version"]:
        print(f"fake-{vendor} 1.2.3")
        return 0
    status = {"cursor": ["status"], "codex": ["login", "status"], "claude": ["auth", "status"], "grok": ["models"]}
    if rest == status[vendor]:
        return status_command(vendor, mode, rest)
    if vendor == "grok":
        if rest[:2] == ["agent", "stdio"]:
            return acp_server(mode)
        return 2
    return one_shot(vendor, mode, rest)


if __name__ == "__main__":
    sys.exit(main())
