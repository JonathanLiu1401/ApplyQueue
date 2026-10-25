"""Run one job through a subscription CLI backend (M3).

One CLI process per job: we write a run file, start the vendor's CLI with our MCP
server attached, stream its output to the log, enforce overall + idle timeouts and
then decide the job's status from ApplyQueue's own evidence (run_state.finalize_job).
"""
from __future__ import annotations

import asyncio
import json
import secrets
import threading
import time
from datetime import datetime
from pathlib import Path

from . import acp
from .adapters import MCP_NAME, SPECS, build_argv, parse_line
from .base import child_env, classify_text, kill_tree, mcp_launch, popen_kwargs, resolve_command, strip_ansi
from .prompt import build_task_prompt

try:
    from core import job_status as js
    from core import jobs_store
    from core.run_state import RunState, finalize_job
    from core.token_guard import scrub
    from core.mcp_server import tool_names
except ImportError:  # pragma: no cover
    from backend.core import job_status as js
    from backend.core import jobs_store
    from backend.core.run_state import RunState, finalize_job
    from backend.core.token_guard import scrub
    from backend.core.mcp_server import tool_names

STOP_OUTCOMES = frozenset({"auth_needed", "rate_limited", "cancelled", "not_installed", "terms_required",
                           "not_supported", "busy"})
DEFAULT_TIMEOUT_S = 25 * 60
DEFAULT_IDLE_S = 6 * 60
_LOCKS: dict[str, threading.Lock] = {k: threading.Lock() for k in SPECS}

_AUTH_HELP = "{name} is not signed in. Run `{login}` in a terminal, then try again."
_RATE_HELP = "{name} hit its plan's usage or rate limit; the job was put back in the queue."


def backend_cfg(settings: dict, backend_id: str) -> dict:
    return ((settings or {}).get("cli_backends") or {}).get(backend_id) or {}


def is_our_tool(tool_call: dict) -> bool:
    """ACP permission requests: allow only tools of our own MCP server."""
    blob = json.dumps(tool_call or {}).lower()
    if MCP_NAME in blob:
        return True
    title = str((tool_call or {}).get("title") or "")
    return any(title == n or title.endswith("." + n) or title.endswith("__" + n) for n in tool_names())


async def _run_stream(spec_id: str, argv: list[str], cwd: Path, *, deadline: float, idle: float, cancel,
                      on_event) -> dict:
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv, stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE, cwd=str(cwd), env=child_env(), limit=16 * 1024 * 1024,
            **popen_kwargs())
    except OSError as e:
        return {"status": "crash", "error": f"could not start the CLI: {e}", "exit_code": None}
    err: list[str] = []

    async def drain():
        while True:
            line = await proc.stderr.readline()
            if not line:
                return
            err.append(line.decode("utf-8", "replace"))
            del err[:-80]

    drain_task = asyncio.ensure_future(drain())
    status, error, result_ev, errors = "ok", "", None, []
    last_seen = time.monotonic()
    try:
        while True:
            now = time.monotonic()
            if cancel():
                status = "cancelled"
                break
            if now > deadline:
                status, error = "timeout", "overall time limit reached"
                break
            try:
                line = await asyncio.wait_for(proc.stdout.readline(), timeout=min(idle, max(0.5, deadline - now), 2.0))
            except asyncio.TimeoutError:
                if time.monotonic() - last_seen > idle:
                    status, error = "timeout", f"no output for {int(idle)}s"
                    break
                continue
            except ValueError:  # one line over the stream limit
                status, error = "crash", "the CLI wrote an oversized line"
                break
            except asyncio.CancelledError:  # /apply/stop: kill the CLI below, report cancelled
                status = "cancelled"
                break
            if not line:
                break
            last_seen = time.monotonic()
            for ev in parse_line(spec_id, line.decode("utf-8", "replace")):
                if ev["kind"] == "result":
                    result_ev = ev
                elif ev["kind"] == "error":
                    errors.append(ev.get("text", ""))
                on_event(ev)
    finally:
        if status != "ok":
            kill_tree(proc.pid)
        try:
            await asyncio.wait_for(proc.wait(), 15)
        except asyncio.TimeoutError:
            kill_tree(proc.pid)
            try:
                await asyncio.wait_for(proc.wait(), 5)
            except asyncio.TimeoutError:
                pass
        try:
            await asyncio.wait_for(drain_task, 2)
        except (asyncio.TimeoutError, Exception):
            drain_task.cancel()
    tail = scrub(strip_ansi("".join(err)))[-1500:]
    if status == "ok":
        failure_text = "\n".join(errors + ([result_ev["text"]] if result_ev and result_ev.get("is_error") else []))
        if proc.returncode != 0 or (result_ev and result_ev.get("is_error")) or errors:
            status = classify_text(failure_text + "\n" + tail) or ("crash" if proc.returncode != 0 or
                                                                   (result_ev and result_ev.get("is_error")) else "ok")
            error = scrub(failure_text)[-500:] or f"exited with code {proc.returncode}"
    return {"status": status, "error": error, "stderr_tail": tail, "exit_code": proc.returncode}


def _new_run_dir(data_dir: Path, backend_id: str) -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return data_dir / "runs" / f"{stamp}-{backend_id}-{secrets.token_hex(3)}"


