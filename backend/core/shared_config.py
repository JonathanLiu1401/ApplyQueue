"""Shared config, utilities, and credential management."""
import asyncio
import json
import os
import re
import subprocess
import sys
import threading
from pathlib import Path

import boto3
from filelock import FileLock
from browser_use.llm import ChatAWSBedrock

try:
    from core.config import get_data_dir
    from memory import MemoryStore
except ImportError:
    from backend.core.config import get_data_dir
    from backend.memory import MemoryStore

_SOURCE_DIR = Path(__file__).resolve().parent.parent.parent  # project root (or temp dir if frozen)

DATA_DIR = get_data_dir()

# When frozen (PyInstaller), BASE_DIR would be a temp dir — use DATA_DIR instead
BASE_DIR = DATA_DIR if getattr(sys, 'frozen', False) else _SOURCE_DIR

# Use OS data dir for settings/profile (written by UI), project root for jobs/logs
JOBS_FILE = DATA_DIR / "jobs.json"
JOBS_LOCK = DATA_DIR / "jobs.json.lock"
QA_FILE = DATA_DIR / "qa_repository.json"
CANDIDATE_PROFILE = DATA_DIR / "candidate_profile.json"
LOGS_DIR = BASE_DIR / "logs"
RESUMES_DIR = BASE_DIR / "resumes"

# Browser profile ALWAYS in OS data dir (must match backend/main.py login endpoint)
BROWSER_PROFILE_DIR = DATA_DIR / "browser_profile"

AWS_PROFILE = "default"
AWS_REGION = "us-west-2"
MODEL_ID = "us.anthropic.claude-sonnet-4-6"
ADA_CMD: tuple[str, ...] = ()  # Only needed for Amazon internal credential refresh

# Load settings from OS data dir (written by desktop app UI) if available
_settings_file = DATA_DIR / "settings.json"
try:
    from core.config import read_json_file as _read_json_file
except ImportError:  # pragma: no cover
    from backend.core.config import read_json_file as _read_json_file
# corrupt / truncated / wrong-type files are quarantined instead of crashing every import
_ui_settings = _read_json_file(_settings_file, {})
# A legacy shared "password" here is moved to the vault at startup (sign-in only).
SENSITIVE_DATA = _ui_settings.get("sensitive_data")
if not isinstance(SENSITIVE_DATA, dict):
    SENSITIVE_DATA = {"email": ""}

# Fall back to profile email if sensitive_data email is blank
if not str(SENSITIVE_DATA.get("email") or "").strip():
    _profile_file = DATA_DIR / "candidate_profile.json"
    if _profile_file.exists():
        _profile_data = _read_json_file(_profile_file, {})
        _profile_email = str(_profile_data.get("email") or "").strip()
        if _profile_email:
            SENSITIVE_DATA["email"] = _profile_email

RESUME_PATH = _ui_settings.get("resume_path", "")
BLOCKED_DOMAINS = _ui_settings.get("blocked_domains", ["meeboss.com"])

_PRIVATE_IP_PREFIXES = ("127.", "10.", "192.168.", "172.16.", "172.17.", "172.18.",
                        "172.19.", "172.20.", "172.21.", "172.22.", "172.23.",
                        "172.24.", "172.25.", "172.26.", "172.27.", "172.28.",
                        "172.29.", "172.30.", "172.31.", "0.", "169.254.")

def _e2e_loopback_ok(host: str, port: int | None) -> bool:
    """Test-only: the e2e suite serves mock ATS sites on 127.0.0.1.

    Needs APPLYQUEUE_E2E_ATS_PORTS (comma list of ports) so a normal install,
    which never sets it, keeps the SSRF block for every loopback address."""
    raw = os.environ.get("APPLYQUEUE_E2E_ATS_PORTS", "").strip()
    if not raw or host != "127.0.0.1" or port is None:
        return False
    return str(port) in {p.strip() for p in raw.split(",") if p.strip()}


def validate_job_url(url: str) -> bool:
    """Reject URLs pointing to private/internal networks (SSRF prevention)."""
    from urllib.parse import urlparse
    try:
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
        if not host or not parsed.scheme.startswith("http"):
            return False
        if _e2e_loopback_ok(host, parsed.port):
            return True
        if host in ("localhost", "0.0.0.0", "[::]", "[::1]"):
            return False
        if any(host.startswith(p) for p in _PRIVATE_IP_PREFIXES):
            return False
        return True
    except Exception:
        return False

# ── Singleton memory store ────────────────────────────────────────────────────
_memory_store: MemoryStore | None = None


def get_memory_store() -> MemoryStore:
    """Get or create the singleton memory store instance."""
    global _memory_store
    if _memory_store is None:
        _memory_store = MemoryStore()
    return _memory_store


def load_json(path: Path, default=None):
    try:
        from core.config import read_json_file
    except ImportError:  # pragma: no cover
        from backend.core.config import read_json_file
    data = read_json_file(Path(path), default)
    return [] if data is None else data


def save_json(path: Path, data):
    try:
        from core.config import atomic_write_text
    except ImportError:  # pragma: no cover
        from backend.core.config import atomic_write_text
    atomic_write_text(path, json.dumps(data, indent=2))


