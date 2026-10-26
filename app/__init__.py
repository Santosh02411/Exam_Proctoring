import os
import time
import json
from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager
from flask_migrate import Migrate

db = SQLAlchemy()
migrate = Migrate()
login_manager = LoginManager()
login_manager.login_view = "auth.login"
login_manager.login_message = "Please log in to continue."
login_manager.login_message_category = "warning"


def create_app(config_object="config.Config"):
    app = Flask(__name__, instance_relative_config=True)
    app.config.from_object(config_object)
    app.config["APP_STARTED_AT"] = time.time()

    # Fail fast (and loudly) on a missing/placeholder SECRET_KEY, bad env
    # values, etc. — before anything else touches the config. See
    # app.config_validation for exactly what is enforced per environment.
    from app.config_validation import validate_config
    for _warning in validate_config(app):
        app.logger.warning("config: %s", _warning)

    # Derived fresh from app.config here, not computed once when config.py
    # happens to first be imported: APP_BASE_URL might come from the
    # APP_BASE_URL environment variable (config.Config's default), or be
    # set directly on a config object (a test config, say) — either way,
    # by the time app.config.from_object() above has run, app.config["APP_
    # BASE_URL"] is the final value to act on, so that's what this reads.
    app_base_url = (app.config.get("APP_BASE_URL") or "").strip().rstrip("/")
    if app_base_url:
        from urllib.parse import urlparse
        parsed = urlparse(app_base_url if "://" in app_base_url else f"//{app_base_url}", scheme="https")
        if parsed.netloc:
            app.config["SERVER_NAME"] = parsed.netloc
            app.config["PREFERRED_URL_SCHEME"] = parsed.scheme or "https"

    # Fall back to sane defaults for anything a custom config object (e.g.
    # tests/conftest.py's TestConfig, which predates these settings and
    # doesn't subclass config.Config) doesn't set explicitly, so this
    # never becomes a hard requirement for every config object in existence.
    app.config.setdefault("BACKUPS_DIR", os.path.join(app.instance_path, "backups"))
    app.config.setdefault("RECORDING_RETENTION_DAYS", 90)
    app.config.setdefault("SNAPSHOT_RETENTION_DAYS", 90)
    app.config.setdefault("ENDED_LOGIN_SESSION_RETENTION_DAYS", 180)
    app.config.setdefault("LOGIN_SECURITY_EVENT_RETENTION_DAYS", 180)
    app.config.setdefault("ACTIVITY_LOG_RETENTION_DAYS", 365)
    app.config.setdefault("NOTIFICATION_LOG_RETENTION_DAYS", 180)
    app.config.setdefault("ERROR_LOG_RETENTION_DAYS", 90)
    app.config.setdefault("TERMS_VERSION", "2026-08-28")
    app.config.setdefault("ORG_BRANDING_DIR", os.path.join(app.instance_path, "org_branding"))
    app.config.setdefault("ORG_LOGO_MAX_BYTES", 2 * 1024 * 1024)
    app.config.setdefault("ORG_LOGO_ALLOWED_EXTS", {"png", "jpg", "jpeg", "svg", "webp"})
    app.config.setdefault("PROCTOR_ALERT_POLL_SECONDS", 3)
    app.config.setdefault("PROCTOR_ALERT_STREAM_SECONDS", 55)
    app.config.setdefault("EXAM_SESSION_STALE_AFTER_SECONDS", 45)
    app.config.setdefault("EXAM_SESSION_ENFORCE_SINGLE_SESSION", True)
    app.config.setdefault("LMS_WEBHOOK_TIMEOUT_SECONDS", 4)
    app.config.setdefault("MAX_WARNINGS_BEFORE_TERMINATION", 2)
    app.config.setdefault("APPEAL_WINDOW_DAYS", 14)
    app.config.setdefault("LIVE_MONITOR_NO_SIGNAL_SECONDS", 60)
    app.config.setdefault("TRUST_PROXY_HEADERS", False)

    if app.config["TRUST_PROXY_HEADERS"]:
        # Only reached when explicitly opted into (see config.py) — trusts
        # one layer of X-Forwarded-* headers so request.remote_addr and
        # url_for(_external=True)'s scheme/host are correct behind a real
        # reverse proxy, instead of showing the proxy's own address/scheme.
        from werkzeug.middleware.proxy_fix import ProxyFix
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

    @app.template_filter("from_json")
    def _from_json_filter(value):
        """Parse a JSON string stored in a text column (e.g.
        Attempt.device_info) for display — used instead of Python's own
        |tojson (which serializes *to* JSON) since this goes the other
        way. Renders as an empty dict rather than raising on bad/missing
        input, since template code treats the result as always-a-dict."""
        if not value:
            return {}
        try:
            return json.loads(value)
        except (TypeError, ValueError):
            return {}

    os.makedirs(app.instance_path, exist_ok=True)
    os.makedirs(app.config["SNAPSHOT_UPLOAD_DIR"], exist_ok=True)
    os.makedirs(app.config["RECORDINGS_DIR"], exist_ok=True)
    os.makedirs(app.config["ID_DOCUMENT_UPLOAD_DIR"], exist_ok=True)
    os.makedirs(app.config["BACKUPS_DIR"], exist_ok=True)
    os.makedirs(app.config["ORG_BRANDING_DIR"], exist_ok=True)

    db.init_app(app)
    # Schema changes are Alembic migrations (see migrations/ and README):
    # `flask db upgrade` applies them. The app itself NEVER creates tables or
    # alters columns at startup any more — that used to happen silently, with
    # failures swallowed. render_as_batch makes ALTERs work on SQLite too.
    migrate.init_app(app, db, render_as_batch=True, compare_type=True)
    login_manager.init_app(app)

    from app.models import User

    @login_manager.user_loader
    def load_user(user_id):
        user = db.session.get(User, int(user_id))
        # A deactivated/pending account must not keep working on an old
        # session cookie: returning None logs it out on the next request.
        if user is None or user.status != "active":
            return None
        return user

    from app.auth import bp as auth_bp
    from app.admin import bp as admin_bp
    from app.student import bp as student_bp
    from app.proctoring import bp as proctoring_bp
    from app.profile import bp as profile_bp
    from app.organizations import bp as organizations_bp
    from app.system_ops import bp as system_ops_bp
    from app.legal import bp as legal_bp
    from app.api_v1 import bp as api_v1_bp
    from app.sso import bp as sso_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(student_bp)
    app.register_blueprint(proctoring_bp)
    app.register_blueprint(profile_bp)
    app.register_blueprint(organizations_bp)
    app.register_blueprint(system_ops_bp)
    app.register_blueprint(legal_bp)
    app.register_blueprint(api_v1_bp)
    app.register_blueprint(sso_bp)

    from app.sso import init_oauth
    init_oauth(app)

    from app import security

    @app.before_request
    def _enforce_single_session():
        security.enforce_single_session()

    from flask import redirect, url_for
    from flask_login import current_user
    from flask.signals import got_request_exception
    from werkzeug.exceptions import HTTPException

    def _on_request_exception(sender, exception, **extra):
        """Error monitoring: observe every exception Flask handles during a
        request and log the genuinely unexpected ones (see
        app.error_monitoring). This is a signal receiver, not an error
        handler — it never touches or replaces the response, so normal
        exception handling (404/403/etc. rendering, and Flask's own 500
        page for real bugs) proceeds exactly as it would with no listener
        at all. HTTPExceptions (404, 403, abort(...) calls, validation
        errors, etc.) are ordinary control flow, not bugs, so they're
        skipped here — only real, unanticipated exceptions get logged."""
        if isinstance(exception, HTTPException):
            return
        from app.error_monitoring import log_error

        log_error(exception)

    got_request_exception.connect(_on_request_exception, app, weak=False)

    from app.branding import current_org_branding

    @app.context_processor
    def _inject_impersonation_flag():
        """True ONLY for a super_admin mid-support-view. Templates must use
        this rather than testing the raw session key: that key can linger
        in the browser's session after a super_admin logs out, and a plain
        `session.get(...)` check then wrongly put a real examiner/admin who
        logs in next in "read-only support view" (no Edit/Assign/etc.)."""
        from app.utils import is_impersonating

        return {"is_impersonating_org": is_impersonating()}

    @app.context_processor
    def _inject_org_branding():
        return {"org_branding": current_org_branding()}

    from app import sso as sso_module

    @app.context_processor
    def _inject_sso_flags():
        return {"google_sso_enabled": sso_module.google_enabled(), "microsoft_sso_enabled": sso_module.microsoft_enabled()}

    @app.context_processor
    def _inject_staff_counts():
        """Glanceable counts for the staff sidebar: exams in progress right
        now, finished attempts flagged but not yet reviewed, and appeals
        awaiting a decision. Three cheap COUNTs per staff page load. Strictly
        best-effort — a failure here must never take a page down, so it falls
        back to "no counts" (the sidebar simply shows none)."""
        from flask_login import current_user as cu

        try:
            if not cu.is_authenticated or cu.role not in ("admin", "examiner", "proctor", "super_admin"):
                return {"staff_counts": None}
            from app.utils import current_org_id
            from app.models import Attempt, Test, Appeal, AttemptReview

            org_id = current_org_id()
            if org_id is None:
                return {"staff_counts": None}
            live = (
                Attempt.query.join(Test, Attempt.test_id == Test.id)
                .filter(Test.org_id == org_id, Attempt.status == "in_progress").count()
            )
            unreviewed = (
                Attempt.query.join(Test, Attempt.test_id == Test.id)
                .outerjoin(AttemptReview, AttemptReview.attempt_id == Attempt.id)
                .filter(
                    Test.org_id == org_id, AttemptReview.id.is_(None), Attempt.status != "in_progress",
                    db.or_(Attempt.status == "terminated", Attempt.violation_count > 0),
                ).count()
            )
            appeals = (
                Appeal.query.join(Attempt, Appeal.attempt_id == Attempt.id)
                .join(Test, Attempt.test_id == Test.id)
                .filter(Test.org_id == org_id, Appeal.status == "pending").count()
            )
            return {"staff_counts": {"live": live, "unreviewed": unreviewed, "appeals": appeals}}
        except Exception:
            db.session.rollback()
            return {"staff_counts": None}

    @app.route("/branding/logo/<int:org_id>")
    def branding_logo(org_id):
        from flask import send_from_directory, abort as flask_abort
        from app.models import Organization

        org = Organization.query.get_or_404(org_id)
        if not org.logo_filename:
            flask_abort(404)
        return send_from_directory(app.config["ORG_BRANDING_DIR"], org.logo_filename)

    @app.route("/")
    def index():
        if current_user.is_authenticated:
            if current_user.role == "super_admin":
                return redirect(url_for("organizations.list_organizations"))
            if current_user.role in ("admin", "examiner"):
                return redirect(url_for("admin.dashboard"))
            if current_user.role == "proctor":
                return redirect(url_for("admin.proctor_queue"))
            return redirect(url_for("student.dashboard"))
        return redirect(url_for("auth.login"))

    from app.cli import register_cli
    register_cli(app)

    from app.scheduler import start_background_scheduler
    start_background_scheduler(app)

    return app
