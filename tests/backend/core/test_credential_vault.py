"""Per-site generated passwords replace upstream's shared hard-coded one."""
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from core.credential_vault import (
    SYMBOLS, CredentialVault, generate_password, migrate_settings_password, normalize_host,
)

REPO = Path(__file__).resolve().parents[3]


def test_generated_passwords_are_strong_and_unique():
    pws = {generate_password() for _ in range(200)}
    assert len(pws) == 200
    for pw in list(pws)[:50]:
        assert len(pw) >= 16
        assert any(c.islower() for c in pw) and any(c.isupper() for c in pw)
        assert any(c.isdigit() for c in pw) and any(c in SYMBOLS for c in pw)


def test_normalize_host():
    assert normalize_host("https://WWW.Example.com/jobs/1?x=1") == "example.com"
    assert normalize_host("jobs.example.com") == "jobs.example.com"


def test_one_password_per_site_persisted(tmp_path):
    path = tmp_path / "vault" / "site_credentials.json"
    v = CredentialVault(path)
    a, created_a = v.get_or_create("https://a.example.com/apply", "alex@example.com")
    b, _ = v.get_or_create("b.example.com", "alex@example.com")
    again, created_again = v.get_or_create("a.example.com", "alex@example.com")
    assert created_a and not created_again
    assert a["password"] == again["password"] != b["password"]
    reopened = CredentialVault(path)
    assert reopened.password_for_sign_in("a.example.com") == a["password"]
    assert "password" not in str(reopened.list_sites())


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permissions")
def test_vault_file_is_private(tmp_path):
    path = tmp_path / "vault" / "site_credentials.json"
    CredentialVault(path).get_or_create("example.com", "alex@example.com")
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600


def test_settings_password_migrates_to_legacy_slot(tmp_path):
    v = CredentialVault(tmp_path / "v.json")
    settings = {"sensitive_data": {"email": "alex@example.com", "password": "OldShared1!"}}
    assert migrate_settings_password(settings, v)
    assert "password" not in settings["sensitive_data"]
    assert v.has_legacy_shared()
    # Legacy password is only a sign-in fallback; new sites still get a fresh one.
    assert v.password_for_sign_in("unknown.example.com") == "OldShared1!"
    fresh, _ = v.get_or_create("new.example.com", "alex@example.com")
    assert fresh["password"] != "OldShared1!"
    assert not migrate_settings_password(settings, v)


def test_upstream_hardcoded_password_is_gone_from_code():
    """Docs may mention the old password historically; code must not."""
    needle = "JobApp" + "2026"
    out = subprocess.run(["git", "grep", "-n", needle, "--", ".", ":!*.lock", ":!*.md"], cwd=REPO,
                         capture_output=True, text=True)
    assert out.stdout.strip() == "", out.stdout