async def run_job(backend_id: str, job: dict, *, settings: dict, data_dir: Path, log=print, cancel=None,
                  timeout_s: float | None = None, idle_timeout_s: float | None = None,
                  run_options: dict | None = None) -> dict:
    """Apply to one job. Returns {"outcome": ..., "status": job status or None, "message": str}.

    outcome: finished | auth_needed | rate_limited | cancelled | not_installed | terms_required |
             not_supported | busy | error
    """
    cancel = cancel or (lambda: False)
    spec = SPECS.get(backend_id)
    if not spec or not spec.runnable:
        return {"outcome": "not_supported", "status": None, "message": f"{backend_id} cannot run applications"}
    cfg = backend_cfg(settings, backend_id)
    if spec.requires_terms and not cfg.get("terms_accepted_at"):
        return {"outcome": "terms_required", "status": None,
                "message": f"Accept the {spec.name} terms in Settings → Agent backends first"}
    cmd = resolve_command(spec, cfg)
    if not cmd:
        return {"outcome": "not_installed", "status": None, "message": f"{spec.name} is not installed"}
    lock = _LOCKS[backend_id]
    if not lock.acquire(blocking=False):
        return {"outcome": "busy", "status": None, "message": f"{spec.name} is already running a job"}
    try:
        return await _run_locked(spec, cmd, cfg, job, settings=settings, data_dir=Path(data_dir), log=log,
                                 cancel=cancel, timeout_s=timeout_s, idle_timeout_s=idle_timeout_s,
                                 run_options=run_options or {})
    finally:
        lock.release()


async def _run_locked(spec, cmd, cfg, job, *, settings, data_dir, log, cancel, timeout_s, idle_timeout_s,
                      run_options) -> dict:
    url = job["url"]
    run_dir = _new_run_dir(data_dir, spec.id)
    workspace = run_dir / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    options = {"blocked_domains": settings.get("blocked_domains") or [], **run_options}
    state = RunState.create(run_dir, backend=f"cli:{spec.id}", job_urls=[url], options=options)
    jobs_store.claim_job(url)
    mcp_cmd, mcp_args = mcp_launch(state.path, data_dir)
    prompt = build_task_prompt()
    timeout_s = float(timeout_s or cfg.get("timeout_s") or DEFAULT_TIMEOUT_S)
    idle = float(idle_timeout_s or cfg.get("idle_timeout_s") or DEFAULT_IDLE_S)
    deadline = time.monotonic() + timeout_s
    text_buf: list[str] = []

    def on_event(ev: dict):
        kind, text = ev.get("kind"), scrub(ev.get("text") or "")
        if kind == "text":
            text_buf.append(text)
            if len(text) > 2 or "\n" in text:
                log(f"[{spec.id}] {text.strip()[:300]}")
        elif kind == "tool":
            if ev.get("phase") in ("started", "pending", "in_progress", ""):
                log(f"[{spec.id}] tool: {text[:120]}")
        elif kind in ("error", "info") and text:
            log(f"[{spec.id}] {text[:300]}")

    log(f"[{spec.id}] starting {spec.name} for {job.get('title') or url}")
    try:
        if spec.transport == "acp":
            res = await acp.run_prompt(cmd, prompt, mcp_cmd, mcp_args, str(workspace), deadline=deadline,
                                       idle_timeout=idle, cancel=cancel, on_event=on_event, allow_tool=is_our_tool)
        else:
            argv = build_argv(spec, cmd, prompt, mcp_cmd, mcp_args, workspace, run_dir, cfg)
            res = await _run_stream(spec.id, argv, workspace, deadline=deadline, idle=idle, cancel=cancel,
                                    on_event=on_event)
    except asyncio.CancelledError:
        res = {"status": "cancelled", "error": "", "exit_code": None}
    except Exception as e:  # never leave the job stuck in_progress
        res = {"status": "crash", "error": scrub(str(e))[:500], "exit_code": None}

    result = state.result_for(url)
    summary = scrub("".join(text_buf))[-1000:]
    if result:
        log(f"[{spec.id}] finished: {result['status']}")
        return {"outcome": "finished", "status": result["status"], "message": result.get("reason", ""),
                "run_dir": str(run_dir)}
    led = state.ledger(url)
    touched_submit = bool(led.get("submit_clicks"))
    status = res.get("status")
    if status in ("auth_needed", "rate_limited", "cancelled") and not touched_submit:
        msg = {"auth_needed": _AUTH_HELP, "rate_limited": _RATE_HELP}.get(status, "Cancelled")
        msg = msg.format(name=spec.name, login=spec.login_command)
        jobs_store.update_job(url, status=js.PENDING, error=msg if status != "cancelled" else None)
        log(f"[{spec.id}] {msg}")
        return {"outcome": status, "status": js.PENDING, "message": msg, "run_dir": str(run_dir)}
    reason = {
        "timeout": f"{spec.name} timed out ({res.get('error') or 'time limit'})",
        "crash": f"{spec.name} stopped unexpectedly: {res.get('error') or res.get('stderr_tail', '')[-300:]}",
        "ok": f"{spec.name} ended without calling finish_job",
        "auth_needed": _AUTH_HELP.format(name=spec.name, login=spec.login_command),
        "rate_limited": _RATE_HELP.format(name=spec.name),
        "cancelled": "Cancelled after the submit button was clicked",
    }.get(status, f"{spec.name}: {status}")
    out = finalize_job(state, url, agent_claimed_success=False, agent_summary=summary, failure_reason=reason,
                       source=f"runner:{status}")
    log(f"[{spec.id}] {out['status']}: {out['reason'][:200]}")
    return {"outcome": "finished" if status == "ok" else ("error" if status == "crash" else status),
            "status": out["status"], "message": out["reason"], "run_dir": str(run_dir)}
