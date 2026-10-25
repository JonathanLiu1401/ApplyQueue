"""
Configuration management for ApplyQueue.
All settings stored in OS-appropriate app data directory.
No hardcoded values.
"""
import json
import logging
import platform
import shutil
from pathlib import Path

APP_DIR_NAME = "applyqueue"
LEGACY_APP_DIR_NAME = "langhire"  # upstream LangHire; migrated on first run
_MIGRATION_MARKER = ".migrated_from_langhire"

_log = logging.getLogger(__name__)


def _os_data_root() -> Path:
    system = platform.system()
    if system == "Darwin":
        return Path.home() / "Library" / "Application Support"
    if system == "Windows":
        return Path.home() / "AppData" / "Roaming"
    return Path.home() / ".config"  # Linux and other POSIX


def migrate_legacy_data_dir(root: Path) -> bool:
    """Move LangHire's data folder contents into the ApplyQueue folder once.

    Idempotent and non-destructive: entries that already exist in the new folder
    are left alone (and stay in the legacy folder), the legacy folder is removed
    only when it ends up empty, and a marker file records the migration. The
    session token is never carried over (it is regenerated on each start), and
    telemetry is reset to off because ApplyQueue telemetry is opt-in.
    Returns True when anything was moved.
    """
    legacy = root / LEGACY_APP_DIR_NAME
    new = root / APP_DIR_NAME
    if not legacy.is_dir() or (new / _MIGRATION_MARKER).exists():
        return False
    new.mkdir(parents=True, exist_ok=True)
    moved = False
    for entry in sorted(legacy.iterdir()):
        if entry.name in (".api_token", ".api_token.tmp"):
            continue
        target = new / entry.name
        if target.exists():
            continue
        try:
            shutil.move(str(entry), str(target))
            moved = True
        except OSError as e:  # e.g. a file locked on Windows; keep going
            _log.warning("Could not migrate %s: %s", entry.name, e)
    settings_file = new / "settings.json"
    if moved and settings_file.exists():
        try:
            data = json.loads(settings_file.read_text(encoding="utf-8"))
            data["telemetry_enabled"] = False
            data.pop("data_dir", None)
            settings_file.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except (OSError, ValueError) as e:
            _log.warning("Could not reset telemetry in migrated settings: %s", e)
    (new / _MIGRATION_MARKER).write_text(str(legacy), encoding="utf-8")
    try:
        leftovers = [p for p in legacy.iterdir() if p.name not in (".api_token", ".api_token.tmp")]
        if not leftovers:
            shutil.rmtree(legacy, ignore_errors=True)
    except OSError:
        pass
    if moved:
        _log.info("Migrated LangHire data from %s to %s", legacy, new)
    return moved


_migration_checked = False


def get_data_dir() -> Path:
    """Get OS-appropriate data directory for the app (migrating LangHire's once)."""
    global _migration_checked
    root = _os_data_root()
    if not _migration_checked:
        _migration_checked = True
        try:
            migrate_legacy_data_dir(root)
        except OSError as e:
            _log.warning("LangHire data migration failed: %s", e)
    base = root / APP_DIR_NAME
    base.mkdir(parents=True, exist_ok=True)
    return base


def _load_json(path: Path, default=None):
    if path.exists():
        return json.loads(path.read_text())
    return default if default is not None else {}


def _save_json(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2))
    tmp.replace(path)
    if any(s in path.name for s in ("llm_settings", "settings")):
        try:
            import os, stat
            os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
        except OSError:
            import logging
            logging.getLogger("config").warning(
                f"Could not set restrictive permissions on {path.name}"
            )


# ── Profile ───────────────────────────────────────────────────────────────

def load_profile() -> dict:
    return _load_json(get_data_dir() / "candidate_profile.json", {
        "name": "",
        "email": "",
        "phone": "",
        "phone_country_code": "+1",
        "country": "US",
        "address": {"street": "", "city": "", "state": "", "zip": "", "country": "USA"},
        "work_authorization": "",
        "visa_sponsorship_needed": False,
        "willing_to_relocate": False,
        "preferred_work_mode": "hybrid",
        "years_of_experience": 0,
        "education": {"degree": "", "school": "", "graduation": ""},
        "current_role": "",
        "target_job_titles": [],
        "target_locations": [],
        "languages": ["English"],
        "skills": [],
        "salary_expectation": {"min": 50000, "max": 100000, "currency": "USD", "period": "annual"},
        "notice_period": "",
        "nationality": "",
        "date_of_birth": "",
        "cover_letter": "",
        "date_format": "MM/DD/YYYY",
        "notes": "",
    })


def save_profile(profile: dict):
    _save_json(get_data_dir() / "candidate_profile.json", profile)


# ── LLM Settings ──────────────────────────────────────────────────────────

def load_llm_settings() -> dict:
    return _load_json(get_data_dir() / "llm_settings.json", {
        "provider": "openrouter",
        "openai": {"api_key": "", "model": "gpt-4o"},
        "anthropic": {"api_key": "", "model": "claude-sonnet-4-5"},
        "bedrock": {"access_key": "", "secret_key": "", "region": "us-west-2", "model": "us.anthropic.claude-sonnet-4-6"},
        "gemini": {"api_key": "", "model": "gemini-2.5-pro"},
        "ollama": {"base_url": "http://localhost:11434", "model": ""},
        "openrouter": {"api_key": "", "model": "qwen/qwen3.6-plus"},
    })


def save_llm_settings(settings: dict):
    _save_json(get_data_dir() / "llm_settings.json", settings)


# ── App Settings ──────────────────────────────────────────────────────────

def load_settings() -> dict:
    return _load_json(get_data_dir() / "settings.json", {
        "resume_path": "",
        "blocked_domains": [],
        "sensitive_data": {"email": ""},
        "max_failures": 8,
        "stagger_delay": 5,
        "data_dir": str(get_data_dir()),
        "telemetry_enabled": False,  # opt-in only
        "theme": "system",
    })


def _vault():
    try:
        from core.credential_vault import CredentialVault
    except ImportError:  # pragma: no cover
        from backend.core.credential_vault import CredentialVault
    return CredentialVault(get_data_dir() / "vault" / "site_credentials.json")


def move_shared_password_to_vault(settings: dict) -> bool:
    """LangHire kept one shared ATS password in settings.json. Move it to the vault
    (sign-in to existing accounts only) and drop it from settings. Returns True if changed."""
    try:
        from core.credential_vault import migrate_settings_password
    except ImportError:  # pragma: no cover
        from backend.core.credential_vault import migrate_settings_password
    return migrate_settings_password(settings, _vault())


def migrate_settings_secrets() -> bool:
    """Startup migration: strip a legacy shared password out of settings.json."""
    path = get_data_dir() / "settings.json"
    if not path.exists():
        return False
    settings = _load_json(path, {})
    if move_shared_password_to_vault(settings):
        _save_json(path, settings)
        return True
    return False


def save_settings(settings: dict):
    move_shared_password_to_vault(settings)
    if "max_failures" in settings:
        settings["max_failures"] = max(1, min(int(settings["max_failures"]), 50))
    if "stagger_delay" in settings:
        settings["stagger_delay"] = max(0, min(int(settings["stagger_delay"]), 300))
    if "blocked_domains" in settings:
        settings["blocked_domains"] = [
            str(d).strip() for d in settings["blocked_domains"]
            if isinstance(d, str) and d.strip()
        ]
    _save_json(get_data_dir() / "settings.json", settings)

