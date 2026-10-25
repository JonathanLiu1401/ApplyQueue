"""ApplyQueue MCP server (M3): our tools for subscription CLI agents.

Grok Build, Cursor, Codex and Claude Code connect to this server over stdio
(``applyqueue-backend --mcp-server --run-file <run.json>``). The CLI's model
plans the application; every action that matters runs here, in ApplyQueue's
code: the browser (Playwright on the ApplyQueue browser profile), the form
scanner/filler, secrets (typed straight into the page, never returned), email
codes (via the linked mailbox API) and the job queue.

The agent can never mark a job submitted: ``finish_job`` re-reads the page,
checks for unanswered fields and failed form POSTs and decides the status
itself (``run_state.finalize_job``).

Protocol: MCP over stdio, newline-delimited JSON-RPC 2.0. Deliberately
dependency-free (no MCP SDK) so the frozen sidecar stays small.
"""
from __future__ import annotations

import asyncio
import base64
import ipaddress
import json
import os
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable
from urllib.parse import urlparse

try:
    from core import form_guard as fg
    from core.config import get_data_dir
    from core.outcome import detect_confirmation
    from core.run_state import RunState, finalize_job, skip_job
except ImportError:  # pragma: no cover
    from backend.core import form_guard as fg
    from backend.core.config import get_data_dir
    from backend.core.outcome import detect_confirmation
    from backend.core.run_state import RunState, finalize_job, skip_job

SERVER_NAME = "applyqueue"
SERVER_VERSION = "0.3.0"
SUPPORTED_PROTOCOLS = ("2025-06-18", "2025-03-26", "2024-11-05")

SNAPSHOT_JS = r"""(maxItems) => {
  const SEL = 'a[href],button,input,select,textarea,summary,[role=button],[role=link],[role=checkbox],' +
              '[role=radio],[role=combobox],[role=option],[role=tab],[role=switch],[contenteditable=true]';
  window.__aqRefN = window.__aqRefN || 0;
  const out = [];
  const vis = (el) => {
    const r = el.getBoundingClientRect();
    if (r.width <= 0 && r.height <= 0 && el.type !== 'file') return false;
    const s = getComputedStyle(el);
    return s.visibility !== 'hidden' && s.display !== 'none';
  };
  const labelOf = (el) => {
    const aria = el.getAttribute('aria-label');
    if (aria) return aria;
    const lb = el.getAttribute('aria-labelledby');
    if (lb) { const t = lb.split(/\s+/).map(i => (document.getElementById(i) || {}).innerText || '').join(' ').trim(); if (t) return t; }
    if (el.labels && el.labels.length) return Array.from(el.labels).map(l => l.innerText).join(' ').trim();
    return el.getAttribute('placeholder') || el.getAttribute('title') || el.getAttribute('name') ||
           (el.innerText || el.value || '').trim();
  };
  for (const el of document.querySelectorAll(SEL)) {
    if (out.length >= maxItems) break;
    if (!vis(el)) continue;
    let ref = el.getAttribute('data-aq-ref');
    if (!ref) { ref = String(++window.__aqRefN); el.setAttribute('data-aq-ref', ref); }
    const tag = el.tagName.toLowerCase();
    const type = (el.getAttribute('type') || '').toLowerCase();
    const item = { ref, tag, name: String(labelOf(el) || '').replace(/\s+/g, ' ').slice(0, 120) };
    if (type) item.type = type;
    const role = el.getAttribute('role'); if (role) item.role = role;
    if (el.required || el.getAttribute('aria-required') === 'true') item.required = true;
    if (el.disabled) item.disabled = true;
    if (type === 'checkbox' || type === 'radio') item.checked = !!el.checked;
    else if (type === 'password') item.value = el.value ? '(filled, hidden)' : '';
    else if (tag === 'select') {
      item.value = el.selectedIndex >= 0 ? (el.options[el.selectedIndex] || {}).text || '' : '';
      item.options = Array.from(el.options).slice(0, 40).map(o => o.text.trim());
    } else if (tag === 'input' || tag === 'textarea') item.value = String(el.value || '').slice(0, 120);
    if (tag === 'a') item.href = String(el.getAttribute('href') || '').slice(0, 200);
    out.push(item);
  }
  return out;
}"""

