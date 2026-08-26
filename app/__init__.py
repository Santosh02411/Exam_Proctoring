import os
from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager
from sqlalchemy.exc import OperationalError

db = SQLAlchemy()
login_manager = LoginManager()
login_manager.login_view = "auth.login"
login_manager.login_message = "Please log in to continue."
login_manager.login_message_category = "warning"


def create_app(config_object="config.Config"):
    app = Flask(__name__, instance_relative_config=True)
    app.config.from_object(config_object)

    os.makedirs(app.instance_path, exist_ok=True)
    os.makedirs(app.config["SNAPSHOT_UPLOAD_DIR"], exist_ok=True)
    os.makedirs(app.config["RECORDINGS_DIR"], exist_ok=True)

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

    app.register_blueprint(auth_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(student_bp)
    app.register_blueprint(proctoring_bp)

    from flask import redirect, url_for
    from flask_login import current_user

    @app.route("/")
    def index():
        if current_user.is_authenticated:
            if current_user.role == "admin":
                return redirect(url_for("admin.dashboard"))
            return redirect(url_for("student.dashboard"))
        return redirect(url_for("auth.login"))

    @app.route("/health")
    def health():
        """Liveness/readiness probe for load balancers and Docker's HEALTHCHECK.
        Confirms the app can actually reach its database, not just that the
        process is up — a DB outage should fail the check."""
        from sqlalchemy import text

        try:
            db.session.execute(text("SELECT 1"))
            return {"status": "ok"}, 200
        except Exception as exc:
            return {"status": "error", "detail": str(exc)}, 503

    @app.after_request
    def set_security_headers(response):
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        # Proctoring pages need camera/microphone; leave those permissions alone
        # elsewhere but explicitly deny the risky ones this app never needs.
        response.headers.setdefault("Permissions-Policy", "geolocation=(), payment=()")
        return response

    with app.app_context():
        try:
            db.create_all()
        except OperationalError:
            # Under multiple concurrent worker processes (e.g. Gunicorn with
            # more than one worker), each one independently calls create_app()
            # at boot, and two can race to create the schema at the same
            # moment. SQLite doesn't handle concurrent DDL well, so the loser
            # gets "table already exists" — harmless, since the schema is
            # already there by the time it fails. Roll back and continue
            # rather than crash the worker.
            db.session.rollback()

    from app.cli import register_cli
    register_cli(app)

    return app
