"""A scripted, OpenAI-compatible "model" for end-to-end apply runs (M8).

It plays the LLM inside the real browser_use Agent: it reads the same browser-state text a
real model gets and answers with AgentOutput JSON that calls our real tools (autofill,
verify, guarded submit, email codes, confirmation). Everything else is production code: the
prompt, the tools, the guard, Chromium, the outcome decision and the job-status writes.
It is deterministic, needs no network and records every request for assertions.

The policy is generic (it does not know which mock site it is on): navigate, create an
account if a password box shows, enter an emailed code if asked, autofill, write essays,
upload the resume, verify, guarded-submit, and report the confirmation.
"""
from __future__ import annotations

import json
import re
import time
import threading
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from tests.fixtures.persona import essay_for

EL_RE = re.compile(r"\[(\d+)\]<(\w+)(.*?)\s*/>\s*$")
SUBMIT_RE = re.compile(r"\b(submit|save and continue|next|continue|create account|verify|review)\b", re.I)
CONFIRM_RE = re.compile(r"thank you for (applying|your application)|application (has been )?(received|submitted)", re.I)
CAPTCHA_RE = re.compile(r"verify you are (a )?human|i'?m not a robot|human check", re.I)


@dataclass
class El:
    index: int
    tag: str
    attrs: str
    text: str = ""
    label: str = ""

    def attr(self, name: str) -> str:
        m = re.search(rf"(?:^|\s){name}=(\S+)", self.attrs)
        return m.group(1) if m else ""


def _text(content) -> str:
    if isinstance(content, str):
        return content
    return "\n".join(p.get("text", "") for p in content if isinstance(p, dict) and p.get("type") == "text")


def parse_state(msg: str) -> dict:
    bs = msg[msg.find("<browser_state>"):] if "<browser_state>" in msg else ""
    tab = re.search(r"Current tab: (\w+)", bs)
    url = ""
    if tab:
        m = re.search(rf"Tab {tab.group(1)}: (\S+)", bs)
        url = m.group(1) if m else ""
    els: list[El] = []
    lines = bs.splitlines()
    prev = ""
    for i, line in enumerate(lines):
        m = EL_RE.search(line)
        if m:
            nxt = lines[i + 1].strip() if i + 1 < len(lines) and not EL_RE.search(lines[i + 1]) else ""
            els.append(El(int(m.group(1)), m.group(2).lower(), m.group(3), nxt, prev))
        elif line.strip():
            prev = line.strip()
    hist = msg[msg.find("<agent_history>"):msg.find("</agent_history>")] if "<agent_history>" in msg else ""
    last = hist.rsplit("<step>", 1)[-1] if "<step>" in hist else ""
    target = re.search(r"Go to (\S+?)(?: on LinkedIn\.| \(the employer)", msg)
    files = re.search(r"<available_file_paths>(.*?)\n", msg)
    return {"url": url, "els": els, "text": bs, "last": last, "target": target.group(1) if target else "",
            "file": files.group(1).strip() if files else ""}