PAGE_TEXT_JS = "() => (document.body ? document.body.innerText : '').slice(0, 20000)"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


class ToolError(Exception):
    """An error reported back to the agent as a tool result (isError=true)."""


class Tool:
    def __init__(self, name: str, description: str, schema: dict, fn: Callable[..., Awaitable[Any]]):
        self.name, self.description, self.schema, self.fn = name, description, schema, fn

    def spec(self) -> dict:
        return {"name": self.name, "description": self.description, "inputSchema": self.schema}

    def validate(self, args: dict) -> None:
        props = self.schema.get("properties") or {}
        missing = [k for k in self.schema.get("required") or [] if args.get(k) in (None, "")]
        if missing:
            raise ToolError(f"{self.name}: missing {', '.join(missing)}")
        for k, v in args.items():
            spec = props.get(k)
            if spec is None:
                raise ToolError(f"{self.name}: unknown argument {k!r}")
            if "enum" in spec and v not in spec["enum"]:
                raise ToolError(f"{self.name}: {k} must be one of {', '.join(map(str, spec['enum']))}")


def _obj(props: dict | None = None, required: list[str] | None = None) -> dict:
    return {"type": "object", "properties": props or {}, "required": required or [], "additionalProperties": False}


S_REF = {"type": "string", "description": "Element ref from browser_snapshot (e.g. '0:12')"}


class Browser:
    """Playwright persistent context on the ApplyQueue browser profile (lazy)."""

    def __init__(self, profile_dir: Path, headless: bool):
        self.profile_dir = profile_dir
        self.headless = headless
        self._pw = None
        self.context = None
        self.page = None
        self.network_errors: list[dict] = []
        self.on_network_error: Callable[[dict], None] | None = None

    async def ensure(self):
        if self.page is not None and not self.page.is_closed():
            return self.page
        if self.context is None:
            from playwright.async_api import async_playwright
            self._pw = await async_playwright().start()
            self.profile_dir.mkdir(parents=True, exist_ok=True)
            self.context = await self._pw.chromium.launch_persistent_context(
                str(self.profile_dir), headless=self.headless,
                chromium_sandbox=(sys.platform != "linux"),
                args=["--disable-blink-features=AutomationControlled"],
            )
            self.context.on("page", self._on_new_page)
            self.context.on("response", self._on_response)
        pages = [p for p in self.context.pages if not p.is_closed()]
        self.page = pages[-1] if pages else await self.context.new_page()
        return self.page

    def _on_new_page(self, page):
        self.page = page  # follow popups / target=_blank like a person would

    def _on_response(self, response):
        try:
            req = response.request
            if req.method in ("POST", "PUT", "PATCH") and response.status >= 400:
                u = urlparse(response.url)
                entry = {"method": req.method, "status": response.status,
                         "url": f"{u.scheme}://{u.netloc}{u.path}"[:300], "at": _now()}
                self.network_errors.append(entry)
                if self.on_network_error:
                    self.on_network_error(entry)
        except Exception:  # pragma: no cover - never break the page over logging
            pass

    def frame(self, index: int):
        frames = self.page.frames
        if index < 0 or index >= len(frames):
            raise ToolError(f"Frame {index} no longer exists - take a new browser_snapshot")
        return frames[index]

    def locator(self, ref: str):
        try:
            fi, n = (ref.split(":", 1) if ":" in ref else ("0", ref))
            fi_i = int(fi)
            int(n)
        except ValueError:
            raise ToolError(f"Bad ref {ref!r}: use a ref from browser_snapshot like '0:12'")
        return self.frame(fi_i).locator(f'[data-aq-ref="{n}"]').first

    async def close(self):
        try:
            if self.context is not None:
                await self.context.close()
        finally:
            if self._pw is not None:
                await self._pw.stop()
            self.context = self.page = self._pw = None


