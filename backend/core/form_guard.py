"""Form guard: find unanswered application questions and fill them with retries.

Policy (ApplyQueue M2): every question on an application form is answered,
including optional ones (essays, cover letter, "why us", extra info), truthfully
from the candidate profile / resume / answer bank. EEO questions may use a
"decline to self-identify" option but must still have an option selected. A
field is only left for a person (``needs_human``) after every fill strategy
failed - it is never silently left blank.

All browser access goes through an ``evaluate(js, *args) -> str`` coroutine so
the same code runs against a browser-use actor page in production and a plain
Playwright page in tests. Each JS snippet is an arrow function returning a JSON
string.
"""
from __future__ import annotations

import difflib
import json
import re
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

Evaluate = Callable[..., Awaitable[Any]]

# ── In-page scripts ─────────────────────────────────────────────────────────

SCAN_JS = r"""() => {
  // Same-origin iframes (iCIMS-style hosted forms) and open shadow roots are scanned too.
  const roots = (() => {
    const out = [], seen = new Set();
    const walk = (root) => {
      if (!root || seen.has(root)) return; seen.add(root); out.push(root);
      for (const el of root.querySelectorAll('*')) {
        if (el.shadowRoot) walk(el.shadowRoot);
        if (el.tagName === 'IFRAME' || el.tagName === 'FRAME') {
          let d = null; try { d = el.contentDocument; } catch (e) { d = null; }
          if (d && d.documentElement) walk(d);
        }
      }
    };
    walk(document);
    return out;
  })();
  const all = (sel) => roots.flatMap(r => Array.from(r.querySelectorAll(sel)));
  const byId = (el, id) => { const r = el.getRootNode(); return (r.getElementById ? r.getElementById(id) : null) || document.getElementById(id); };
  const SKIP_TYPES = new Set(['hidden','submit','button','reset','image','search','password']);
  const visible = (el) => {
    if (el.type === 'file') return true;  // file inputs are often visually hidden behind a button
    const r = el.getBoundingClientRect();
    const s = (el.ownerDocument.defaultView || window).getComputedStyle(el);
    if (s.display === 'none' || s.visibility === 'hidden') return false;
    if (r.width > 0 || r.height > 0) return true;
    // Styled radios/checkboxes hide the input; count them if their label is visible.
    if ((el.type === 'radio' || el.type === 'checkbox') && el.labels && el.labels.length) {
      const lr = el.labels[0].getBoundingClientRect();
      return lr.width > 0 || lr.height > 0;
    }
    return false;
  };
  const clean = (t) => (t || '').replace(/\s+/g, ' ').trim();
  // Bot-trap fields: humans never see them, so a value there flags the application as spam.
  const HONEYPOT_LABEL = /(leave (this|it)?\s*(field )?(blank|empty)|do ?n[o']?t (fill|complete|enter)|honey ?pot|bots? only|if you are (a )?human)/i;
  const honeypot = (el, label) => {
    if (el.type === 'file' || el.type === 'radio' || el.type === 'checkbox') return false;
    if (el.closest('[aria-hidden="true"]')) return true;
    if (HONEYPOT_LABEL.test(label || '')) return true;
    const r = el.getBoundingClientRect();
    const w = el.ownerDocument.defaultView || window;
    if (r.right + w.scrollX <= 0 || r.bottom + w.scrollY <= 0) return true;   // parked off-screen
    const s = w.getComputedStyle(el);
    if (parseFloat(s.opacity) === 0 && el.tabIndex < 0) return true;
    return false;
  };
  const ownLabel = (el) => {
    if (el.labels && el.labels.length) return clean(Array.from(el.labels).map(l => l.innerText).join(' '));
    const aria = el.getAttribute('aria-label'); if (aria) return clean(aria);
    const lb = el.getAttribute('aria-labelledby');
    if (lb) { const t = clean(lb.split(/\s+/).map(id => (byId(el, id) || {}).innerText || '').join(' ')); if (t) return t; }
    return '';
  };
  const questionLabel = (el) => {
    const fs = el.closest('fieldset');
    if (fs) { const lg = fs.querySelector('legend'); if (lg && clean(lg.innerText)) return clean(lg.innerText); }
    const grp = el.closest('[role=radiogroup],[role=group]');
    if (grp) { const l = ownLabel(grp); if (l) return l; }
    const wrap = el.closest('.field,.form-group,.question,[class*=question],[class*=field]');
    if (wrap) { const lab = wrap.querySelector('label,.label,[class*=label]'); if (lab && clean(lab.innerText)) return clean(lab.innerText); }
    return '';
  };
  const labelOf = (el) => ownLabel(el) || questionLabel(el) || clean(el.placeholder) || el.name || el.id || '';
  const isRequired = (el, label) => !!(el.required || el.getAttribute('aria-required') === 'true' || /\*\s*$/.test(label));
  const PLACEHOLDER_OPT = /^(select|choose|please|--|—|-)/i;
  const comboHasValue = (el) => {
    const box = el.closest('[class*=select__control],[class*=-control],[class*=combobox],[class*=select-container],[class*=Select]');
    if (!box) return false;
    const v = box.querySelector('[class*=single-value],[class*=singleValue],[class*=multi-value],[class*=multiValue],[class*=selected-value]');
    return !!(v && clean(v.innerText));
  };
  const out = [];
  const groups = new Map();
  // Keys must stay unique across re-scans, so the counter lives on window.
  const nextKey = (p) => p + (window.__aqKeySeq = (window.__aqKeySeq || 0) + 1);
  const nodes = all('input, textarea, select, [contenteditable=true][role=textbox]');
  for (const el of nodes) {
    const type = (el.getAttribute('type') || el.type || el.tagName).toLowerCase();
    if (SKIP_TYPES.has(type)) continue;
    if (el.disabled || el.readOnly || el.getAttribute('aria-hidden') === 'true') continue;
    if (!visible(el)) continue;
    if (type === 'radio' || type === 'checkbox') {
      const gname = el.name || el.getAttribute('data-aq-group') || (el.setAttribute('data-aq-group', nextKey('anon')), el.getAttribute('data-aq-group'));
      if (!groups.has(gname)) groups.set(gname, []);
      groups.get(gname).push(el);
      continue;
    }
    const label = labelOf(el);
    if (honeypot(el, label)) continue;
    const key = el.getAttribute('data-aq-key') || nextKey('f');
    el.setAttribute('data-aq-key', key);
    let kind = el.tagName === 'TEXTAREA' || el.isContentEditable ? 'textarea' : el.tagName === 'SELECT' ? 'select' : type === 'file' ? 'file' : 'text';
    const combo = el.getAttribute('role') === 'combobox' || el.hasAttribute('aria-autocomplete');
    if (combo && kind === 'text') kind = 'combobox';
    let empty, options = [], value = '';
    if (kind === 'select') {
      options = Array.from(el.options).map(o => ({value: o.value, label: clean(o.text)}));
      const o = el.options[el.selectedIndex];
      empty = !o || o.value === '' || PLACEHOLDER_OPT.test(clean(o.text));
      value = o ? clean(o.text) : '';
    } else if (kind === 'file') {
      empty = !(el.files && el.files.length);
    } else if (el.isContentEditable) {
      value = clean(el.innerText); empty = value === '';
    } else {
      value = el.value || ''; empty = value.trim() === '' && !(combo && comboHasValue(el));
    }
    out.push({key, kind, type, name: el.name || '', id: el.id || '', label, required: isRequired(el, label), empty, options, value});
  }
  for (const [gname, els] of groups) {
    const key = 'g:' + gname;
    els.forEach(e => e.setAttribute('data-aq-key', key));
    const first = els[0];
    const type = first.type;
    const kind = type === 'radio' ? 'radio' : (els.length > 1 ? 'checkbox_group' : 'checkbox');
    const label = kind === 'checkbox' ? (ownLabel(first) || questionLabel(first)) : (questionLabel(first) || gname);
    const options = els.map(e => ({value: e.value, label: ownLabel(e) || e.value}));
    const anyChecked = els.some(e => e.checked);
    const required = els.some(e => isRequired(e, label));
    // A lone optional checkbox (e.g. "send me updates") is answered by its state; a
    // lone *required* one (e.g. "I certify...") must be checked.
    const empty = kind === 'checkbox' ? (required && !anyChecked) : !anyChecked;
    out.push({key, kind, type, name: first.name || '', id: first.id || '', label, required, empty, options,
              value: els.filter(e => e.checked).map(e => ownLabel(e) || e.value).join(', ')});
  }
  return JSON.stringify(out);
}"""

