"""Subscription CLI backends (M3): Grok Build CLI, Cursor CLI, Codex, Claude Code (opt-in).

Gemini is API-key only; Grok Bot is supported through a generated setup prompt.
"""
from .adapters import CLAUDE_TERMS, SPECS, detect
from .prompt import build_task_prompt, grok_bot_setup_prompt, schedule_runner_prompt

ORDER = ("grok", "cursor", "codex", "claude", "gemini")


def list_backends(settings: dict, check_auth: bool = True) -> list[dict]:
    cfgs = (settings or {}).get("cli_backends") or {}
    selected = (settings or {}).get("agent_backend") or "browser_use"
    out = []
    for bid in ORDER:
        info = detect(SPECS[bid], cfgs.get(bid), check_auth=check_auth)
        info["selected"] = selected == f"cli:{bid}"
        out.append(info)
    return out


__all__ = ["SPECS", "ORDER", "CLAUDE_TERMS", "detect", "list_backends", "build_task_prompt",
           "grok_bot_setup_prompt", "schedule_runner_prompt"]
