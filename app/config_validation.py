"""Startup validation of configuration and environment variables.

The goal is "fail safely": a production deployment that is missing a
required secret (or has a placeholder one) must refuse to start, loudly and
with every problem listed at once, rather than quietly running with a
published default key. Development and test runs stay frictionless.

Environment is decided by APP_ENV ("production" — the default, i.e. the safe
one — "development", or "testing"); a config with TESTING=True is always
"testing".
"""
import os
import secrets
from urllib.parse import urlparse

MIN_SECRET_LENGTH = 32

# Values that have shipped in this repo's docs/defaults or are universally
# guessable. Compared case-insensitively against the whole key; a key that
# merely *contains* a placeholder-ish phrase is also rejected.
KNOWN_PLACEHOLDER_SECRETS = {
    "dev-secret-key-change-this-in-production",
    "change-this-to-a-random-secret",
    "change-this-in-production",
    "changeme",
    "change-me",
    "secret",
    "secret-key",
    "test-secret-key",
    "your-secret-key",
    "supersecret",
    "password",
}
PLACEHOLDER_FRAGMENTS = ("change-this", "change_this", "changeme", "change-me", "your-secret", "example")

GENERATE_HINT = "python -c \"import secrets; print(secrets.token_urlsafe(48))\""


class ConfigError(RuntimeError):
    """Raised at startup when the configuration is unsafe or invalid."""


def environment(config):
    if config.get("TESTING"):
        return "testing"
    env = str(config.get("APP_ENV") or "production").strip().lower()
    return env if env in ("production", "development", "testing") else "production"


def is_production(config):
    return environment(config) == "production"


def dev_mail_fallback_allowed(config):
    """May emails (OTP codes, invitation links, ...) be echoed to the screen
    when no SMTP server is configured? Only outside production — in
    production that would hand any visitor the password-reset code for any
    address they type in."""
    return environment(config) in ("development", "testing")


def _secret_problems(key):
    problems = []
    if not key:
        problems.append("SECRET_KEY is not set.")
        return problems
    low = key.lower()
    if low in KNOWN_PLACEHOLDER_SECRETS or any(f in low for f in PLACEHOLDER_FRAGMENTS):
        problems.append("SECRET_KEY is a placeholder/known value — anyone who has seen the docs can forge sessions with it.")
    if len(key) < MIN_SECRET_LENGTH:
        problems.append(f"SECRET_KEY is too short ({len(key)} chars; need at least {MIN_SECRET_LENGTH}).")
    if len(set(key)) < 8:
        problems.append("SECRET_KEY has too little variety (looks repetitive/guessable).")
    return problems


def _truthy_env(name):
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes")