# Strategy 1 (direct): native value setter + input/change/blur, option/label match for choices.
# Strategy 2 (search): open a searchable dropdown/combobox, type, click the matching option.
# Strategy 3 (typing): synthetic per-character key events for inputs that ignore direct sets.
FILL_JS = r"""(key, value, strategy) => (async () => {
  // Same-origin iframes (iCIMS-style hosted forms) and open shadow roots are scanned too.
  const roots = (() => {
    const out = [], seen = new Set();
    const walk = (root) => {
      if (!root || seen.has(root)) return; seen.add(root); out.push(root);
      for (const el of root.querySelectorAll('*')) {
        if (el.shadowRoot) walk(el.shadowRoot);
        if (el.tagName === 'IFRAME' || el.tagName === 'FRAME') {
          let d = null; try { d = el.contentDocument; } catch (e) { d = null; }
          if (d && d.documentElement) walk(d);
        }
      }
    };
    walk(document);
    return out;
  })();
  const all = (sel) => roots.flatMap(r => Array.from(r.querySelectorAll(sel)));
  const byId = (el, id) => { const r = el.getRootNode(); return (r.getElementById ? r.getElementById(id) : null) || document.getElementById(id); };
  const els = all('[data-aq-key="' + CSS.escape(key) + '"]');
  if (!els.length) return JSON.stringify({ok: false, detail: 'element not found'});
  const clean = (t) => (t || '').replace(/\s+/g, ' ').trim().toLowerCase();
  const sleep = (ms) => new Promise(r => setTimeout(r, ms));
  const want = Array.isArray(value) ? value.map(clean) : [clean(String(value))];
  const score = (text) => {
    const t = clean(text);
    let best = 0;
    for (const w of want) {
      if (!w) continue;
      if (t === w) best = Math.max(best, 3);
      else if (t.startsWith(w) || w.startsWith(t) && t.length > 1) best = Math.max(best, 2);
      else if (t.includes(w) || (w.includes(t) && t.length > 2)) best = Math.max(best, 1);
    }
    return best;
  };
  const labelText = (e) => (e.labels && e.labels.length ? Array.from(e.labels).map(l => l.innerText).join(' ') : '') || e.getAttribute('aria-label') || e.value;
  const fire = (el, ...types) => types.forEach(t => el.dispatchEvent(new Event(t, {bubbles: true})));
  const setNative = (el, v) => {
    const w = el.ownerDocument.defaultView || window;
    const proto = el.tagName === 'TEXTAREA' ? w.HTMLTextAreaElement.prototype : w.HTMLInputElement.prototype;
    const setter = Object.getOwnPropertyDescriptor(proto, 'value').set;
    setter.call(el, v);
  };
  const el = els[0];
  const type = (el.type || '').toLowerCase();
  if (el.tagName === 'SELECT') {
    let bestI = -1, bestS = 0;
    Array.from(el.options).forEach((o, i) => { const s = Math.max(score(o.text), score(o.value)); if (s > bestS) { bestS = s; bestI = i; } });
    if (bestI < 0) return JSON.stringify({ok: false, detail: 'no matching option'});
    el.selectedIndex = bestI; fire(el, 'input', 'change', 'blur');
    return JSON.stringify({ok: el.selectedIndex === bestI, detail: el.options[bestI].text});
  }
  if (type === 'radio' || type === 'checkbox') {
    if (els.length === 1 && type === 'checkbox') {
      const on = !['false', 'no', '0', ''].includes(want[0]);
      if (el.checked !== on) el.click();
      return JSON.stringify({ok: el.checked === on, detail: String(on)});
    }
    const scored = els.map(e => ({e, s: Math.max(score(labelText(e)), score(e.value))})).filter(x => x.s > 0);
    if (!scored.length) return JSON.stringify({ok: false, detail: 'no matching option'});
    const picks = type === 'radio' ? [scored.sort((a, b) => b.s - a.s)[0].e] : scored.map(x => x.e);
    for (const p of picks) { if (!p.checked) { p.click(); if (!p.checked && p.labels && p.labels[0]) p.labels[0].click(); } }
    return JSON.stringify({ok: picks.every(p => p.checked), detail: picks.map(labelText).join(', ')});
  }
  if (el.isContentEditable) {
    el.focus(); el.innerText = String(value); fire(el, 'input', 'change', 'blur');
    return JSON.stringify({ok: clean(el.innerText) === want[0], detail: 'contenteditable'});
  }
  const text = String(value);
  if (strategy === 'search') {
    el.focus(); el.click();
    setNative(el, text); fire(el, 'input');
    el.dispatchEvent(new KeyboardEvent('keydown', {key: 'ArrowDown', bubbles: true}));
    for (let i = 0; i < 10; i++) {
      await sleep(150);
      const opts = all('[role=option],[class*=option]').filter(o => o.offsetParent !== null);
      const ranked = opts.map(o => ({o, s: score(o.innerText)})).filter(x => x.s > 0).sort((a, b) => b.s - a.s);
      if (ranked.length) { ranked[0].o.click(); await sleep(100); return JSON.stringify({ok: true, detail: clean(ranked[0].o.innerText)}); }
    }
    // No option matched the full answer ("Springfield, Illinois" vs "Springfield, IL"): search by its
    // first part and take an option that starts with it.
    const head = text.split(/[,(\/-]/)[0].trim();
    if (head && head.length >= 3 && head !== text) {
      setNative(el, head); fire(el, 'input');
      el.dispatchEvent(new KeyboardEvent('keydown', {key: 'ArrowDown', bubbles: true}));
      for (let i = 0; i < 6; i++) {
        await sleep(150);
        const opts = all('[role=option],[class*=option]').filter(o => o.offsetParent !== null);
        const hit = opts.find(o => clean(o.innerText).startsWith(clean(head)));
        if (hit) { hit.click(); await sleep(100); return JSON.stringify({ok: true, detail: clean(hit.innerText)}); }
      }
      setNative(el, text); fire(el, 'input');
    }
    el.dispatchEvent(new KeyboardEvent('keydown', {key: 'Enter', bubbles: true}));
    return JSON.stringify({ok: el.value.trim() !== '', detail: 'enter'});
  }
  if (strategy === 'typing') {
    el.focus(); setNative(el, ''); fire(el, 'input');
    for (const ch of text) {
      el.dispatchEvent(new KeyboardEvent('keydown', {key: ch, bubbles: true}));
      el.dispatchEvent(new KeyboardEvent('keypress', {key: ch, bubbles: true}));
      setNative(el, el.value + ch);
      el.dispatchEvent(new InputEvent('input', {bubbles: true, data: ch, inputType: 'insertText'}));
      el.dispatchEvent(new KeyboardEvent('keyup', {key: ch, bubbles: true}));
    }
    fire(el, 'change', 'blur');
    return JSON.stringify({ok: el.value === text, detail: 'typed'});
  }
  el.focus(); setNative(el, text); fire(el, 'input', 'change', 'blur');
  return JSON.stringify({ok: el.value === text, detail: 'direct'});
})()"""

