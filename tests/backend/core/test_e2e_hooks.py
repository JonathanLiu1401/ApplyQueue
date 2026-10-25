"""The e2e endpoint hook only ever accepts a loopback base URL."""
import pytest

from core import e2e_hooks
from core.mail import oauth, providers
from core.sync import github


@pytest.fixture
def restore(monkeypatch):
    monkeypatch.setitem(oauth.ENDPOINTS, "gmail", dict(oauth.ENDPOINTS["gmail"]))
    monkeypatch.setitem(oauth.ENDPOINTS, "outlook", dict(oauth.ENDPOINTS["outlook"]))
    monkeypatch.setitem(providers.API_BASE, "gmail", providers.API_BASE["gmail"])
    monkeypatch.setitem(providers.API_BASE, "outlook", providers.API_BASE["outlook"])
    monkeypatch.setattr(github, "WEB", github.WEB)
    monkeypatch.setattr(github, "API", github.API)


def test_unset_is_noop(restore, monkeypatch):
    monkeypatch.delenv("APPLYQUEUE_E2E_MOCK_MAIL", raising=False)
    monkeypatch.delenv("APPLYQUEUE_E2E_MOCK_GITHUB", raising=False)
    assert e2e_hooks.apply() == {}
    assert oauth.ENDPOINTS["gmail"]["token"].startswith("https://oauth2.googleapis.com")


@pytest.mark.parametrize("bad", ["https://evil.example.com", "http://127.0.0.1.example.com:80",
                                 "http://localhost:9000", "http://10.0.0.5:80", "http://127.0.0.1:80/x",
                                 "file:///etc/passwd", "http://127.0.0.1"])
def test_non_loopback_refused(restore, monkeypatch, bad):
    monkeypatch.setenv("APPLYQUEUE_E2E_MOCK_MAIL", bad)
    monkeypatch.setenv("APPLYQUEUE_E2E_MOCK_GITHUB", bad)
    assert e2e_hooks.apply() == {}
    assert github.API == "https://api.github.com"
    assert providers.API_BASE["outlook"].startswith("https://graph.microsoft.com")


def test_loopback_points_everything(restore, monkeypatch):
    monkeypatch.setenv("APPLYQUEUE_E2E_MOCK_MAIL", "http://127.0.0.1:5555/")
    monkeypatch.setenv("APPLYQUEUE_E2E_MOCK_GITHUB", "http://127.0.0.1:6666")
    assert e2e_hooks.apply() == {"mail": "http://127.0.0.1:5555", "github": "http://127.0.0.1:6666"}
    assert oauth.ENDPOINTS["outlook"]["token"] == "http://127.0.0.1:5555/ms/{tenant}/oauth2/v2.0/token"
    assert providers.API_BASE["gmail"] == "http://127.0.0.1:5555/gmail/v1/users/me"
    assert github.WEB == github.API == "http://127.0.0.1:6666"