def read_jobs() -> dict:
    """Read jobs.json with cross-process lock."""
    with FileLock(JOBS_LOCK):
        return load_json(JOBS_FILE, {})


def write_jobs(jobs: dict):
    """Write jobs.json with cross-process lock."""
    with FileLock(JOBS_LOCK):
        save_json(JOBS_FILE, jobs)


def update_job(url: str, **fields):
    """Atomically update a single job entry."""
    with FileLock(JOBS_LOCK):
        jobs = load_json(JOBS_FILE, {})
        if url in jobs:
            jobs[url].update(fields)
            save_json(JOBS_FILE, jobs)


_claim_lock = threading.Lock()


def claim_job(url: str) -> bool:
    """Atomically claim a pending job. Returns True if claimed, False if already taken.
    Uses both FileLock (cross-process) and threading lock (in-process workers), plus the sync
    repo's cross-device lock when sync is on (core.jobs_store.claim_job)."""
    try:
        from core import jobs_store
    except ImportError:  # pragma: no cover
        from backend.core import jobs_store
    with _claim_lock:
        return jobs_store.claim_job(url)


def refresh_credentials():
    """Run ada credentials update and return True on success."""
    if not ADA_CMD:
        # No credential refresh command configured — skip silently
        # Users should configure AWS credentials via the Settings UI or aws cli
        return True
    print("🔑 Refreshing AWS credentials...")
    try:
        subprocess.run(ADA_CMD, check=True, timeout=30)
        print("✅ Credentials refreshed")
        return True
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
        print(f"❌ Credential refresh failed: {e}", file=sys.stderr)
        return False


async def credential_refresh_loop(interval_minutes: int = 14):
    """Background task that refreshes credentials on a timer. Cancels with parent."""
    while True:
        await asyncio.sleep(interval_minutes * 60)
        refresh_credentials()


def get_llm() -> ChatAWSBedrock:
    """Create a fresh LLM client with current credentials."""
    session = boto3.Session(profile_name=AWS_PROFILE, region_name=AWS_REGION)
    return ChatAWSBedrock(model=MODEL_ID, session=session)


def normalize_question(q: str) -> str:
    return re.sub(r"[^\w\s]", "", q.lower()).strip()


