"""Test-only: point the mail and GitHub clients at local mocks for the packaged-app e2e suite.

A packaged sidecar cannot be monkeypatched, so the UI e2e suite sets
APPLYQUEUE_E2E_MOCK_MAIL / APPLYQUEUE_E2E_MOCK_GITHUB / APPLYQUEUE_E2E_MOCK_XAI to a mock
server's base URL.
Only ``http://127.0.0.1:<port>`` is accepted, so the variables can never send OAuth codes,
tokens or mail anywhere but a server on the same machine. A normal install never sets them.
"""
from __future__ import annotations

import logging
import os
import re

_LOOPBACK = re.compile(r"^http://127\.0\.0\.1:(\d{2,5})$")
_log = logging.getLogger(__name__)


def _base(var: str) -> str | None:
    raw = (os.environ.get(var) or "").strip().rstrip("/")
    if not raw:
        return None
    if not _LOOPBACK.match(raw):
        _log.warning("%s ignored: only http://127.0.0.1:<port> is allowed", var)
        return None
    return raw


def apply() -> dict:
    """Rewrite endpoints when the e2e variables are set. Returns what was pointed where."""
    done = {}
    mail = _base("APPLYQUEUE_E2E_MOCK_MAIL")
    if mail:
        from core.mail import oauth, providers
        oauth.ENDPOINTS["gmail"] = {"auth": mail + "/google/auth", "token": mail + "/google/token",
                                    "revoke": mail + "/google/revoke"}
        oauth.ENDPOINTS["outlook"] = {"auth": mail + "/ms/{tenant}/oauth2/v2.0/authorize",
                                      "token": mail + "/ms/{tenant}/oauth2/v2.0/token", "revoke": ""}
        providers.API_BASE["gmail"] = mail + "/gmail/v1/users/me"
        providers.API_BASE["outlook"] = mail + "/graph/v1.0/me"
        done["mail"] = mail
    gh = _base("APPLYQUEUE_E2E_MOCK_GITHUB")
    if gh:
        from core.sync import github
        github.WEB = gh
        github.API = gh
        done["github"] = gh
    xai = _base("APPLYQUEUE_E2E_MOCK_XAI")
    if xai:
        from core import llm_factory
        llm_factory.xai_base_url = lambda: xai + "/v1"
        done["xai"] = xai
    if done:
        _log.warning("E2E mock endpoints active: %s", done)
    return done
