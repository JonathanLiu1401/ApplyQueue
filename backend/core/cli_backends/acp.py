"""Minimal Agent Client Protocol (ACP) client for `grok agent stdio` (M3).

We are the *client*: we start the Grok Build CLI, give it our MCP server in
session/new, send one prompt and stream its updates. We declare no fs/terminal
capabilities, and approve permission requests only for our own MCP tools.
"""
from __future__ import annotations

import asyncio
import json
import os
import time

from .base import child_env, classify_text, kill_tree, popen_kwargs, strip_ansi

try:
    from core.token_guard import scrub
except ImportError:  # pragma: no cover
    from backend.core.token_guard import scrub

PROTOCOL_VERSION = 1
ACP_ARGS = ["agent", "stdio"]


class AcpError(Exception):
    def __init__(self, message: str, code: int | None = None):
        super().__init__(message)
        self.code = code


class AcpClient:
    def __init__(self, proc: asyncio.subprocess.Process, on_update=None, allow_tool=None):
        self.proc = proc
        self.on_update = on_update or (lambda u: None)
        self.allow_tool = allow_tool or (lambda tc: False)
        self._next = 1
        self._pending: dict[int, asyncio.Future] = {}
        self.last_activity = time.monotonic()
        self._reader = asyncio.ensure_future(self._read_loop())
        self.closed = asyncio.Event()

    async def _send(self, obj: dict) -> None:
        self.proc.stdin.write((json.dumps(obj) + "\n").encode())
        await self.proc.stdin.drain()

    async def request(self, method: str, params: dict, timeout: float | None = None):
        rid = self._next
        self._next += 1
        fut = asyncio.get_running_loop().create_future()
        self._pending[rid] = fut
        await self._send({"jsonrpc": "2.0", "id": rid, "method": method, "params": params})
        try:
            return await asyncio.wait_for(fut, timeout)
        finally:
            self._pending.pop(rid, None)

    async def notify(self, method: str, params: dict) -> None:
        await self._send({"jsonrpc": "2.0", "method": method, "params": params})

    async def _read_loop(self):
        try:
            while True:
                line = await self.proc.stdout.readline()
                if not line:
                    break
                self.last_activity = time.monotonic()
                try:
                    msg = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(msg, dict):
                    continue
                if "id" in msg and "method" not in msg:
                    fut = self._pending.get(msg["id"])
                    if fut and not fut.done():
                        if "error" in msg:
                            err = msg["error"] or {}
                            fut.set_exception(AcpError(str(err.get("message", "error")), err.get("code")))
                        else:
                            fut.set_result(msg.get("result"))
                elif "method" in msg and "id" in msg:
                    await self._handle_request(msg)
                elif msg.get("method") == "session/update":
                    try:
                        self.on_update((msg.get("params") or {}).get("update") or {})
                    except Exception:
                        pass
        finally:
            for fut in self._pending.values():
                if not fut.done():
                    fut.set_exception(AcpError("agent exited"))
            self.closed.set()

    async def _handle_request(self, msg: dict):
        method, rid, params = msg.get("method"), msg.get("id"), msg.get("params") or {}
        if method == "session/request_permission":
            tc = params.get("toolCall") or {}
            options = params.get("options") or []
            want = ("allow_once", "allow_always") if self.allow_tool(tc) else ("reject_once", "reject_always")
            choice = next((o for k in want for o in options if o.get("kind") == k), None)
            outcome = {"outcome": "selected", "optionId": choice["optionId"]} if choice else {"outcome": "cancelled"}
            await self._send({"jsonrpc": "2.0", "id": rid, "result": {"outcome": outcome}})
            return
        await self._send({"jsonrpc": "2.0", "id": rid,
                          "error": {"code": -32601, "message": f"{method} is not supported by ApplyQueue"}})


def pick_auth_method(methods: list[dict]) -> str | None:
    ids = [m.get("id") for m in methods or [] if isinstance(m, dict) and m.get("id")]
    if os.environ.get("XAI_API_KEY"):
        for i in ids:
            if "api_key" in i or "apikey" in i:
                return i
    for i in ids:
        if "cached" in i or "token" in i or "oauth" in i or "login" in i:
            return i
    return ids[0] if ids else None


async def _spawn(cmd: list[str], cwd: str | None):
    return await asyncio.create_subprocess_exec(
        *cmd, *ACP_ARGS, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE, cwd=cwd, env=child_env(), limit=16 * 1024 * 1024, **popen_kwargs())


async def _drain(stream, buf: list[str]):
    while True:
        line = await stream.readline()
        if not line:
            return
        buf.append(line.decode("utf-8", "replace"))
        del buf[:-60]


async def initialize(client: AcpClient, timeout: float = 30.0) -> dict:
    return await client.request("initialize", {
        "protocolVersion": PROTOCOL_VERSION,
        "clientCapabilities": {"fs": {"readTextFile": False, "writeTextFile": False}, "terminal": False},
        "clientInfo": {"name": "ApplyQueue", "version": "1"},
    }, timeout=timeout) or {}