STRATEGIES: tuple[str, ...] = ("direct", "search", "typing")

# ── Field model ─────────────────────────────────────────────────────────────


@dataclass
class Field:
    key: str
    kind: str
    label: str
    required: bool = False
    empty: bool = True
    options: list[dict] = field(default_factory=list)
    type: str = ""
    name: str = ""
    id: str = ""
    value: str = ""

    @property
    def category(self) -> str:
        return classify(self)

    def brief(self) -> dict:
        d = {"key": self.key, "label": self.label[:160], "kind": self.kind, "required": self.required,
             "category": self.category}
        if self.options:
            d["options"] = [o.get("label") or o.get("value") for o in self.options][:25]
        return d


def _as_text(raw: Any) -> str:
    if isinstance(raw, (bytes, bytearray)):
        raw = raw.decode("utf-8", "replace")
    return raw if isinstance(raw, str) else json.dumps(raw)


def parse_fields(raw: Any) -> list[Field]:
    data = json.loads(_as_text(raw)) if not isinstance(raw, list) else raw
    out = []
    for d in data:
        out.append(Field(
            key=d["key"], kind=d.get("kind", "text"), label=d.get("label", "") or "",
            required=bool(d.get("required")), empty=bool(d.get("empty")), options=d.get("options") or [],
            type=d.get("type", ""), name=d.get("name", ""), id=d.get("id", ""), value=d.get("value", "") or "",
        ))
    return out