def validate_config(app):
    """Validate app.config for the current environment. Raises ConfigError
    listing every problem; returns a list of non-fatal warnings. Also fills
    in the environment-appropriate runtime defaults (a random dev secret,
    hardened cookie flags in production)."""
    config = app.config
    env = environment(config)
    config["APP_ENV"] = env
    errors, warnings = [], []

    if env == "testing":
        return warnings

    # --- problems found while parsing environment variables (config.py) ---
    try:
        import config as config_module
        errors.extend(getattr(config_module, "ENV_ERRORS", []))
    except Exception:  # pragma: no cover - config module always importable in practice
        pass

    # --- SECRET_KEY -------------------------------------------------------
    key = config.get("SECRET_KEY")
    if env == "production":
        errors.extend(_secret_problems(key))
    else:
        if not key:
            config["SECRET_KEY"] = secrets.token_urlsafe(48)
            warnings.append(
                "SECRET_KEY is not set: generated a random one for this process. Sessions will be "
                "invalidated on every restart (and differ between workers). Set SECRET_KEY in .env."
            )
        elif _secret_problems(key):
            warnings.append("SECRET_KEY is a weak/placeholder value (fine for local development only).")

    if env == "production":
        # --- required deployment settings ---------------------------------
        base = (config.get("APP_BASE_URL") or "").strip()
        if not base:
            errors.append("APP_BASE_URL is not set (needed to build the links in invitation emails safely).")
        else:
            parsed = urlparse(base if "://" in base else f"//{base}", scheme="https")
            if not parsed.netloc:
                errors.append(f"APP_BASE_URL is not a valid URL: {base!r}")
            elif parsed.scheme != "https" and not _truthy_env("ALLOW_INSECURE_BASE_URL"):
                errors.append("APP_BASE_URL must start with https:// in production (set ALLOW_INSECURE_BASE_URL=true only for a private network).")

        if not config.get("MAIL_SERVER"):
            errors.append(
                "MAIL_SERVER is not set. Verification codes, password-reset codes and staff invitations are "
                "delivered by email; without SMTP they would have to be shown on screen, which is only "
                "allowed in development."
            )
        if config.get("WTF_CSRF_ENABLED") is False:
            errors.append("WTF_CSRF_ENABLED must not be disabled in production.")
        if config.get("DEBUG") or app.debug:
            errors.append("Debug mode must be off in production (unset FLASK_DEBUG / DEBUG).")

        uri = str(config.get("SQLALCHEMY_DATABASE_URI") or "")
        if uri.startswith("sqlite") and not _truthy_env("ALLOW_SQLITE_IN_PRODUCTION"):
            warnings.append(
                "Production is using SQLite. That is fine for a single small node, but use "
                "PostgreSQL/MySQL (DATABASE_URL) for multiple workers/containers; set "
                "ALLOW_SQLITE_IN_PRODUCTION=true to silence this."
            )

        # Secure-only cookies in production. (Flask pre-populates these keys
        # with its own insecure defaults, so setdefault() would never apply —
        # assign explicitly; ALLOW_INSECURE_COOKIES=true is the opt-out for a
        # plain-http private network.)
        if not _truthy_env("ALLOW_INSECURE_COOKIES"):
            config["SESSION_COOKIE_SECURE"] = True
            config["REMEMBER_COOKIE_SECURE"] = True

    # Cookie flags that are right in every environment.
    config["SESSION_COOKIE_HTTPONLY"] = True
    config["REMEMBER_COOKIE_HTTPONLY"] = True
    if not config.get("SESSION_COOKIE_SAMESITE"):
        config["SESSION_COOKIE_SAMESITE"] = "Lax"
    if not config.get("REMEMBER_COOKIE_SAMESITE"):
        config["REMEMBER_COOKIE_SAMESITE"] = "Lax"

    # --- consistency checks that apply in any non-test environment --------
    if config.get("MAIL_SERVER"):
        if bool(config.get("MAIL_USERNAME")) != bool(config.get("MAIL_PASSWORD")):
            errors.append("MAIL_USERNAME and MAIL_PASSWORD must be set together (or both left blank).")
        if not config.get("MAIL_SENDER"):
            errors.append("MAIL_SENDER is required when MAIL_SERVER is set.")
    for prefix in ("GOOGLE", "MICROSOFT"):
        cid, sec = config.get(f"{prefix}_CLIENT_ID"), config.get(f"{prefix}_CLIENT_SECRET")
        if bool(cid) != bool(sec):
            errors.append(f"{prefix}_CLIENT_ID and {prefix}_CLIENT_SECRET must be set together.")
    twilio = [config.get(k) for k in ("TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_FROM_NUMBER")]
    if any(twilio) and not all(twilio):
        errors.append("TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN and TWILIO_FROM_NUMBER must all be set together.")

    if int(config.get("MAX_VIOLATIONS_BEFORE_TERMINATION", 5)) < 1:
        errors.append("MAX_VIOLATIONS must be at least 1.")
    if int(config.get("MAX_WARNINGS_BEFORE_TERMINATION", 2)) < 0:
        errors.append("MAX_WARNINGS must not be negative.")
    if int(config.get("OTP_MAX_ATTEMPTS", 5)) < 1 or int(config.get("OTP_TTL_MINUTES", 10)) < 1:
        errors.append("OTP_MAX_ATTEMPTS and OTP_TTL_MINUTES must be at least 1.")
    mode = str(config.get("SCHEDULER_MODE") or "").lower()
    if mode and mode not in ("external", "embedded", "off"):
        errors.append(f"SCHEDULER_MODE must be one of external, embedded, off — got {mode!r}.")

    if errors:
        lines = "\n".join(f"  - {e}" for e in errors)
        hint = f"\n\nGenerate a secret key with:\n  {GENERATE_HINT}" if any("SECRET_KEY" in e for e in errors) else ""
        raise ConfigError(
            f"Refusing to start: invalid configuration for APP_ENV={env}:\n{lines}{hint}\n\n"
            "(Set APP_ENV=development in your local .env for a relaxed development setup.)"
        )
    return warnings