@dataclass
class Policy:
    navigated: bool = False
    autofilled: dict = field(default_factory=dict)   # page sig -> times
    pending_essays: dict = field(default_factory=dict)  # sig -> [(key,label)]
    typed: set = field(default_factory=set)
    uploaded: set = field(default_factory=set)
    verified: dict = field(default_factory=dict)
    submitted: dict = field(default_factory=dict)
    account: dict = field(default_factory=dict)
    code: str = ""
    reported_confirmation: bool = False
    needs_human: bool = False
    finished: bool = False
    log: list = field(default_factory=list)
    empty_since: float = 0.0  # like a real model: give up on a page that stays empty for a minute

    def decide(self, st: dict) -> list[dict]:
        els, text, last = st["els"], st["text"], st["last"]
        if self.finished:
            return [{"done": {"text": "finished", "success": not self.needs_human}}]
        if not self.navigated:
            self.navigated = True
            return [{"navigate": {"url": st["target"], "new_tab": False}}]
        if CONFIRM_RE.search(text):
            if not self.reported_confirmation:
                self.reported_confirmation = True
                return [{"report_confirmation": {}}]
            self.finished = True
            return [{"done": {"text": "Application submitted; the page shows the confirmation.", "success": True}}]
        if CAPTCHA_RE.search(text):
            if not self.needs_human:
                self.needs_human = True
                return [{"report_needs_human": {"field_label": "CAPTCHA", "reason": "The site shows a human check."}}]
            self.finished = True
            return [{"done": {"text": "Blocked by a human check.", "success": False}}]
        inputs = [e for e in els if e.tag in ("input", "textarea", "select")]
        if inputs:
            self.empty_since = 0.0
        buttons = [e for e in els if e.tag in ("button", "a") or (e.tag == "input" and e.attr("type") == "submit")]
        if not inputs:
            if not buttons:
                if self.submitted:  # submitted and nothing on the page: claim done, the app decides
                    self.finished = True
                    return [{"done": {"text": "I clicked submit.", "success": True}}]
                self.empty_since = self.empty_since or time.monotonic()
                if time.monotonic() - self.empty_since > 60:
                    self.finished = True
                    self.needs_human = True
                    return [{"done": {"text": "The page never showed an application form.", "success": False}}]
                return [{"wait": {"seconds": 1}}]
        sig = (st["url"].split("?")[0], tuple(sorted(e.attr("id") or e.attr("name") for e in inputs)))
        pw = [e for e in inputs if e.attr("type") == "password"]
        if pw:
            step = self.account.setdefault(sig, 0)
            self.account[sig] += 1
            email = next((e for e in inputs if e.attr("type") == "email" or e.attr("id") == "email"), None)
            if step == 0 and email:
                return [{"input": {"index": email.index, "text": "<secret>account_email</secret>", "clear": True}}]
            if 1 <= step <= len(pw):
                return [{"fill_site_password": {"index": pw[step - 1].index, "mode": "new_account"}}]
            btn = next((b for b in buttons if SUBMIT_RE.search(b.text)), None)
            return [{"submit_form_guarded": {"index": btn.index}}] if btn else [{"wait": {"seconds": 1}}]
        code_box = next((e for e in inputs if "code" in (e.attr("id") + e.attr("name")).lower()), None)
        if code_box is not None:
            if not self.code:
                m = re.search(r"Verification code: (\d{4,8})", last)
                if m and self.log and self.log[-1] == "get_email_code":
                    self.code = m.group(1)
                else:
                    self.log.append("get_email_code")
                    return [{"get_email_code": {"sender_hint": "", "wait_seconds": 20}}]
            if sig not in self.typed:
                self.typed.add(sig)
                return [{"input": {"index": code_box.index, "text": self.code, "clear": True}}]
            btn = next((b for b in buttons if SUBMIT_RE.search(b.text)), None)
            return [{"submit_form_guarded": {"index": btn.index}}] if btn else [{"wait": {"seconds": 1}}]
        self.log.append("form")
        n = self.autofilled.get(sig, 0)
        if n == 0 or (self.verified.get(sig) == "retry" and n < 3):
            self.autofilled[sig] = n + 1
            self.verified.pop(sig, None)
            return [{"autofill_known_fields": {}}]
        if sig not in self.pending_essays:
            todo = []
            m = re.search(r"\{.*\}", last, re.S)
            if m:
                try:
                    todo = [(f.get("key", ""), f.get("label", "")) for f in json.loads(m.group()).get("answer_yourself", [])
                            if f.get("category") in ("essay", "cover_letter", "generic") and f.get("kind") in ("textarea", "text")]
                except ValueError:
                    todo = []
            self.pending_essays[sig] = todo
        while self.pending_essays[sig]:
            key, label = self.pending_essays[sig].pop(0)
            want = label.rstrip(" *").strip().lower()
            el = next((e for e in inputs if e.label.rstrip(" *").strip().lower() == want), None)
            if el is not None:
                return [{"input": {"index": el.index, "text": essay_for(label), "clear": True}}]
        for e in inputs:
            if e.attr("type") == "file" and (sig, e.index) not in self.uploaded and st["file"]:
                self.uploaded.add((sig, e.index))
                return [{"upload_file": {"index": e.index, "path": st["file"]}}]
        v = self.verified.get(sig)
        if v is None:
            self.verified[sig] = "asked"
            return [{"verify_form_complete": {}}]
        if v == "asked":
            if "NOT COMPLETE" in last:
                self.verified[sig] = "retry" if self.autofilled.get(sig, 0) < 3 else "stuck"
                if self.verified[sig] == "retry":
                    self.pending_essays.pop(sig, None)
                    return [{"scroll": {"down": True, "pages": 0.5}}]
            else:
                self.verified[sig] = "ok"
        if self.verified.get(sig) == "stuck":
            self.needs_human = True
            self.finished = True
            label = re.search(r"'label': '([^']+)'|\"label\": \"([^\"]+)\"", last)
            return [{"report_needs_human": {"field_label": (label.group(1) or label.group(2)) if label else "unknown",
                                            "reason": "Not in the profile or resume."}}]
        tries = self.submitted.get(sig, 0)
        if tries >= 2:
            self.finished = True
            return [{"done": {"text": "The site keeps rejecting the submission.", "success": False}}]
        btn = next((b for b in reversed(buttons) if SUBMIT_RE.search(b.text)), None)
        if btn is None:
            return [{"scroll": {"down": True, "pages": 1.0}}]
        self.submitted[sig] = tries + 1
        return [{"submit_form_guarded": {"index": btn.index}}]


class ScriptedLLM:
    """OpenAI-compatible chat-completions server driven by a Policy (one run at a time)."""

    def __init__(self, policy: Policy | None = None):
        self.policy = policy or Policy()
        self.requests: list[dict] = []
        self.actions: list[list[dict]] = []
        self.lock = threading.Lock()
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
                with outer.lock:
                    outer.requests.append(body)
                    msgs = body.get("messages") or []
                    if body.get("response_format"):
                        st = parse_state("\n".join(_text(m.get("content", "")) for m in msgs
                                                    if m.get("role") == "user"))
                        act = outer.policy.decide(st)
                        outer.actions.append(act)
                        content = json.dumps({"evaluation_previous_goal": "ok", "memory": "", "next_goal": "",
                                              "action": act})
                    else:  # memory extraction etc.: plain text
                        content = "[]"
                resp = {"id": "scripted", "object": "chat.completion", "created": 0, "model": "scripted",
                        "choices": [{"index": 0, "finish_reason": "stop",
                                     "message": {"role": "assistant", "content": content}}],
                        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}}
                data = json.dumps(resp).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.port = self.server.server_address[1]
        self.base_url = f"http://127.0.0.1:{self.port}/v1"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def reset(self):
        with self.lock:
            self.policy = Policy()
            self.requests.clear()
            self.actions.clear()

    def flat_actions(self) -> list[str]:
        return [next(iter(a)) for step in self.actions for a in step]

    def close(self):
        self.server.shutdown()
        self.server.server_close()