async def scan(evaluate: Evaluate) -> list[Field]:
    return parse_fields(await evaluate(SCAN_JS))


def unanswered(fields: list[Field], *, include_optional: bool = True) -> list[Field]:
    """Fields still empty. Optional questions count too (answer-every-question rule).

    Exception: an optional file input that is not a resume/CV/cover-letter slot
    (e.g. "portfolio attachment") cannot be answered truthfully without a file
    and is not reported.
    """
    out = []
    for f in fields:
        if not f.empty:
            continue
        if not include_optional and not f.required:
            continue
        if f.kind == "file" and not f.required and classify(f) not in ("resume", "cover_letter"):
            continue
        out.append(f)
    return out


# ── Classification & answer planning ────────────────────────────────────────

_EEO_RE = re.compile(
    r"\b(gender|sex\b|race|racial|ethnic|ethnicity|hispanic|latino|veteran|disabilit|sexual orientation|"
    r"transgender|pronoun|lgbt|self[- ]identif|voluntary self|equal employment|eeo)\w*", re.I)
_COVER_RE = re.compile(r"cover\s*letter|motivation(al)? letter", re.I)
_RESUME_RE = re.compile(r"\b(resume|résumé|cv|curriculum vitae)\b", re.I)
_ESSAY_RE = re.compile(
    r"\bwhy\b|tell us|describe|explain|what (makes|interests|excites|would)|additional (info|information|comments)|"
    r"anything else|extra info|about yourself|motivat|summary|message to|share (with us|more)|"
    r"how (would|do|did) you|interest(ed)? in", re.I)
