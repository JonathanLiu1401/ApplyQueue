"""
Configuration management for ApplyQueue.
All settings stored in OS-appropriate app data directory.
No hardcoded values.
"""
import json
import logging
import os
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
    override = os.environ.get("APPLYQUEUE_DATA_DIR", "").strip()
    if override:  # set for the MCP server child process (and tests)
        base = Path(override).expanduser()
        base.mkdir(parents=True, exist_ok=True)
        return base
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


def data_dir_problem() -> str | None:
    """Why the data dir cannot be used (None if it can): missing parent, read-only, full."""
    try:
        d = get_data_dir()
    except OSError as e:
        return f"data directory cannot be created ({e}). Set APPLYQUEUE_DATA_DIR to a writable folder."
    probe = d / f".write-probe-{os.getpid()}"
    try:
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except OSError as e:
        return (f"data directory {d} is not writable ({e.strerror or e}). Free disk space or fix its "
                "permissions, or set APPLYQUEUE_DATA_DIR to a writable folder.")
    return None


def quarantine_corrupt(path: Path, err: Exception) -> None:
    """Move an unreadable JSON file aside (never delete it) so the app can start with defaults."""
    import time as _t
    dest = path.with_name(f"{path.name}.corrupt-{_t.strftime('%Y%m%d-%H%M%S')}")
    try:
        path.replace(dest)
        _log.error("%s was unreadable (%s); moved it to %s and continued with defaults", path.name, err, dest.name)
    except OSError as move_err:  # read-only dir etc.: leave it, still use defaults
        _log.error("%s was unreadable (%s) and could not be moved aside: %s", path.name, err, move_err)


def read_json_file(path: Path, default):
    """Read JSON as UTF-8 (BOM tolerated). A corrupt or truncated file is quarantined and
    ``default`` returned, instead of crashing startup or being silently overwritten."""
    try:
        raw = path.read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        return default
    except UnicodeDecodeError as e:
        quarantine_corrupt(path, e)
        return default
    try:
        data = json.loads(raw)
    except ValueError as e:
        quarantine_corrupt(path, e)
        return default
    if default is not None and not isinstance(data, type(default)):
        quarantine_corrupt(path, TypeError(f"expected {type(default).__name__}, got {type(data).__name__}"))
        return default
    return data


def _load_json(path: Path, default=None):
    data = read_json_file(path, default)
    return {} if data is None else data


