# Force third-party telemetry off before anything imports browser-use.
from . import privacy_env as _privacy_env  # noqa: F401
# Core backend modules
