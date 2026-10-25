"""Codex / ChatGPT / Claude tokens must never be written into the sync repo (PLAN §3.2)."""
import json

import pytest

from core.token_guard import SyncSecretError, check_sync_payload, find_tokens, safe_sync_write, scrub

# Token-shaped strings are assembled at runtime so secret scanners do not flag this file.
JWT = "ey" + "J" + "hbGciOiJSUzI1NiJ9" + "." + "eyJzdWIiOiJ4eXoifQ" + "." + "c2lnbmF0dXJlLXZhbHVl"
OPENAI = "sk" + "-proj-" + "A1b2C3d4" * 4
ANTHROPIC = "sk" + "-ant-" + "api03-" + "Z9y8X7w6" * 4
GOOGLE_RT = "1" + "//0" + "gAbCdEfGhIjKlMnOpQrStUv"


def codex_auth_json():
    return json.dumps({"OPENAI_API_KEY": None, "tokens": {"id_token": JWT, "access_token": JWT,
                                                          "refresh_token": "rt_" + "x" * 30, "account_id": "acct"},
                       "last_refresh": "2026-10-01T00:00:00Z"})


@pytest.mark.parametrize("payload,kind", [
    (codex_auth_json(), "codex_auth_json"),
    (f"token={JWT}", "jwt"),
    (f"OPENAI_API_KEY={OPENAI}", "openai_key"),
    (f"key: {ANTHROPIC}", "anthropic_key"),
    (json.dumps({"refresh_token": GOOGLE_RT}), "oauth_token_field"),
])
def test_find_tokens(payload, kind):
    assert kind in find_tokens(payload)


def test_clean_text_passes():
    assert find_tokens(json.dumps({"jobs": [{"url": "https://jobs.example.com/1", "status": "pending"}]})) == []


def test_scrub_redacts():
    out = scrub(f"error from CLI: Authorization: Bearer {JWT} and {OPENAI}")
    assert JWT not in out and OPENAI not in out and "[REDACTED]" in out


def test_sync_rejects_codex_token_and_credential_files(tmp_path):
    with pytest.raises(SyncSecretError):
        safe_sync_write(tmp_path, "profile/notes.json", codex_auth_json())
    for name in ("auth.json", ".credentials.json", "email_accounts.json", "site_credentials.json"):
        with pytest.raises(SyncSecretError):
            check_sync_payload(f"x/{name}", "{}")
    with pytest.raises(SyncSecretError):
        safe_sync_write(tmp_path, "../escape.json", "{}")
    with pytest.raises(SyncSecretError):
        safe_sync_write(tmp_path, ".git/config", "x")
    assert not list(tmp_path.rglob("*.json"))


def test_sync_allows_clean_write(tmp_path):
    p = safe_sync_write(tmp_path, "jobs/state.json", '{"ok": true}')
    assert p.read_text() == '{"ok": true}'