def build_memory_context(
    profile: dict,
    qa: dict,
    applied_labels: list[str] | None = None,
    job_url: str | None = None,
) -> str:
    """Build the system message context with candidate profile, Q&A bank, and per-website learnings."""
    parts = []

    profile = profile if isinstance(profile, dict) else {}
    # A quick-start profile has only a few fields; nothing below may assume a key exists.
    def _d(v) -> dict:
        return v if isinstance(v, dict) else {}

    def _l(v) -> list:
        return [str(x) for x in v if x not in (None, "")] if isinstance(v, (list, tuple)) else ([str(v)] if v else [])

    def _num(v):
        try:
            return f"{int(float(str(v).replace(',', ''))):,}"
        except (TypeError, ValueError):
            return str(v)

    # Salary formatting (country-aware)
    sal = _d(profile.get('salary_expectation'))
    sal_currency = sal.get('currency', 'USD') or 'USD'
    sal_min = sal.get('min', 0) or 0
    sal_max = sal.get('max', 0) or 0
    sal_period = sal.get('period', 'annual') or 'annual'
    if sal_min:
        salary_str = f"{sal_currency} {_num(sal_min)}" + (f"-{_num(sal_max)}" if sal_max else "") + f" ({sal_period})"
    else:
        salary_str = "Not specified"

    # Country-aware date format
    date_format = profile.get('date_format', '') or 'MM/DD/YYYY'

    addr = _d(profile.get('address'))
    edu = _d(profile.get('education'))
    phone = f"{profile.get('phone_country_code', '') or ''}{profile.get('phone', '') or ''}"
    location = " ".join(x for x in (", ".join(x for x in (addr.get('city'), addr.get('state')) if x),
                                    addr.get('zip') or "", addr.get('country') or "") if x).strip()
    edu_text = " from ".join(x for x in (edu.get('degree'), edu.get('school')) if x)
    if edu_text and edu.get('graduation'):
        edu_text += f" ({edu['graduation']})"
    candidates = [
        ("Name", profile.get('name')),
        ("Email", profile.get('email')),
        ("Phone", phone),
        ("Location", location),
        ("Work Authorization", profile.get('work_authorization')),
        ("Visa Sponsorship Needed", profile.get('visa_sponsorship_needed')),
        ("Willing to Relocate", profile.get('willing_to_relocate')),
        ("Preferred Work Mode", profile.get('preferred_work_mode')),
        ("Years of Experience", profile.get('years_of_experience')),
        ("Education", edu_text),
        ("Current Role", profile.get('current_role')),
        ("Current Company", profile.get('current_company')),
        ("Target Locations", ", ".join(_l(profile.get('target_locations')))),
        ("Languages", ", ".join(_l(profile.get('languages')))),
        ("Skills", ", ".join(_l(profile.get('skills')))),
    ]
    profile_lines = ["CANDIDATE PROFILE:"] + [f"{k}: {v}" for k, v in candidates if v not in (None, "")]
    profile_lines.append(f"Salary: {salary_str}")
    missing = [k for k, v in candidates if v in (None, "")]
    if missing:
        profile_lines.append("Not in the profile (answer only from the resume, never invent): " + ", ".join(missing))
    if profile.get('notice_period'):
        profile_lines.append(f"Notice Period: {profile['notice_period']}")
    if profile.get('nationality'):
        profile_lines.append(f"Nationality: {profile['nationality']}")
    if profile.get('notes'):
        profile_lines.append(f"Notes: {profile['notes']}")

    parts.append("\n".join(profile_lines))

    # Country-specific instructions for the agent
    country_instructions = []
    country_instructions.append(f"DATE FORMAT: When filling date fields, use {date_format} format.")
    if profile.get('notice_period'):
        country_instructions.append(f"NOTICE PERIOD: If asked about notice period or availability, answer: {profile['notice_period']}")
    if profile.get('nationality'):
        country_instructions.append(f"NATIONALITY: If asked about nationality, answer: {profile['nationality']}")
    if profile.get('cover_letter'):
        country_instructions.append(f"COVER LETTER: If a cover letter is requested, use:\n{profile['cover_letter']}")
    if country_instructions:
        parts.append("COUNTRY-SPECIFIC INSTRUCTIONS:\n" + "\n".join(country_instructions))

    if applied_labels:
        parts.append("Already applied — SKIP:\n" + "\n".join(f"- {j}" for j in applied_labels))

    # Try SQLite Q&A first, fall back to passed-in dict
    qa_for_prompt = qa
    try:
        store = get_memory_store()
        if store:
            db_qa = store.qa_get_all_for_prompt()
            if db_qa:
                qa_for_prompt = db_qa
    except Exception:
        pass
    if qa_for_prompt:
        qa_list = "\n".join(f'Q: {q}\nA: {a}' for q, a in qa_for_prompt.items() if a)
        if qa_list:
            parts.append(f"Pre-filled answers for application questions:\n{qa_list}")

    # ── Per-website memory injection ──────────────────────────────────────
    if job_url:
        try:
            store = get_memory_store()
            memories = store.get_domain_memories(job_url, limit=20)
            if memories:
                domain = store.extract_domain(job_url)
                mem_count = len(memories)
                print(f"    🧠 Injecting {mem_count} memories for {domain}")
                parts.append(store.format_for_prompt(memories))
        except Exception as e:  # locked/unreadable memory db: apply without site memories
            print(f"    ⚠️  Site memories unavailable: {e}")

    parts.append(
        "TRACKING INSTRUCTIONS:\n"
        "After each successful application: @@JOB_APPLIED: {\"title\": \"...\", \"company\": \"...\", \"location\": \"...\"}\n"
        "For each form question encountered: @@QUESTION: {\"question\": \"...\", \"answer\": \"...\", \"type\": \"text|dropdown|radio|checkbox\"}\n\n"
        "SELF-LEARNING — report observations about THIS WEBSITE's UI/flow as you navigate:\n"
        "@@LEARNING: {\"domain\": \"<website domain>\", \"category\": \"navigation|form_strategy|element_interaction|failure_recovery|site_structure|qa_pattern\", \"insight\": \"<specific actionable observation>\"}\n"
        "Examples of good learnings:\n"
        "- Navigation: 'Easy Apply opens a modal overlay, don't navigate away from the page'\n"
        "- Element interaction: 'The checkbox is inside a scrollable div, must scroll to find it'\n"
        "- Form strategy: 'This ATS splits the form into 4 steps: Personal → Resume → Questions → Review'\n"
        "Report at least 2-3 learnings per application run."
    )
    return "\n\n".join(parts)


def extract_from_history(result):
    """Extract applied jobs and questions from agent history."""
    jobs, questions, seen = [], {}, set()
    for item in result.history:
        if not item.model_output:
            continue
        memory = item.model_output.memory or ""
        for m in re.finditer(r"@@JOB_APPLIED:\s*(\{[^}]{1,2000}\})", memory):
            try:
                j = json.loads(m.group(1))
                jobs.append(f"{j.get('title','')} at {j.get('company','')} - {j.get('location','')}")
            except json.JSONDecodeError:
                pass
        for m in re.finditer(r"@@QUESTION:\s*(\{[^}]{1,2000}\})", memory):
            try:
                q = json.loads(m.group(1))
                qtext, ans = q.get("question", "").strip(), q.get("answer", "").strip()
                norm = normalize_question(qtext)
                if qtext and norm not in seen:
                    seen.add(norm)
                    questions[qtext] = ans
            except json.JSONDecodeError:
                pass
        # Fallback
        if not jobs and any(kw in memory.lower() for kw in ["application submitted", "successfully applied"]):
            for pat in [r"applied to (.+?) via", r"Application submitted for (.+?) via"]:
                match = re.search(pat, memory, re.IGNORECASE)
                if match:
                    jobs.append(match.group(1).strip())
                    break
    return list(dict.fromkeys(jobs)), questions
