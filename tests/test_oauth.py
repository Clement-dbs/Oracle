from types import SimpleNamespace

import app.google.oauth as oauth_module


def _mock_userinfo(monkeypatch, *, email=None, raises=None):
    def fake_get(url, headers=None, timeout=None):
        if raises:
            raise raises
        return SimpleNamespace(
            raise_for_status=lambda: None,
            json=lambda: {"email": email} if email else {},
        )

    monkeypatch.setattr(oauth_module.http_requests, "get", fake_get)


def test_check_allowed_domain_disabled_when_no_restriction(monkeypatch):
    """GOOGLE_OAUTH_ALLOWED_DOMAIN non défini -- comportement permissif,
    aucun appel réseau ne doit être fait."""

    def boom(*args, **kwargs):
        raise AssertionError("userinfo ne devrait pas être appelé sans restriction")

    monkeypatch.setattr(oauth_module, "GOOGLE_OAUTH_ALLOWED_DOMAIN", None)
    monkeypatch.setattr(oauth_module.http_requests, "get", boom)

    assert oauth_module._check_allowed_domain("token") is None


def test_check_allowed_domain_accepts_matching_domain(monkeypatch):
    monkeypatch.setattr(oauth_module, "GOOGLE_OAUTH_ALLOWED_DOMAIN", "strattt.com")
    _mock_userinfo(monkeypatch, email="clement.dubois@strattt.com")

    assert oauth_module._check_allowed_domain("token") is None


def test_check_allowed_domain_is_case_insensitive(monkeypatch):
    monkeypatch.setattr(oauth_module, "GOOGLE_OAUTH_ALLOWED_DOMAIN", "Strattt.COM")
    _mock_userinfo(monkeypatch, email="clement.dubois@STRATTT.com")

    assert oauth_module._check_allowed_domain("token") is None


def test_check_allowed_domain_rejects_other_domain(monkeypatch):
    monkeypatch.setattr(oauth_module, "GOOGLE_OAUTH_ALLOWED_DOMAIN", "strattt.com")
    _mock_userinfo(monkeypatch, email="someone@gmail.com")

    error = oauth_module._check_allowed_domain("token")

    assert error is not None
    assert "someone@gmail.com" in error
    assert "strattt.com" in error


def test_check_allowed_domain_missing_email_rejected(monkeypatch):
    """Réponse userinfo sans champ "email" (scope refusé, token invalide...) :
    on refuse par prudence plutôt que de laisser passer."""
    monkeypatch.setattr(oauth_module, "GOOGLE_OAUTH_ALLOWED_DOMAIN", "strattt.com")
    _mock_userinfo(monkeypatch, email=None)

    assert oauth_module._check_allowed_domain("token") is not None


def test_check_allowed_domain_falls_back_on_request_error(monkeypatch):
    """L'appel à l'API userinfo échoue (réseau, timeout...) : on refuse plutôt
    que de stocker des credentials dont on n'a pas pu vérifier le domaine."""
    monkeypatch.setattr(oauth_module, "GOOGLE_OAUTH_ALLOWED_DOMAIN", "strattt.com")
    _mock_userinfo(monkeypatch, raises=RuntimeError("timeout"))

    assert oauth_module._check_allowed_domain("token") is not None