_ACCURACY_ATTEST_RE = re.compile(
    r"\b(certify|attest|confirm|declare|affirm)\b.*\b(true|accurate|correct|complete)\b", re.I)
DECLINE_RE = re.compile(
    r"decline|prefer not|don.?t wish|do not wish|not (wish )?to (answer|disclose|say|self[- ]identify)|"
    r"choose not|rather not|i don.?t want to answer|not specified|undisclosed", re.I)


def classify(f: Field) -> str:
    label = f.label or f.name
    if f.kind == "file":
        if _COVER_RE.search(label):
            return "cover_letter"
        return "resume" if _RESUME_RE.search(label) or f.required else "file"
    if _EEO_RE.search(label):
        return "eeo"
    if _COVER_RE.search(label):
        return "cover_letter"
    if f.kind == "textarea" or _ESSAY_RE.search(label):
        return "essay"
    return "generic"


def normalize(q: str) -> str:
    q = re.sub(r"[\*\(\)\[\]:?.!,\"'’]", " ", (q or "").lower())
    q = re.sub(r"\b(optional|required|please|kindly)\b", " ", q)
    return re.sub(r"\s+", " ", q).strip()


def answer_bank_lookup(label: str, qa: dict[str, str], cutoff: float = 0.82) -> str | None:
    """Fuzzy-match a question label against the saved answer bank."""
    if not qa or not label:
        return None
    norm = normalize(label)
    bank = {normalize(k): v for k, v in qa.items() if v not in (None, "")}
    if norm in bank:
        return str(bank[norm])
    match = difflib.get_close_matches(norm, list(bank), n=1, cutoff=cutoff)
    if match:
        return str(bank[match[0]])
    # Whole-word containment either way for long-ish questions ("Why do you want to work at X?" vs
    # "why do you want to work here"). Words, not characters: "location" is not in "relocation".
    nw = norm.split()
    for k, v in bank.items():
        kw = k.split()
        short, long_ = (kw, nw) if len(kw) <= len(nw) else (nw, kw)
        if len(short) >= 3 and _contains_words(long_, short):
            return str(v)
    return None


def _contains_words(hay: list[str], needle: list[str]) -> bool:
    n = len(needle)
    return any(hay[i:i + n] == needle for i in range(len(hay) - n + 1))


def _yes_no(v: Any) -> str | None:
    if isinstance(v, bool):
        return "Yes" if v else "No"
    if isinstance(v, str) and v.strip().lower() in ("yes", "no", "true", "false"):
        return "Yes" if v.strip().lower() in ("yes", "true") else "No"
    return None


def _d(v: Any) -> dict:
    return v if isinstance(v, dict) else {}


