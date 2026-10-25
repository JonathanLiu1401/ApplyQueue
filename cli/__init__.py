# Force third-party telemetry off before the CLI modules import browser-use.
try:
    from core import privacy_env as _privacy_env  # noqa: F401
except ImportError:
    from backend.core import privacy_env as _privacy_env  # noqa: F401