def atomic_write_text(path: Path, text: str) -> None:
    """tmp + replace. On failure (disk full, read-only) the previous file is untouched and the
    partial tmp file is removed, then the error propagates."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    try:
        tmp.write_text(text, encoding="utf-8")
        tmp.replace(path)
    except BaseException:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise


def _save_json(path: Path, data):
    atomic_write_text(path, json.dumps(data, indent=2))
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

def _profile_defaults() -> dict:
    return {
        "name": "",
        "email": "",
        "phone": "",
        # Preset answers start "not answered" (see profile_presets): None / "" / [] until the user
        # gives one, so an untouched field is never mistaken for a choice.
        "phone_country_code": "",
        "country": "",
        "address": {"street": "", "city": "", "state": "", "zip": "", "country": ""},
        "work_authorization": "",
        "visa_sponsorship_needed": None,
        "willing_to_relocate": None,
        "preferred_work_mode": "",
        "years_of_experience": None,
        "education": {"degree": "", "school": "", "graduation": ""},
        "current_role": "",
        "target_job_titles": [],
        "target_locations": [],
        "languages": [],
        "skills": [],
        "salary_expectation": {"min": None, "max": None, "currency": "USD", "period": "annual"},
        "notice_period": "",
        "nationality": "",
        "date_of_birth": "",
        "cover_letter": "",
        "date_format": "MM/DD/YYYY",
        "notes": "",
        "_schema": _presets().SCHEMA,
    }


def _presets():
    try:
        from core import profile_presets
    except ImportError:  # pragma: no cover
        from backend.core import profile_presets
    return profile_presets


def _lenient_like(template, value, bool_text: bool = True):
    """Repair the shapes that crash callers (null, a list where text belongs, text where a list or
    object belongs) without rewriting values that are merely unusual (a bool work authorization,
    "6" years as text)."""
    if isinstance(template, dict):
        if not isinstance(value, dict):
            return dict(template)
        return {k: (_lenient_like(template[k], v, bool_text) if k in template else v) for k, v in value.items()}
    if isinstance(template, list):
        if value is None:
            return []
        if isinstance(value, str):
            return [x.strip() for x in value.split(",") if x.strip()]
        if isinstance(value, (list, tuple)):
            return [x for x in value if isinstance(x, str) and x.strip()]
        return list(template)
    if isinstance(template, str):
        if value is None:
            return ""
        if isinstance(value, (list, tuple)):
            return " ".join(str(x).strip() for x in value if isinstance(x, (str, int, float)) and str(x).strip())
        if isinstance(value, dict):
            return template
        if isinstance(value, bool):
            return value if bool_text else template
        if isinstance(value, (int, float)):
            return str(value)
        return value
    if template is None:  # a "not answered" field: null stays null, a wrong shape becomes null
        return None if isinstance(value, (dict, list, tuple)) else value
    if value is None or isinstance(value, (dict, list, tuple)):
        return template
    return value


def normalize_profile(profile) -> dict:
    """A profile whose known fields have the types the app relies on (see ``_lenient_like``)."""
    if not isinstance(profile, dict):
        return _profile_defaults()
    d = _profile_defaults()
    # work_authorization may be a yes/no bool (form_guard reads it either way); other text fields may not
    return {k: (_lenient_like(d[k], v, bool_text=(k == "work_authorization")) if k in d else v)
            for k, v in profile.items() if isinstance(k, str)}


def load_profile() -> dict:
    """The profile, upgraded to the current schema. An old file (preset answers stored as values)
    is migrated once and written back; see ``profile_presets.migrate`` for the rules."""
    path = get_data_dir() / "candidate_profile.json"
    raw = _load_json(path, {})  # a dict default: a non-object file is quarantined, not used
    if not isinstance(raw, dict) or not raw:  # no profile yet (or an empty / unreadable file)
        return normalize_profile(_profile_defaults())
    migrated, changed = _presets().migrate(raw)
    profile = normalize_profile(migrated)
    if changed and path.exists():
        try:
            _save_json(path, profile)
        except OSError:  # read-only data dir: still return the migrated view
            _log.warning("Could not write the migrated profile")
    return profile


def save_profile(profile: dict):
    """Save what the user entered. It is already in the current schema (the app only ever shows
    and sends schema-2 fields), so nothing is migrated here; stale confirm flags are dropped."""
    pp = _presets()
    p = dict(profile) if isinstance(profile, dict) else {}
    p[pp.SCHEMA_KEY] = pp.SCHEMA
    p, _ = pp.migrate(p)
    _save_json(get_data_dir() / "candidate_profile.json", normalize_profile(p))


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

def _settings_defaults() -> dict:
    return {
        "resume_path": "",
        "blocked_domains": [],
        "sensitive_data": {"email": ""},
        "max_failures": 8,
        "stagger_delay": 5,
        "data_dir": str(get_data_dir()),
        "telemetry_enabled": False,  # opt-in only
        "theme": "system",
    }


def normalize_settings(settings) -> dict:
    """Settings whose known fields have the types the app relies on; a text field holding a bool,
    a list or an object (e.g. ``resume_path: true``) falls back to its default."""
    if not isinstance(settings, dict):
        return _settings_defaults()
    d = _settings_defaults()
    return {k: (_lenient_like(d[k], v, bool_text=False) if k in d else v)
            for k, v in settings.items() if isinstance(k, str)}


def load_settings() -> dict:
    return normalize_settings(_load_json(get_data_dir() / "settings.json", _settings_defaults()))


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


def _clamped_int(value, default: int, lo: int, hi: int) -> int:
    try:
        n = int(float(value)) if not isinstance(value, bool) else default
    except (TypeError, ValueError, OverflowError):
        n = default
    return max(lo, min(n, hi))


def save_settings(settings: dict):
    normalized = normalize_settings(settings)
    settings.clear()
    settings.update(normalized)
    move_shared_password_to_vault(settings)
    if "max_failures" in settings:
        settings["max_failures"] = _clamped_int(settings["max_failures"], 8, 1, 50)
    if "stagger_delay" in settings:
        settings["stagger_delay"] = _clamped_int(settings["stagger_delay"], 5, 0, 300)
    if "blocked_domains" in settings:
        settings["blocked_domains"] = [
            str(d).strip() for d in settings["blocked_domains"]
            if isinstance(d, str) and d.strip()
        ]
    _save_json(get_data_dir() / "settings.json", settings)

