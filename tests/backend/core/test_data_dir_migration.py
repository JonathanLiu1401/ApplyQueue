"""LangHire -> ApplyQueue data-folder migration (backend/core/config.py)."""
import json

import core.config as config


def _legacy(root):
    legacy = root / "langhire"
    legacy.mkdir(parents=True)
    (legacy / "settings.json").write_text(json.dumps({"telemetry_enabled": True, "theme": "dark"}))
    (legacy / "jobs.json").write_text(json.dumps({"https://jobs.example.com/1": {"status": "pending"}}))
    (legacy / ".api_token").write_text("old-token")
    (legacy / "browser_profile").mkdir()
    (legacy / "browser_profile" / "Cookies").write_text("x")
    return legacy


def test_moves_everything_and_resets_telemetry(tmp_path):
    legacy = _legacy(tmp_path)
    assert config.migrate_legacy_data_dir(tmp_path) is True
    new = tmp_path / "applyqueue"
    assert json.loads((new / "jobs.json").read_text())["https://jobs.example.com/1"]["status"] == "pending"
    assert (new / "browser_profile" / "Cookies").exists()
    settings = json.loads((new / "settings.json").read_text())
    assert settings["telemetry_enabled"] is False  # opt-in again under ApplyQueue
    assert settings["theme"] == "dark"
    assert not (new / ".api_token").exists()  # session token is never carried over
    assert (new / ".migrated_from_langhire").exists()
    # Only the stale token was left, so the legacy folder is cleaned up.
    assert not legacy.exists()


def test_idempotent(tmp_path):
    _legacy(tmp_path)
    assert config.migrate_legacy_data_dir(tmp_path) is True
    assert config.migrate_legacy_data_dir(tmp_path) is False


def test_does_not_overwrite_existing_new_data(tmp_path):
    legacy = _legacy(tmp_path)
    new = tmp_path / "applyqueue"
    new.mkdir()
    (new / "jobs.json").write_text(json.dumps({"keep": True}))
    config.migrate_legacy_data_dir(tmp_path)
    assert json.loads((new / "jobs.json").read_text()) == {"keep": True}
    # The conflicting legacy file is kept, so nothing is lost.
    assert (legacy / "jobs.json").exists()
    assert (new / "settings.json").exists()


def test_no_legacy_is_noop(tmp_path):
    assert config.migrate_legacy_data_dir(tmp_path) is False
    assert not (tmp_path / "applyqueue").exists()


def test_default_settings_have_telemetry_off_and_no_shared_password(data_dir):
    s = config.load_settings()
    assert s["telemetry_enabled"] is False
    assert "password" not in s["sensitive_data"]