class ApplyQueueTools:
    def __init__(self, state: RunState, data_dir: Path):
        self.state = state
        self.data_dir = data_dir
        opts = state.options
        self.allow_local = bool(opts.get("allow_local_urls"))
        headless = bool(opts.get("headless")) or os.environ.get("APPLYQUEUE_MCP_HEADLESS") == "1"
        self.browser = Browser(Path(opts.get("browser_profile_dir") or data_dir / "browser_profile"), headless)
        self.browser.on_network_error = lambda e: self.state.update_ledger(
            lambda led: led["network_errors"].append(e))
        self.form_frame = 0
        self.tools: dict[str, Tool] = {}
        self._register()

    # ── context helpers ──────────────────────────────────────────────────
    def _settings(self) -> dict:
        return _load_json(self.data_dir / "settings.json", {}) or {}

    def _profile(self) -> dict:
        return _load_json(self.data_dir / "candidate_profile.json", {}) or {}

    def _qa(self) -> dict:
        qa = _load_json(self.data_dir / "qa_repository.json", {})
        return qa if isinstance(qa, dict) else {}

    def _emails(self) -> tuple[str, str]:
        profile_email = (self._profile().get("email") or "").strip()
        account = ((self._settings().get("sensitive_data") or {}).get("email") or "").strip()
        return profile_email or account, account or profile_email

    def _job(self) -> dict:
        url = self.state.current()
        if not url:
            raise ToolError("No current job - call next_job first")
        try:
            from core import jobs_store
        except ImportError:  # pragma: no cover
            from backend.core import jobs_store
        return jobs_store.get_job(url) or {"url": url}

    def _evaluator(self, frame):
        async def evaluate(js_src, *args):
            if not args:
                return await frame.evaluate(js_src)
            return await frame.evaluate(f"(a) => ({js_src})(...a)", list(args))
        return evaluate

    async def _pick_form_frame(self) -> tuple[Any, list]:
        """The frame holding the application form (ATS forms often live in an iframe)."""
        await self.browser.ensure()
        best, best_fields, best_i = None, [], 0
        for i, fr in enumerate(self.browser.page.frames):
            try:
                fields = await fg.scan(self._evaluator(fr))
            except Exception:
                continue
            if len(fields) > len(best_fields) or best is None:
                best, best_fields, best_i = fr, fields, i
        self.form_frame = best_i
        return best, best_fields

    def _remaining(self, fields: list) -> list:
        reported = {e["field_label"].strip().lower() for e in self.state.ledger().get("needs_human", [])}
        return [f for f in fg.unanswered(fields) if f.label.strip().lower() not in reported]

    def _check_url(self, url: str) -> str:
        u = urlparse(url)
        if u.scheme == "file" and self.allow_local:
            return url
        if u.scheme not in ("http", "https") or not u.hostname:
            raise ToolError("Only http(s) URLs can be opened")
        host = u.hostname.lower()
        if not self.allow_local:
            if host in ("localhost",) or host.endswith(".local"):
                raise ToolError("Local addresses are not allowed")
            try:
                ip = ipaddress.ip_address(host)
                if ip.is_private or ip.is_loopback or ip.is_link_local:
                    raise ToolError("Private network addresses are not allowed")
            except ValueError:
                pass
        for d in self._settings().get("blocked_domains") or []:
            d = str(d).lower().strip()
            if d and (host == d or host.endswith("." + d)):
                raise ToolError(f"{host} is on the blocked-domains list - call skip_job")
        return url

    async def _page_evidence(self):
        page = self.browser.page
        texts = []
        for fr in page.frames:
            try:
                texts.append(await fr.evaluate(PAGE_TEXT_JS) or "")
            except Exception:
                continue
        return detect_confirmation("\n".join(texts), page.url or "")

    # ── registry ─────────────────────────────────────────────────────────
    def _add(self, name, description, schema=None):
        def deco(fn):
            self.tools[name] = Tool(name, description, schema or _obj(), fn)
            return fn
        return deco

    def _register(self):  # noqa: C901 - one place lists every tool
        add = self._add

        # Queue
        @add("next_job", "Get the job to apply to now (title, company, URL). Call this first. Returns "
             "{done:true} when there is nothing left in this run.")
        async def next_job():
            job = self.state.next_job()
            if not job:
                return {"done": True, "message": "No more jobs in this run. Stop now."}
            return {"done": False, "job": {k: job.get(k) for k in ("url", "title", "company", "location", "easy_apply", "source") if job.get(k) is not None}}

        @add("skip_job", "Do not apply to the current job (video funnel, recruiter pitch, job closed, blocked site, "
             "not a real application form). Give a short reason.",
             _obj({"reason": {"type": "string"}}, ["reason"]))
        async def skip(reason: str):
            job = self._job()
            return skip_job(self.state, job["url"], reason)

        @add("finish_job", "Finish the current job. ApplyQueue reads the page itself to decide the status: "
             "claiming 'submitted' never counts without a confirmation on the page.",
             _obj({"result": {"type": "string", "enum": ["submitted", "needs_human", "failed"]},
                   "summary": {"type": "string", "description": "One or two sentences on what happened"}},
                  ["result"]))
        async def finish(result: str, summary: str = ""):
            job = self._job()
            if self.browser.page is not None and not self.browser.page.is_closed():
                try:
                    ev = await self._page_evidence()
                    self.state.update_ledger(lambda led: led["confirmations"].append({**ev.to_dict(), "at": _now()}))
                    _, fields = await self._pick_form_frame()
                    remaining = [f.brief() for f in self._remaining(fields)]
                    if not ev.confirmed:
                        self.state.update_ledger(lambda led: led.__setitem__("unanswered_last", remaining))
                except Exception:
                    pass
            if result == "needs_human":
                note = (summary or "The agent asked for a person to finish this application")[:500]
                self.state.update_ledger(lambda led: led["needs_human"] or led["needs_human"].append(
                    {"field_label": "Agent request", "reason": note}))
            reason = None if result == "submitted" else (summary or f"Agent finished with {result}")
            out = finalize_job(self.state, job["url"], agent_claimed_success=(result == "submitted"),
                               agent_summary=summary, failure_reason=reason)
            return {**out, "next": "Call next_job for the next job."}

        @add("get_candidate_context", "The candidate's profile, saved answers and resume text. Use ONLY these facts "
             "to answer questions. Secrets are not included: use fill_secret for emails/passwords.")
        async def ctx():
            profile = {k: v for k, v in self._profile().items() if k not in ("password", "api_key")}
            resume_text = ""
            resume = (self._settings().get("resume_path") or "").strip()
            if resume and Path(resume).is_file():
                try:
                    try:
                        from core.pdf_io import extract_text
                    except ImportError:  # pragma: no cover
                        from backend.core.pdf_io import extract_text
                    resume_text = extract_text(resume)[:12000]
                except Exception:
                    resume_text = ""
            qa = self._qa()
            return {"profile": profile, "saved_answers": dict(list(qa.items())[:200]),
                    "resume_text": resume_text, "resume_available": bool(resume)}

        # Browser
        @add("browser_navigate", "Open a URL in the current tab.", _obj({"url": {"type": "string"}}, ["url"]))
        async def navigate(url: str):
            page = await self.browser.ensure()
            await page.goto(self._check_url(url), wait_until="domcontentloaded", timeout=45000)
            return {"url": page.url, "title": await page.title()}

        @add("browser_snapshot", "List the page's interactive elements with refs (and a text excerpt). Take a new "
             "snapshot after the page changes; refs look like '0:12' (frame:element).",
             _obj({"text_chars": {"type": "integer", "description": "Page text to include (default 3000)"}}))
        async def snapshot(text_chars: int = 3000):
            page = await self.browser.ensure()
            elements = []
            for i, fr in enumerate(page.frames):
                try:
                    items = await fr.evaluate(SNAPSHOT_JS, 250)
                except Exception:
                    continue
                for it in items:
                    it["ref"] = f"{i}:{it['ref']}"
                elements.extend(items)
                if len(elements) >= 400:
                    break
            text = ""
            try:
                text = (await page.evaluate(PAGE_TEXT_JS))[: max(0, min(int(text_chars), 12000))]
            except Exception:
                pass
            return {"url": page.url, "title": await page.title(), "tabs": len(self.browser.context.pages),
                    "elements": elements[:400], "text": text}

        @add("browser_click", "Click an element by ref. Do NOT use this for the final Submit / Next buttons of an "
             "application form: use form_submit.", _obj({"ref": S_REF}, ["ref"]))
        async def click(ref: str):
            await self.browser.ensure()
            loc = self.browser.locator(ref)
            try:
                await loc.click(timeout=10000)
            except Exception:
                await loc.click(timeout=5000, force=True)
            await asyncio.sleep(0.8)
            return {"clicked": ref, "url": self.browser.page.url}

        @add("browser_type", "Type text into an input by ref (clears it first unless clear=false).",
             _obj({"ref": S_REF, "text": {"type": "string"}, "clear": {"type": "boolean"},
                   "press_enter": {"type": "boolean"}}, ["ref", "text"]))
        async def type_(ref: str, text: str, clear: bool = True, press_enter: bool = False):
            await self.browser.ensure()
            loc = self.browser.locator(ref)
            if clear:
                try:
                    await loc.fill(text, timeout=8000)
                except Exception:
                    await loc.click(timeout=5000)
                    await loc.press_sequentially(text, delay=20)
            else:
                await loc.press_sequentially(text, delay=20)
            if press_enter:
                await loc.press("Enter")
            return {"typed": len(text)}

        @add("browser_select", "Choose an option in a <select> (by visible text, or value).",
             _obj({"ref": S_REF, "option": {"type": "string"}}, ["ref", "option"]))
        async def select(ref: str, option: str):
            await self.browser.ensure()
            loc = self.browser.locator(ref)
            try:
                got = await loc.select_option(label=option, timeout=8000)
            except Exception:
                got = await loc.select_option(value=option, timeout=8000)
            return {"selected": got}

        @add("browser_press", "Press a key (e.g. Enter, Tab, Escape, ArrowDown) on the page or an element.",
             _obj({"key": {"type": "string"}, "ref": S_REF}, ["key"]))
        async def press(key: str, ref: str | None = None):
            page = await self.browser.ensure()
            if ref:
                await self.browser.locator(ref).press(key)
            else:
                await page.keyboard.press(key)
            return {"pressed": key}

        @add("browser_upload_resume", "Upload the candidate's resume (or cover letter file) to a file input or "
             "an upload button by ref.",
             _obj({"ref": S_REF, "file": {"type": "string", "enum": ["resume", "cover_letter"]}}, ["ref"]))
        async def upload(ref: str, file: str = "resume"):
            page = await self.browser.ensure()
            settings = self._settings()
            path = (settings.get("cover_letter_path") if file == "cover_letter" else None) or settings.get("resume_path") or ""
            try:
                from resume.tailor import get_tailored_resume_path  # optional
                tailored = get_tailored_resume_path(self.state.current() or "") if file == "resume" else None
                path = tailored or path
            except Exception:
                pass
            if not path or not Path(path).is_file():
                raise ToolError(f"No {file.replace('_', ' ')} file is set in ApplyQueue settings")
            loc = self.browser.locator(ref)
            try:
                await loc.set_input_files(path, timeout=5000)
            except Exception:
                async with page.expect_file_chooser(timeout=10000) as fc:
                    await loc.click()
                chooser = await fc.value
                await chooser.set_files(path)
            return {"uploaded": Path(path).name}

        @add("browser_wait", "Wait for the page (seconds, max 30).", _obj({"seconds": {"type": "number"}}))
        async def wait(seconds: float = 2):
            await asyncio.sleep(max(0.0, min(float(seconds), 30.0)))
            return {"waited": seconds}

        @add("browser_tabs", "List open tabs, or switch to one by index.", _obj({"switch_to": {"type": "integer"}}))
        async def tabs(switch_to: int | None = None):
            await self.browser.ensure()
            pages = [p for p in self.browser.context.pages if not p.is_closed()]
            if switch_to is not None:
                if not 0 <= switch_to < len(pages):
                    raise ToolError("No such tab")
                self.browser.page = pages[switch_to]
                await self.browser.page.bring_to_front()
            return {"tabs": [{"index": i, "url": p.url, "current": p is self.browser.page} for i, p in enumerate(pages)]}

        @add("browser_screenshot", "A screenshot of the current tab (PNG).")
        async def screenshot():
            page = await self.browser.ensure()
            png = await page.screenshot(type="png")
            return {"__image__": base64.b64encode(png).decode("ascii")}

        @add("browser_network_errors", "Form submissions (POST/PUT) the site rejected with an HTTP error.")
        async def net_errors():
            return {"errors": self.state.ledger().get("network_errors", [])[-20:]}

        # Forms
        @add("form_scan", "Every question on the current application form that is still unanswered, INCLUDING "
             "optional ones (category: eeo / essay / cover_letter / resume / generic).")
        async def form_scan():
            _, fields = await self._pick_form_frame()
            rem = self._remaining(fields)
            self.state.update_ledger(lambda led: led.__setitem__("unanswered_last", [f.brief() for f in rem]))
            return {"frame": self.form_frame, "total_fields": len(fields), "unanswered": [f.brief() for f in rem]}

        @add("form_autofill", "Fill every field ApplyQueue can answer from the profile and saved answers (with "
             "retry strategies). Returns what is still left for you to write.")
        async def form_autofill():
            frame, _ = await self._pick_form_frame()
            job = self._job()
            res = await fg.autofill(self._evaluator(frame), self._profile(), self._qa(),
                                    {"company": job.get("company", ""), "title": job.get("title", ""),
                                     "source": job.get("source") or "LinkedIn"})
            return res

        @add("form_fill", "Fill one form field by its key from form_scan (handles selects, radios, checkboxes, "
             "comboboxes with retries).",
             _obj({"key": {"type": "string"}, "value": {"type": "string"}}, ["key", "value"]))
        async def form_fill(key: str, value: str):
            frame, fields = await self._pick_form_frame()
            f = next((x for x in fields if x.key == key), None)
            if f is None:
                raise ToolError(f"No field with key {key!r} - run form_scan again")
            res = await fg.fill_field(self._evaluator(frame), f, value)
            return {"ok": res.ok, "strategy": res.strategy, "attempts": res.attempts}

        @add("form_answer_for", "Look up the saved answer for a question (answer bank / profile), if any.",
             _obj({"question": {"type": "string"}}, ["question"]))
        async def answer_for(question: str):
            return {"answer": fg.answer_bank_lookup(question, self._qa())}

        @add("report_needs_human", "Record a field that is truly impossible to answer truthfully after every "
             "retry strategy. The job is flagged for a person instead of submitting a blank answer.",
             _obj({"field_label": {"type": "string"}, "reason": {"type": "string"}}, ["field_label", "reason"]))
        async def needs_human(field_label: str, reason: str):
            def _m(led):
                if not any(e["field_label"] == field_label for e in led["needs_human"]):
                    led["needs_human"].append({"field_label": field_label[:200], "reason": reason[:500]})
            self.state.update_ledger(_m)
            return {"recorded": field_label}

        @add("form_submit", "Click a form's Next / Continue / Review / Submit button by ref. Refuses while any "
             "question is unanswered. Records whether the page then shows a confirmation.",
             _obj({"ref": S_REF}, ["ref"]))
        async def form_submit(ref: str):
            _, fields = await self._pick_form_frame()
            rem = self._remaining(fields)
            self.state.update_ledger(lambda led: led.__setitem__("unanswered_last", [f.brief() for f in rem]))
            if rem:
                self.state.update_ledger(lambda led: led.__setitem__("blocked_submits", led["blocked_submits"] + 1))
                raise ToolError("Refused to click: unanswered questions " + json.dumps([f.brief() for f in rem])[:3000])
            stamp = _now()
            self.state.update_ledger(lambda led: (led.__setitem__("last_submit_at", stamp),
                                                   led.__setitem__("submit_clicks", led["submit_clicks"] + 1)))
            loc = self.browser.locator(ref)
            try:
                await loc.click(timeout=10000)
            except Exception:
                await loc.click(timeout=5000, force=True)
            try:
                await self.browser.page.wait_for_load_state("domcontentloaded", timeout=10000)
            except Exception:
                pass
            await asyncio.sleep(2)
            ev = await self._page_evidence()
            self.state.update_ledger(lambda led: led["confirmations"].append({**ev.to_dict(), "at": _now()}))
            errs = [e for e in self.state.ledger().get("network_errors", []) if e.get("at", "") >= stamp]
            msg = {"clicked": ref, "confirmation_on_page": ev.confirmed}
            if errs:
                msg["rejected_by_site"] = errs[-3:]
            return msg

        # Secrets
        @add("fill_secret", "Type a secret into an input without ever seeing it. secret: 'email' (application "
             "contact email), 'account_email' (ATS login email), 'site_password_new' (create + save a unique "
             "password for this site; use on password AND confirm boxes), 'site_password' (sign in with the saved one).",
             _obj({"ref": S_REF, "secret": {"type": "string",
                                            "enum": ["email", "account_email", "site_password_new", "site_password"]}},
                  ["ref", "secret"]))
        async def fill_secret(ref: str, secret: str):
            page = await self.browser.ensure()
            contact, account = self._emails()
            if secret in ("email", "account_email"):
                value = contact if secret == "email" else account
                if not value:
                    raise ToolError("No email is set in ApplyQueue settings")
            else:
                try:
                    from core.credential_vault import CredentialVault, normalize_host
                except ImportError:  # pragma: no cover
                    from backend.core.credential_vault import CredentialVault, normalize_host
                vault = CredentialVault(self.data_dir / "vault" / "site_credentials.json")
                try:
                    host = normalize_host(page.url)
                except ValueError:
                    raise ToolError("Could not determine the current site")
                if secret == "site_password_new":
                    entry, _created = vault.get_or_create(host, account)
                    value = entry["password"]
                else:
                    value = vault.password_for_sign_in(host)
                    if not value:
                        raise ToolError(f"No saved password for {host}. Create an account (site_password_new) or "
                                        "use 'Forgot password'.")
            loc = self.browser.locator(ref)
            try:
                await loc.fill(value, timeout=8000)
            except Exception:
                await loc.click(timeout=5000)
                await loc.press_sequentially(value, delay=15)
            return {"filled": secret, "value": "(hidden)"}

        # Email
        @add("email_get_otp", "Read a verification / one-time code from the linked mailbox (Gmail or Outlook API). "
             "Waits up to wait_seconds for the email to arrive.",
             _obj({"sender_hint": {"type": "string", "description": "Company or site name, e.g. 'Greenhouse'"},
                   "since_minutes": {"type": "integer"}, "wait_seconds": {"type": "integer"}}))
        async def get_otp(sender_hint: str = "", since_minutes: int = 15, wait_seconds: int = 90):
            try:
                from core.mail import service as mail
            except ImportError:  # pragma: no cover
                from backend.core.mail import service as mail
            if not mail.has_linked_account():
                raise ToolError("No mailbox is linked in ApplyQueue (Settings > Email). Ask the user for the code "
                                "via report_needs_human('verification code', ...).")
            job = self._job()
            hint = sender_hint or job.get("company") or ""
            used = list(self.state.ledger(job["url"]).get("otp_message_ids") or [])
            got = await asyncio.to_thread(mail.get_otp, hint=hint, since_minutes=int(since_minutes),
                                          wait_seconds=max(0, min(int(wait_seconds), 300)), job_url=job["url"],
                                          exclude_ids=used)
            if not got:
                raise ToolError("No verification code arrived yet. Request a new code and call again.")
            self.state.update_ledger(lambda led: led["otp_message_ids"].append(got["message_id"]))
            out = {"code": got.get("code"), "from": got.get("from_domain"), "subject": got.get("subject", "")[:120]}
            if not got.get("code") and got.get("link"):
                out["verify_link"] = got["link"]  # open it with browser_navigate
            return out

        @add("email_find_confirmation", "Check the linked mailbox for this job's application confirmation email "
             "(subject/sender only).")
        async def find_conf():
            try:
                from core.mail import service as mail
            except ImportError:  # pragma: no cover
                from backend.core.mail import service as mail
            if not mail.has_linked_account():
                return {"linked": False}
            job = self._job()
            hits = await asyncio.to_thread(mail.find_confirmations_for, job)
            return {"linked": True, "matches": hits[:5]}

    # ── dispatch ─────────────────────────────────────────────────────────
    async def call(self, name: str, args: dict) -> dict:
        tool = self.tools.get(name)
        if tool is None:
            return {"content": [{"type": "text", "text": f"Unknown tool {name}"}], "isError": True}
        self.state.update_ledger(lambda led: led.__setitem__("tool_calls", led.get("tool_calls", 0) + 1))
        try:
            tool.validate(args or {})
            out = await tool.fn(**(args or {}))
        except ToolError as e:
            return {"content": [{"type": "text", "text": str(e)}], "isError": True}
        except TypeError as e:
            return {"content": [{"type": "text", "text": f"Bad arguments for {name}: {e}"}], "isError": True}
        except Exception as e:  # browser errors etc. go back to the agent, never crash the server
            try:
                from core.token_guard import scrub
            except ImportError:  # pragma: no cover
                from backend.core.token_guard import scrub
            return {"content": [{"type": "text", "text": scrub(f"{type(e).__name__}: {e}")[:2000]}], "isError": True}
        if isinstance(out, dict) and "__image__" in out:
            return {"content": [{"type": "image", "data": out["__image__"], "mimeType": "image/png"}]}
        return {"content": [{"type": "text", "text": json.dumps(out, ensure_ascii=False, default=str)}]}

    async def aclose(self):
        await self.browser.close()


