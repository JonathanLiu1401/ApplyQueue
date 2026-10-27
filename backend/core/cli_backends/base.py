"""Shared plumbing for subscription CLI backends (M3): specs, discovery, processes."""
from __future__ import annotations

import json
import os
import re
import shutil
import signal
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

try:
    from core.token_guard import scrub
except ImportError:  # pragma: no cover
    from backend.core.token_guard import scrub

ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
AUTH_RE = re.compile(
    r"not (?:logged|signed) in|not authenticated|unauthenticated|authentication (?:required|failed)|"
    r"please (?:run )?[`'\"]?[\w/-]*\s?(?:log ?in|login|auth login)|run [`'\"]?[\w-]+ (?:auth )?login|"
    r"invalid api key|\b401\b|unauthori[sz]ed|login required|session expired|token (?:has )?expired",
    re.I,
)
RATE_RE = re.compile(r"rate[ -]?limit|\b429\b|usage limit|quota exceeded|too many requests|limit reached", re.I)

# Our env vars the vendor CLI must never inherit.
_STRIP_ENV = ("JOB_APPLICANT_TOKEN",)


@dataclass(frozen=True)
class CliSpec:
    id: str
    name: str
    vendor: str
    binaries: tuple[str, ...]
    login_command: str
    install_hint: str
    docs_url: str
    transport: str  # "acp" | "stream-json" | "codex-json" | "none"
    version_args: tuple[str, ...] = ("--version",)
    auth_args: tuple[str, ...] = ()
    api_key_env: tuple[str, ...] = ()
    requires_terms: bool = False
    runnable: bool = True
    notes: str = ""
    login_methods: tuple[str, ...] = field(default_factory=tuple)
    # Copyable setup steps shown in Settings: (label, shell command) pairs.
    install_steps: tuple[tuple[str, str], ...] = ()
    login_steps: tuple[tuple[str, str], ...] = ()


def strip_ansi(text: str) -> str:
    return ANSI_RE.sub("", text or "")


def child_env(extra: dict | None = None) -> dict:
    env = {k: v for k, v in os.environ.items() if k not in _STRIP_ENV}
    env.setdefault("NO_COLOR", "1")
    env["APPLYQUEUE_CHILD"] = "1"
    if extra:
        env.update(extra)
    return env


def _extra_dirs() -> list[Path]:
    """Install locations that are often missing from a GUI app's PATH (macOS Finder launches)."""
    home = Path.home()
    dirs = [home / ".local" / "bin", home / ".grok" / "bin", home / ".claude" / "local", home / ".cursor" / "bin",
            home / ".npm-global" / "bin", home / ".bun" / "bin", home / ".volta" / "bin"]
    if sys.platform == "darwin":
        dirs += [Path("/opt/homebrew/bin"), Path("/usr/local/bin")]
    elif sys.platform.startswith("win"):
        appdata = os.environ.get("APPDATA")
        local = os.environ.get("LOCALAPPDATA")
        if appdata:
            dirs.append(Path(appdata) / "npm")
        if local:
            dirs += [Path(local) / "Programs" / "cursor-agent", Path(local) / "grok" / "bin"]
    else:
        dirs += [Path("/usr/local/bin"), Path("/usr/bin")]
    return dirs


def resolve_command(spec: CliSpec, cfg: dict | None) -> list[str] | None:
    """The argv prefix that starts this CLI, or None when it is not installed."""
    cfg = cfg or {}
    cmd = cfg.get("command")
    if isinstance(cmd, list) and cmd:
        return [str(c) for c in cmd]
    path = (cfg.get("path") or "").strip()
    if path:
        p = Path(path).expanduser()
        return [str(p)] if p.is_file() else None
    for b in spec.binaries:
        found = shutil.which(b)
        if found:
            return [found]
    exts = [".exe", ".cmd", ".bat", ""] if sys.platform.startswith("win") else [""]
    for d in _extra_dirs():
        for b in spec.binaries:
            for ext in exts:
                cand = d / f"{b}{ext}"
                if cand.is_file() and (sys.platform.startswith("win") or os.access(cand, os.X_OK)):
                    return [str(cand)]
    return None


def run_quick(argv: list[str], timeout: float = 15.0, cwd: str | None = None) -> tuple[int | None, str]:
    """Run a short status command. Returns (exit_code or None on timeout/error, scrubbed output)."""
    kwargs = {}
    if sys.platform.startswith("win"):
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        cp = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL,
                            env=child_env({"NO_OPEN_BROWSER": "1"}), cwd=cwd, encoding="utf-8",
                            errors="replace", **kwargs)
    except subprocess.TimeoutExpired:
        return None, "timed out"
    except OSError as e:
        return None, str(e)
    return cp.returncode, scrub(strip_ansi((cp.stdout or "") + ("\n" + cp.stderr if cp.stderr else "")))[:4000]


def classify_text(text: str) -> str | None:
    if not text:
        return None
    if RATE_RE.search(text):
        return "rate_limited"
    if AUTH_RE.search(text):
        return "auth_needed"
    return None


def popen_kwargs() -> dict:
    if sys.platform.startswith("win"):
        return {"creationflags": getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
                | getattr(subprocess, "CREATE_NO_WINDOW", 0)}
    return {"start_new_session": True}


def kill_tree(pid: int) -> None:
    """Kill a CLI and everything it started (MCP server, browser driver)."""
    if sys.platform.startswith("win"):
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(pid, sig)
        except (ProcessLookupError, PermissionError, OSError):
            return
        if sig == signal.SIGTERM:
            import time
            for _ in range(20):
                try:
                    os.killpg(pid, 0)
                except OSError:
                    return
                time.sleep(0.1)


def mcp_launch(run_file: Path, data_dir: Path) -> tuple[str, list[str]]:
    """How a CLI starts our MCP server: the sidecar binary itself (frozen) or main.py (dev)."""
    args = ["--mcp-server", "--run-file", str(run_file), "--data-dir", str(data_dir)]
    if getattr(sys, "frozen", False):
        return sys.executable, args
    main_py = Path(__file__).resolve().parents[2] / "main.py"
    return sys.executable, [str(main_py), *args]


def first_json_object(text: str) -> dict | None:
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        data = json.loads(text[start:end + 1])
    except ValueError:
        return None
    return data if isinstance(data, dict) else None
