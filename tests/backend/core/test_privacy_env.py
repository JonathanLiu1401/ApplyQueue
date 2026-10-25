"""browser-use / LangSmith telemetry is off unless explicitly opted in."""
import os

from core.privacy_env import apply_privacy_env


def test_defaults_force_telemetry_off():
    env = {}
    apply_privacy_env(env)
    assert env["ANONYMIZED_TELEMETRY"] == "false"
    assert env["BROWSER_USE_CLOUD_SYNC"] == "false"


def test_empty_value_is_treated_as_unset():
    # browser-use treats an empty ANONYMIZED_TELEMETRY as *true*; we must not.
    env = {"ANONYMIZED_TELEMETRY": ""}
    apply_privacy_env(env)
    assert env["ANONYMIZED_TELEMETRY"] == "false"


def test_explicit_opt_in_is_respected():
    env = {"ANONYMIZED_TELEMETRY": "true"}
    apply_privacy_env(env)
    assert env["ANONYMIZED_TELEMETRY"] == "true"


def test_process_env_is_off_after_importing_core():
    import core  # noqa: F401
    from browser_use.config import CONFIG
    assert os.environ["ANONYMIZED_TELEMETRY"] == "false" or os.environ["ANONYMIZED_TELEMETRY"].lower().startswith("t")
    if os.environ["ANONYMIZED_TELEMETRY"] == "false":
        assert CONFIG.ANONYMIZED_TELEMETRY is False
        assert CONFIG.BROWSER_USE_CLOUD_SYNC is False
