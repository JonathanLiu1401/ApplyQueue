"""Privacy-preserving environment defaults, applied before browser-use loads.

browser-use ships anonymous product telemetry (PostHog) and cloud sync that are
ON unless ``ANONYMIZED_TELEMETRY`` is explicitly false (an empty value counts
as true). ApplyQueue telemetry is opt-in, so both are forced off by default.
An explicit, non-empty value already present in the environment is respected
(a developer can still opt in). LangChain/LangSmith tracing is likewise off.
"""
import os

_DEFAULTS = {
    "ANONYMIZED_TELEMETRY": "false",
    "BROWSER_USE_CLOUD_SYNC": "false",
    "LANGCHAIN_TRACING_V2": "false",
    "LANGSMITH_TRACING": "false",
}


def apply_privacy_env(environ=None) -> None:
    env = os.environ if environ is None else environ
    for key, value in _DEFAULTS.items():
        if not env.get(key, "").strip():
            env[key] = value


apply_privacy_env()
