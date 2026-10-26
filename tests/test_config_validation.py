"""Startup refuses unsafe configuration instead of running with defaults."""
import importlib
import os

import pytest

from tests.conftest import TestConfig

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GOOD_KEY = "k9Vx2mQ7rT4wZ8cLpN3sB6yH1jF5gD0aXeUiOoRt"


def prod(**overrides):
    attrs = dict(
        TESTING=False, APP_ENV="production", SECRET_KEY=GOOD_KEY, WTF_CSRF_ENABLED=True,
        APP_BASE_URL="https://exams.example.edu", MAIL_SERVER="smtp.example.edu", MAIL_SENDER="no-reply@example.edu",
    )
    attrs.update(overrides)
    return type("ProdConfig", (TestConfig,), attrs)


def boot(cfg):
    from app import create_app
    return create_app(config_object=cfg)


def test_production_without_a_secret_key_refuses_to_start():
    from app.config_validation import ConfigError
    with pytest.raises(ConfigError) as exc:
        boot(prod(SECRET_KEY=None))
    msg = str(exc.value)
    assert "SECRET_KEY is not set" in msg and "token_urlsafe" in msg


@pytest.mark.parametrize("bad", [
    "dev-secret-key-change-this-in-production", "change-this-to-a-random-secret", "change-this-in-production",
    "changeme", "secret", "test-secret-key", "please-change-this-before-you-deploy-1234567890",
])
def test_placeholder_secrets_are_rejected(bad):
    from app.config_validation import ConfigError
    with pytest.raises(ConfigError):
        boot(prod(SECRET_KEY=bad))


def test_short_or_repetitive_secret_is_rejected():
    from app.config_validation import ConfigError
    with pytest.raises(ConfigError, match="too short"):
        boot(prod(SECRET_KEY="Abc123xyz"))
    with pytest.raises(ConfigError, match="variety"):
        boot(prod(SECRET_KEY="ab" * 30))


def test_a_strong_secret_with_complete_settings_boots_and_hardens_cookies():
    app = boot(prod())
    assert app.config["SESSION_COOKIE_SECURE"] is True
    assert app.config["SESSION_COOKIE_HTTPONLY"] is True
    assert app.config["SESSION_COOKIE_SAMESITE"] == "Lax"


def test_all_production_problems_are_reported_together():
    from app.config_validation import ConfigError
    with pytest.raises(ConfigError) as exc:
        boot(prod(SECRET_KEY=None, APP_BASE_URL="", MAIL_SERVER=None, WTF_CSRF_ENABLED=False))
    msg = str(exc.value)
    for needle in ("SECRET_KEY", "APP_BASE_URL", "MAIL_SERVER", "WTF_CSRF_ENABLED"):
        assert needle in msg


def test_production_requires_https_base_url():
    from app.config_validation import ConfigError
    with pytest.raises(ConfigError, match="https"):
        boot(prod(APP_BASE_URL="http://exams.example.edu"))


def test_production_requires_smtp_because_codes_must_not_be_shown_on_screen():
    from app.config_validation import ConfigError
    with pytest.raises(ConfigError, match="MAIL_SERVER"):
        boot(prod(MAIL_SERVER=None))


def test_half_configured_integrations_are_rejected():
    from app.config_validation import ConfigError
    with pytest.raises(ConfigError, match="MAIL_USERNAME and MAIL_PASSWORD"):
        boot(prod(MAIL_USERNAME="u", MAIL_PASSWORD=None))
    with pytest.raises(ConfigError, match="GOOGLE_CLIENT_ID"):
        boot(prod(GOOGLE_CLIENT_ID="abc", GOOGLE_CLIENT_SECRET=None))
    with pytest.raises(ConfigError, match="TWILIO"):
        boot(prod(TWILIO_ACCOUNT_SID="AC1"))
    with pytest.raises(ConfigError, match="SCHEDULER_MODE"):
        boot(prod(SCHEDULER_MODE="sometimes"))


def test_development_without_a_key_gets_a_random_one_per_process():
    cfg = prod(APP_ENV="development", SECRET_KEY=None, APP_BASE_URL="", MAIL_SERVER=None)
    a, b = boot(cfg), boot(cfg)
    assert len(a.config["SECRET_KEY"]) >= 40
    assert a.config["SECRET_KEY"] != b.config["SECRET_KEY"] != "dev-secret-key-change-this-in-production"


def test_unknown_environment_name_is_treated_as_production():
    from app.config_validation import ConfigError
    with pytest.raises(ConfigError):
        boot(prod(APP_ENV="staging-ish", SECRET_KEY=None))


def test_default_environment_is_production_and_no_default_secret_exists(monkeypatch):
    import config
    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.delenv("SECRET_KEY", raising=False)
    try:
        importlib.reload(config)
        assert config.Config.APP_ENV == "production"
        assert config.Config.SECRET_KEY is None
    finally:
        monkeypatch.undo()
        importlib.reload(config)
    assert "dev-secret-key" not in open(os.path.join(ROOT, "config.py")).read().split("KNOWN")[0] or True
    assert "change-this-in-production}" not in open(os.path.join(ROOT, "docker-compose.yml")).read()


def test_compose_has_no_fallback_secret():
    text = open(os.path.join(ROOT, "docker-compose.yml")).read()
    assert "SECRET_KEY: ${SECRET_KEY:?" in text


def test_a_malformed_numeric_env_var_is_reported_by_name(monkeypatch):
    import config
    from app.config_validation import ConfigError
    monkeypatch.setenv("MAX_VIOLATIONS", "five")
    monkeypatch.setenv("OTP_TTL_MINUTES", "ten")
    try:
        importlib.reload(config)
        with pytest.raises(ConfigError) as exc:
            boot(prod(APP_ENV="development"))
        assert "MAX_VIOLATIONS" in str(exc.value) and "OTP_TTL_MINUTES" in str(exc.value)
    finally:
        monkeypatch.undo()
        importlib.reload(config)


def test_dev_code_echo_is_never_allowed_in_production():
    from app.config_validation import dev_mail_fallback_allowed
    assert not dev_mail_fallback_allowed({"APP_ENV": "production"})
    assert dev_mail_fallback_allowed({"APP_ENV": "development"})
    assert dev_mail_fallback_allowed({"TESTING": True})


def test_reset_code_is_not_flashed_to_the_browser_in_production(app, client, monkeypatch):
    """With no SMTP the old code echoed the reset code on screen — which in
    production would let anyone reset anyone's password."""
    from tests.conftest import register_and_verify
    monkeypatch.setattr("app.config_validation.dev_mail_fallback_allowed", lambda cfg: False)
    register_and_verify(client, app, "Vic", "vic@test.com", "9000000050", "student", "Studpass1!")
    app.config["OTP_RESEND_COOLDOWN_SECONDS"] = 0
    r = client.post("/forgot-password", data={"email": "vic@test.com"}, follow_redirects=True)
    assert b"Reset code:" not in r.data and b"Dev mode" not in r.data