def _s(v: Any) -> str:
    """Profile scalars as text. A hand-edited or imported profile can carry numbers, lists or
    nulls where strings belong; those must not crash a run halfway through a form."""
    if v is None or isinstance(v, (dict, list, tuple, set)):
        return ""
    return str(v).strip()


def _profile_answer(label: str, profile: dict, context: dict) -> str | None:
    p = _d(profile)
    name = " ".join(_s(p.get("name")).split())
    first, _, last = name.partition(" ")
    addr = _d(p.get("address"))
    sal = _d(p.get("salary_expectation"))
    context = _d(context)
    l = normalize(label)
    rules: list[tuple[str, Any]] = [
        (r"\bfirst name\b|\bgiven name\b", first),
        (r"\blast name\b|\bsurname\b|\bfamily name\b", last.strip()),
        (r"\b(full |legal )?name\b", name),
        (r"\be-?mail\b", p.get("email")),
        (r"\bphone\b|\bmobile\b|\btelephone\b", (f"{_s(p.get('phone_country_code'))}{_s(p.get('phone'))}" if _s(p.get("phone")) else "")),
        (r"\blinkedin\b", p.get("linkedin") or p.get("linkedin_url")),
        (r"\bgithub\b", p.get("github")),
        (r"\b(website|portfolio|personal site)\b", p.get("website") or p.get("portfolio") or p.get("github")),
        (r"\bsponsor", _yes_no(p.get("visa_sponsorship_needed"))),
        (r"\b(authori[sz]ed|eligible|legally (able|permitted)) to work\b|\bwork authori[sz]ation\b",
         _yes_no(p.get("work_authorization")) or p.get("work_authorization")),
        (r"\brelocat", _yes_no(p.get("willing_to_relocate"))),
        (r"\bcity\b", addr.get("city")),
        (r"\b(state|province|region)\b", addr.get("state")),
        (r"\b(zip|postal)\b", addr.get("zip")),
        (r"\bcountry\b", addr.get("country")),
        (r"\b(location|where are you based|current address)\b",
         ", ".join(x for x in (addr.get("city"), addr.get("state")) if x)),
        (r"\b(current|most recent) (title|role|position)\b", p.get("current_role")),
        (r"\b(current|most recent) (company|employer)\b", p.get("current_company")),
        (r"\byears of (professional )?experience\b|\bhow many years\b", p.get("years_of_experience")),
        (r"\b(notice period|start date|when can you start|availability)\b", p.get("notice_period")),
        (r"\b(salary|compensation|pay) (expectation|requirement|range)s?\b|\bdesired (salary|pay)\b",
         (f"{sal.get('min')}" if sal.get("min") else None)),
        (r"\bhow did you (hear|find|learn)\b|\bsource\b|\breferr(ed|al) source\b", context.get("source")),
        (r"\b(school|university|college)\b", _d(p.get("education")).get("school")),
        (r"\bdegree\b", _d(p.get("education")).get("degree")),
        (r"\bnationality\b|\bcitizenship\b", p.get("nationality")),
        (r"\bpronouns?\b", p.get("pronouns")),
    ]
    for pattern, value in rules:
        if re.search(pattern, l) and _s(value):
            return _s(value)
    return None


def pick_decline_option(options: list[dict]) -> str | None:
    for o in options:
        text = o.get("label") or o.get("value") or ""
        if DECLINE_RE.search(text):
            return text
    return None


def best_option(options: list[dict], answer: str) -> str | None:
    """Map a free-text answer onto one of a field's options (or None)."""
    if not options:
        return answer
    labels = [(o.get("label") or o.get("value") or "") for o in options]
    real = [t for t in labels if t and not re.match(r"^(select|choose|please|--|—)", t, re.I)]
    a = normalize(answer)
    if not a:
        return None          # "?" or "" normalizes to nothing, and "" is a prefix of every option
    for t in real:
        if normalize(t) == a:
            return t
    for t in real:
        # whole-word prefixes only: "No" is not "None of the above" or "Norway"
        nt = normalize(t)
        if nt and (nt.startswith(a + " ") or a.startswith(nt + " ")):
            return t
    m = difflib.get_close_matches(a, [normalize(t) for t in real], n=1, cutoff=0.6)
    if m:
        for t in real:
            if normalize(t) == m[0]:
                return t
    return None


