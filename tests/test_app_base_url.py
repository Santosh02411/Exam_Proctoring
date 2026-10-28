"""APP_BASE_URL / TRUST_PROXY_HEADERS (config.py, app/__init__.py).

Every link this app emails (verification, password reset, exam reminders,
appeal notifications, ...) is built with url_for(..., _external=True),
which — without APP_BASE_URL set — derives its host from whatever request
triggered it. That's correct for a real request behind a correctly
configured reverse proxy, but it's also exactly why running the app
locally (`flask run`) produces links like http://127.0.0.1:5000/... in
real emails: that genuinely is the host the request arrived on. Setting
APP_BASE_URL overrides that, everywhere, including outside any request
(the CLI, the background reminder scheduler) where there's no Host header
to derive from at all.
"""
from tests.conftest import TestConfig


def _make_app(tmp_path, name, **config_overrides):
    """Matches this codebase's established test pattern (see conftest.py's
    TestConfig and every E2E/scheduler test config) — a fresh class with
    literal attribute overrides, not mutating environment variables and
    re-"importing" an already-cached config module, which plain Python
    module caching makes a no-op after the first import in any given
    process (this file's own config_object=config.Config, env-var-based
    first draft hit exactly that, which is also why the real derivation
    logic now lives in create_app() rather than config.py's class body —
    see the comment there)."""
    attrs = {"SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path}/{name}.db"}
    attrs.update(config_overrides)
    cfg = type(f"Cfg_{name}", (TestConfig,), attrs)
    from app import create_app
    return create_app(config_object=cfg())


def test_unset_app_base_url_leaves_server_name_alone(tmp_path):
    app = _make_app(tmp_path, "a")
    assert app.config.get("SERVER_NAME") is None
    with app.test_request_context(base_url="http://127.0.0.1:5000"):
        from flask import url_for
        assert url_for("auth.login", _external=True) == "http://127.0.0.1:5000/login"


def test_app_base_url_overrides_every_external_link(tmp_path):
    app = _make_app(tmp_path, "b", APP_BASE_URL="https://exams.my-college.edu")
    assert app.config["SERVER_NAME"] == "exams.my-college.edu"
    assert app.config["PREFERRED_URL_SCHEME"] == "https"
    with app.test_request_context():
        from flask import url_for
        assert url_for("auth.login", _external=True) == "https://exams.my-college.edu/login"
        assert url_for("auth.verify_email", token="tok123", _external=True) == \
            "https://exams.my-college.edu/verify-email/tok123"


def test_app_base_url_works_with_no_request_at_all(tmp_path):
    """The actual point of this fix: the CLI and the background scheduler
    build links with no incoming request/Host header to go on."""
    app = _make_app(tmp_path, "c", APP_BASE_URL="https://exams.my-college.edu")
    with app.app_context():
        from flask import url_for
        # No test_request_context() at all -- SERVER_NAME alone is enough.
        assert url_for("auth.login", _external=True) == "https://exams.my-college.edu/login"


def test_app_base_url_trailing_slash_is_stripped(tmp_path):
    app = _make_app(tmp_path, "d", APP_BASE_URL="https://exams.my-college.edu/")
    assert app.config["SERVER_NAME"] == "exams.my-college.edu"


def test_app_base_url_without_scheme_defaults_to_https(tmp_path):
    app = _make_app(tmp_path, "e", APP_BASE_URL="exams.my-college.edu")
    assert app.config["PREFERRED_URL_SCHEME"] == "https"


def test_trust_proxy_headers_defaults_off(tmp_path):
    app = _make_app(tmp_path, "f")
    assert app.config["TRUST_PROXY_HEADERS"] is False
    assert "ProxyFix" not in type(app.wsgi_app).__name__


def test_trust_proxy_headers_true_applies_proxyfix(tmp_path):
    app = _make_app(tmp_path, "g", TRUST_PROXY_HEADERS=True)
    assert "ProxyFix" in type(app.wsgi_app).__name__


def test_trust_proxy_headers_actually_fixes_remote_addr_and_scheme(tmp_path):
    """Not just that ProxyFix is attached -- that trusting one hop of
    X-Forwarded-* headers actually changes what the app sees, the way it
    would behind a real reverse proxy."""
    app = _make_app(tmp_path, "h", TRUST_PROXY_HEADERS=True)
    seen = {}

    @app.route("/_probe")
    def _probe():
        from flask import request
        seen["remote_addr"] = request.remote_addr
        seen["scheme"] = request.scheme
        return "ok"

    client = app.test_client()
    client.get(
        "/_probe",
        headers={
            "X-Forwarded-For": "203.0.113.7",
            "X-Forwarded-Proto": "https",
            "X-Forwarded-Host": "exams.my-college.edu",
        },
        environ_overrides={"REMOTE_ADDR": "10.0.0.5"},  # the proxy's own address
    )
    assert seen["remote_addr"] == "203.0.113.7", seen
    assert seen["scheme"] == "https", seen


def test_trust_proxy_headers_off_does_not_trust_spoofed_headers(tmp_path):
    """The default-off case this exists to protect: without an actual
    trusted proxy in front, a client sending these headers itself must NOT
    be able to fake its address."""
    app = _make_app(tmp_path, "i")
    seen = {}

    @app.route("/_probe")
    def _probe():
        from flask import request
        seen["remote_addr"] = request.remote_addr
        return "ok"

    client = app.test_client()
    client.get("/_probe", headers={"X-Forwarded-For": "203.0.113.7"}, environ_overrides={"REMOTE_ADDR": "10.0.0.5"})
    assert seen["remote_addr"] == "10.0.0.5", "an untrusted X-Forwarded-For header must be ignored"


def test_get_client_ip_no_longer_trusts_spoofed_x_forwarded_for(tmp_path):
    """Regression: app.utils.get_client_ip() used to read X-Forwarded-For
    directly and trust its first entry unconditionally -- so a client with
    no real reverse proxy in front of the app at all could bypass
    registration/forgot-password/resend-verification rate limiting (and
    pollute exam-session/security-event IP logging) just by sending a
    different fake X-Forwarded-For value on every request. It must now
    agree with app.access_control.get_client_ip (which never had this
    bug): request.remote_addr, untouched unless TRUST_PROXY_HEADERS is
    explicitly on."""
    app = _make_app(tmp_path, "j")  # TRUST_PROXY_HEADERS left at its default (off)
    seen = {}

    @app.route("/_probe")
    def _probe():
        from app.utils import get_client_ip
        from app.access_control import get_client_ip as ac_get_client_ip
        seen["utils"] = get_client_ip()
        seen["access_control"] = ac_get_client_ip()
        return "ok"

    client = app.test_client()
    for fake_ip in ("203.0.113.7", "198.51.100.42", "1.2.3.4"):
        client.get("/_probe", headers={"X-Forwarded-For": fake_ip}, environ_overrides={"REMOTE_ADDR": "10.0.0.5"})
        assert seen["utils"] == "10.0.0.5", f"get_client_ip trusted a spoofed X-Forwarded-For of {fake_ip}"
        assert seen["utils"] == seen["access_control"], "the two get_client_ip implementations must agree"
