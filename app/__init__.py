import os
import time
import json
from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager

db = SQLAlchemy()
login_manager = LoginManager()
login_manager.login_view = "auth.login"
login_manager.login_message = "Please log in to continue."
login_manager.login_message_category = "warning"


def _literal_default_sql(column, dialect):
    """A column's Python-side `default=` as a literal SQL value, or None if
    it isn't one (a callable like `datetime.utcnow` or `lambda: gen_user_id(...)`
    can't be expressed as a static SQL DEFAULT — there's no way to ask
    SQLite to run Python at insert time). Literals (str/int/float/bool) are
    the common case and cover most columns in this app; the earlier,
    narrower version of this function hand-wrote exactly one of these
    ("INTEGER NOT NULL DEFAULT 0") per column, which is exactly the kind of
    thing this generic version exists to stop anyone needing to remember."""
    if column.default is None or getattr(column.default, "is_callable", False):
        return None
    value = getattr(column.default, "arg", None)
    if callable(value):
        return None
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        return "'" + value.replace("'", "''") + "'"
    return None


def _ensure_columns(engine):
    """Reconcile every model's columns against the live database and add
    whatever's missing. db.create_all() only creates whole tables that are
    missing — on an already-deployed database it silently leaves an
    existing table's columns untouched, so any column a model has gained
    since that database was first created turns into a hard crash ("no
    such column: users.totp_secret", say) the instant any query touches
    it, regardless of how long ago that column was added or how many
    columns have piled up since.

    This used to be a short hand-maintained list of (table, column, DDL
    type) tuples — safe, but only as complete as whoever last remembered
    to add an entry, and a real deployment hit exactly that gap: several
    `users` columns (2FA, SSO, notification preferences) predated the list
    entirely and were never in it. This version instead walks every table
    SQLAlchemy's models define (db.metadata, not a maintained list),
    compares each one against what the live database actually has, and
    ALTERs in anything the models have that the table doesn't — so
    upgrading never again depends on someone remembering to list a column.
    """
    from sqlalchemy import inspect, text

    inspector = inspect(engine)
    try:
        existing_tables = set(inspector.get_table_names())
    except Exception:
        return

    with engine.connect() as conn:
        for table in db.metadata.sorted_tables:
            if table.name not in existing_tables:
                continue  # a fresh install: create_all() already made this table in full
            try:
                existing_cols = {c["name"] for c in inspector.get_columns(table.name)}
            except Exception:
                continue

            for column in table.columns:
                if column.name in existing_cols:
                    continue
                try:
                    ddl_type = column.type.compile(dialect=engine.dialect)
                except Exception:
                    continue  # an exotic/custom type we can't safely stringify — skip rather than guess

                clause = f"ALTER TABLE {table.name} ADD COLUMN {column.name} {ddl_type}"
                default_sql = _literal_default_sql(column, engine.dialect)
                if default_sql is not None:
                    clause += f" DEFAULT {default_sql}"
                    if not column.nullable:
                        clause += " NOT NULL"
                # else: a callable default (datetime.utcnow, gen_user_id, ...)
                # or no default at all — added as a plain nullable column
                # rather than forcing NOT NULL with nothing to backfill
                # existing rows with, which SQLite would simply refuse.

                try:
                    conn.execute(text(clause))
                    conn.commit()
                except Exception:
                    # Best-effort: an unsupported dialect quirk here
                    # shouldn't prevent the app from starting. Worst case,
                    # this specific column behaves as if unset until an
                    # admin applies the ALTER TABLE by hand.
                    pass


def create_app(config_object="config.Config"):
    app = Flask(__name__, instance_relative_config=True)
    app.config.from_object(config_object)
    app.config["APP_STARTED_AT"] = time.time()

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
    login_manager.init_app(app)

    from app.models import User

    @login_manager.user_loader
    def load_user(user_id):
        return db.session.get(User, int(user_id))

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

    with app.app_context():
        db.create_all()
        _ensure_columns(db.engine)

    from app.cli import register_cli
    register_cli(app)

    from app.scheduler import start_background_scheduler
    start_background_scheduler(app)

    return app