def plan_answer(f: Field, profile: dict, qa: dict, context: dict | None = None) -> str | None:
    """Truthful answer for a field from profile / answer bank, or None.

    None means the agent must compose the answer itself from the resume (essays,
    why-us, extra info) - or, if it truly cannot, report the field as needs-human.
    """
    context = context or {}
    cat = classify(f)
    if cat in ("resume", "file"):
        return None  # uploads are done by the agent's upload action
    answer: str | None = None
    if cat == "eeo":
        eeo = _d(_d(profile).get("eeo"))
        for k, v in eeo.items():
            if v and k.lower() in normalize(f.label):
                answer = str(v)
                break
        if answer is None:
            answer = answer_bank_lookup(f.label, qa)
        if answer and f.options:
            answer = best_option(f.options, answer)
        if not answer:
            answer = pick_decline_option(f.options) if f.options else "Prefer not to say"
        return answer
    if cat == "cover_letter":
        cl = _s(_d(profile).get("cover_letter"))
        if cl:
            return cl
    answer = answer_bank_lookup(f.label, qa)
    if answer is None:
        answer = _profile_answer(f.label, profile, context)
        # A keyword rule ("relocat" -> willing_to_relocate) gives a bare Yes/No; that is no answer
        # to "Tell us about your relocation timeline" - the agent writes those from the resume.
        if answer in ("Yes", "No") and cat == "essay" and f.kind in ("textarea", "text"):
            answer = None
    if answer is None and f.kind == "checkbox" and _ACCURACY_ATTEST_RE.search(f.label):
        # Every answer comes from the candidate's own profile/resume, so the
        # "information above is true/accurate" attestation is truthful. Terms,
        # privacy and marketing consents are NOT auto-ticked (left to the agent).
        answer = "Yes"
    if answer is None:
        return None
    if f.kind in ("select", "radio", "checkbox_group", "combobox") and f.options:
        return best_option(f.options, answer)
    if f.kind == "checkbox":
        yn = _yes_no(answer)
        return "true" if yn == "Yes" else ("false" if yn == "No" else None)
    return answer


# ── Filling with retries ────────────────────────────────────────────────────


@dataclass
class FillResult:
    key: str
    label: str
    ok: bool
    strategy: str = ""
    attempts: list[dict] = field(default_factory=list)


async def fill_field(evaluate: Evaluate, f: Field, value: Any) -> FillResult:
    """Try each strategy in turn until a re-scan shows the field answered."""
    attempts: list[dict] = []
    order = list(STRATEGIES)
    if f.kind == "combobox":
        order = ["search", "direct", "typing"]
    elif f.kind in ("select", "radio", "checkbox", "checkbox_group"):
        order = ["direct"]  # choice controls only have the option-matching path
    for strategy in order:
        try:
            raw = await evaluate(FILL_JS, f.key, value, strategy)
            res = json.loads(_as_text(raw))
        except Exception as e:  # noqa: BLE001 - recorded, next strategy
            res = {"ok": False, "detail": f"error: {e}"[:200]}
        attempts.append({"strategy": strategy, **res})
        if res.get("ok"):
            # Confirm through an independent re-scan (the page may have reset the value).
            after = {x.key: x for x in await scan(evaluate)}
            if f.key in after and not after[f.key].empty:
                return FillResult(f.key, f.label, True, strategy, attempts)
            attempts[-1]["ok"] = False
            attempts[-1]["detail"] = "value did not stick"
    return FillResult(f.key, f.label, False, "", attempts)


async def autofill(evaluate: Evaluate, profile: dict, qa: dict, context: dict | None = None,
                   compose: Callable[[Field], Awaitable[str | None]] | None = None) -> dict:
    """Fill every empty field we can answer. Returns filled / failed / remaining lists.

    ``compose`` (optional) produces answers for fields the profile/bank cannot
    (essays) - used by tests; in production the agent writes those itself.
    """
    fields = await scan(evaluate)
    filled, failed, remaining = [], [], []
    for f in unanswered(fields):
        value = plan_answer(f, profile, qa, context)
        if value is None and compose is not None:
            value = await compose(f)
        if value is None:
            remaining.append(f.brief())
            continue
        res = await fill_field(evaluate, f, value)
        (filled if res.ok else failed).append({**f.brief(), "strategy": res.strategy, "attempts": res.attempts})
    return {"filled": filled, "failed": failed, "remaining": remaining}