def tool_names() -> list[str]:
    """Names of every tool (without needing a run file); used for CLI allow-lists and docs."""
    t = ApplyQueueTools.__new__(ApplyQueueTools)
    t.tools = {}
    ApplyQueueTools._register(t)
    return list(t.tools)


class StdioServer:
    def __init__(self, tools: ApplyQueueTools, reader=None, writer=None):
        self.tools = tools
        self.reader = reader or sys.stdin.buffer
        self.writer = writer or sys.stdout.buffer
        self._wlock = threading.Lock()

    def send(self, msg: dict) -> None:
        data = (json.dumps(msg, ensure_ascii=False) + "\n").encode("utf-8")
        with self._wlock:
            self.writer.write(data)
            self.writer.flush()

    async def handle(self, msg: dict) -> dict | None:
        method = msg.get("method")
        mid = msg.get("id")
        params = msg.get("params") or {}
        if mid is None:  # notification
            return None
        if method == "initialize":
            requested = params.get("protocolVersion")
            version = requested if requested in SUPPORTED_PROTOCOLS else SUPPORTED_PROTOCOLS[0]
            result = {"protocolVersion": version, "capabilities": {"tools": {"listChanged": False}},
                      "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
                      "instructions": "ApplyQueue job-application tools. Start with next_job."}
        elif method == "ping":
            result = {}
        elif method == "tools/list":
            result = {"tools": [t.spec() for t in self.tools.tools.values()]}
        elif method == "tools/call":
            result = await self.tools.call(params.get("name", ""), params.get("arguments") or {})
        elif method in ("resources/list", "prompts/list"):
            result = {"resources": []} if method == "resources/list" else {"prompts": []}
        else:
            return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"Method not found: {method}"}}
        return {"jsonrpc": "2.0", "id": mid, "result": result}

    async def serve(self) -> None:
        loop = asyncio.get_running_loop()
        while True:
            line = await loop.run_in_executor(None, self.reader.readline)
            if not line:
                break
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except ValueError:
                self.send({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}})
                continue
            try:
                reply = await self.handle(msg)
            except Exception as e:  # pragma: no cover - last resort
                reply = {"jsonrpc": "2.0", "id": msg.get("id"), "error": {"code": -32603, "message": str(e)[:500]}}
            if reply is not None:
                self.send(reply)


def main(argv: list[str]) -> int:
    """Entry point: ``--mcp-server --run-file PATH [--data-dir DIR]``."""
    run_file = None
    data_dir = None
    it = iter(argv)
    for a in it:
        if a == "--run-file":
            run_file = next(it, None)
        elif a == "--data-dir":
            data_dir = next(it, None)
    run_file = run_file or os.environ.get("APPLYQUEUE_RUN_FILE")
    if data_dir:
        os.environ["APPLYQUEUE_DATA_DIR"] = data_dir
    if not run_file or not Path(run_file).is_file():
        sys.stderr.write("applyqueue mcp: --run-file is required (started by ApplyQueue for one run)\n")
        return 2
    real_stdout = sys.stdout.buffer
    sys.stdout = sys.stderr  # stray prints must never corrupt the protocol stream
    tools = ApplyQueueTools(RunState(run_file), Path(data_dir) if data_dir else get_data_dir())
    server = StdioServer(tools, writer=real_stdout)

    async def _run():
        try:
            await server.serve()
        finally:
            try:
                await asyncio.wait_for(tools.aclose(), timeout=10)
            except Exception:
                pass
    asyncio.run(_run())
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main(sys.argv[1:]))