def _is_auth_error(e: AcpError) -> bool:
    return e.code == -32000 or classify_text(str(e)) == "auth_needed" or "auth" in str(e).lower()


async def new_session(client: AcpClient, init: dict, params: dict, auth_timeout: float = 30.0) -> str:
    """session/new, authenticating with the CLI's own cached login only when it asks for it.

    A logged-out CLI would start an interactive sign-in; we time that out and report
    auth_needed instead, so the user signs in with the CLI's own login command.
    """
    methods = init.get("authMethods") or []
    if os.environ.get("XAI_API_KEY") and any("api" in (m.get("id") or "") for m in methods):
        await client.request("authenticate", {"methodId": pick_auth_method(methods), "_meta": {"headless": True}},
                             timeout=auth_timeout)
    try:
        sess = await client.request("session/new", params, timeout=90) or {}
    except AcpError as e:
        if not _is_auth_error(e):
            raise
        method = (init.get("_meta") or {}).get("defaultAuthMethodId") or pick_auth_method(methods)
        if not method:
            raise AcpError("Authentication required", -32000)
        try:
            await client.request("authenticate", {"methodId": method, "_meta": {"headless": True}},
                                 timeout=auth_timeout)
        except asyncio.TimeoutError:
            raise AcpError("not signed in (authentication required)", -32000)
        sess = await client.request("session/new", params, timeout=90) or {}
    sid = sess.get("sessionId")
    if not sid:
        raise AcpError("session/new returned no sessionId")
    return sid


async def run_prompt(cmd: list[str], prompt: str, mcp_cmd: str, mcp_args: list[str], workspace: str, *,
                     deadline: float, idle_timeout: float, cancel, on_event, allow_tool) -> dict:
    """Run one ACP session. Returns {status: ok|auth_needed|rate_limited|timeout|crash|cancelled, ...}."""
    try:
        proc = await _spawn(cmd, workspace)
    except OSError as e:
        return {"status": "crash", "error": f"could not start Grok CLI: {e}", "exit_code": None}
    err: list[str] = []
    drain = asyncio.ensure_future(_drain(proc.stderr, err))

    def on_update(u: dict):
        kind = u.get("sessionUpdate")
        if kind == "agent_message_chunk":
            text = (u.get("content") or {}).get("text", "")
            if text:
                on_event({"kind": "text", "text": text})
        elif kind in ("tool_call", "tool_call_update"):
            on_event({"kind": "tool", "text": u.get("title") or u.get("toolCallId", ""), "phase": u.get("status", "")})

    client = AcpClient(proc, on_update=on_update, allow_tool=allow_tool)
    status, error, stop = "ok", "", None
    try:
        init = await initialize(client, 60)
        sid = await new_session(client, init, {
            "cwd": workspace,
            "mcpServers": [{"name": "applyqueue", "command": mcp_cmd, "args": mcp_args, "env": []}],
        })
        task = asyncio.ensure_future(client.request(
            "session/prompt", {"sessionId": sid, "prompt": [{"type": "text", "text": prompt}]}))
        while not task.done():
            try:
                await asyncio.wait({task}, timeout=1.0)
            except asyncio.CancelledError:  # /apply/stop
                status = "cancelled"
                task.cancel()
                break
            now = time.monotonic()
            if task.done():
                break
            if cancel():
                status = "cancelled"
            elif now > deadline:
                status, error = "timeout", "overall time limit reached"
            elif now - client.last_activity > idle_timeout:
                status, error = "timeout", f"no output for {int(idle_timeout)}s"
            if status != "ok":
                try:
                    await client.notify("session/cancel", {"sessionId": sid})
                except Exception:
                    pass
                task.cancel()
                break
        if status == "ok":
            res = task.result() or {}
            stop = res.get("stopReason")
            if stop == "refusal":
                status, error = "crash", "agent refused the task"
    except AcpError as e:
        error = scrub(str(e))
        status = "auth_needed" if (e.code == -32000 and "auth" in error.lower()) else (
            classify_text(error + "\n" + "".join(err)) or "crash")
    except asyncio.TimeoutError:
        status, error = "timeout", "ACP request timed out"
    except asyncio.CancelledError:
        status = "cancelled"
    finally:
        if status in ("cancelled", "timeout"):
            kill_tree(proc.pid)
        try:
            proc.stdin.close()
        except Exception:
            pass
        try:
            await asyncio.wait_for(proc.wait(), 10)
        except asyncio.TimeoutError:
            kill_tree(proc.pid)
            try:
                await asyncio.wait_for(proc.wait(), 5)
            except asyncio.TimeoutError:
                pass
        drain.cancel()
    tail = scrub(strip_ansi("".join(err)))[-1500:]
    if status == "ok" and proc.returncode not in (0, None) and stop is None:
        status = classify_text(tail) or "crash"
    return {"status": status, "error": error, "stderr_tail": tail, "exit_code": proc.returncode,
            "stop_reason": stop}
