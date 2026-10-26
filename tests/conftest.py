from datetime import datetime
import os
import re
import shutil

import pytest

from app import create_app


class TestConfig:
    TESTING = True
    # The real app runs exam-reminder sweeps on a background thread (see
    # app.scheduler) — off here so hundreds of short-lived test apps don't
    # each spin one up against a database that's gone by the time it fires.
    ENABLE_BACKGROUND_SCHEDULER = False
    SECRET_KEY = "test-secret-key"
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    WTF_CSRF_ENABLED = False
    MAX_VIOLATIONS_BEFORE_TERMINATION = 5
    MAX_WARNINGS_BEFORE_TERMINATION = 2
    FACE_MATCH_THRESHOLD = 0.6
    MAIL_SERVER = None
    MAIL_PORT = 587
    MAIL_USE_TLS = True
    MAIL_USERNAME = None
    MAIL_PASSWORD = None
    MAIL_SENDER = "no-reply@test.local"
    MAX_CONTENT_LENGTH = 25 * 1024 * 1024

    # Disabled by default so fixtures that register/verify many accounts in a
    # single test (which all share the test client's fixed IP) aren't
    # throttled. Rate-limiting tests turn this back on for just that test.
    RATE_LIMIT_ENABLED = False
    REGISTER_MAX_PER_IP = 5
    REGISTER_WINDOW_MINUTES = 60
    FORGOT_PASSWORD_MAX_PER_IP = 5
    FORGOT_PASSWORD_WINDOW_MINUTES = 15
    RESEND_VERIFICATION_MAX_PER_IP = 5
    RESEND_VERIFICATION_WINDOW_MINUTES = 15

    PASSWORD_MIN_LENGTH = 8

    IDENTITY_SPOTCHECK_MIN_SECONDS = 180
    IDENTITY_SPOTCHECK_MAX_SECONDS = 420
    ID_DOCUMENT_MAX_BYTES = 8 * 1024 * 1024
    ID_DOCUMENT_ALLOWED_EXTS = {"png", "jpg", "jpeg", "webp"}
    EXAM_REMINDER_WINDOW_MINUTES = 60

    _base = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "instance")
    SNAPSHOT_UPLOAD_DIR = os.path.join(_base, "snapshots")
    RECORDINGS_DIR = os.path.join(_base, "recordings")
    ID_DOCUMENT_UPLOAD_DIR = os.path.join(_base, "id_documents")


@pytest.fixture()
def app():
    application = create_app(config_object=TestConfig())
    # The app no longer creates tables itself (migrations own the schema).
    # Tests build the schema straight from the models for speed; a separate
    # test (test_migrations.py) proves the migrations produce the same schema.
    from app import db as _db
    with application.app_context():
        _db.create_all()

    # Each test gets an in-memory DB, but the outbox log lives on disk under
    # instance/ — reset it so tests don't see other tests' emails.
    outbox_path = os.path.join(application.instance_path, "outbox.log")
    if os.path.exists(outbox_path):
        os.remove(outbox_path)

    yield application

    shutil.rmtree(application.config["SNAPSHOT_UPLOAD_DIR"], ignore_errors=True)
    shutil.rmtree(application.config["RECORDINGS_DIR"], ignore_errors=True)
    shutil.rmtree(application.config["ID_DOCUMENT_UPLOAD_DIR"], ignore_errors=True)
    if os.path.exists(outbox_path):
        os.remove(outbox_path)


@pytest.fixture()
def client(app):
    return app.test_client()


def get_captcha_answer(html):
    """Extract the answer to the login page's math CAPTCHA from its rendered question."""
    m = re.search(r"Quick check: (\d+) ([+-]) (\d+) = \?", html)
    if not m:
        return None
    a, op, b = int(m.group(1)), m.group(2), int(m.group(3))
    return a + b if op == "+" else a - b


def get_outbox(app):
    outbox_path = os.path.join(app.instance_path, "outbox.log")
    if not os.path.exists(outbox_path):
        return ""
    with open(outbox_path) as f:
        return f.read()


def get_last_otp(app):
    """The most recent 6-digit code written to the dev outbox log."""
    codes = re.findall(r"one-time code to [^\n]*is:\s+(\d{6})", get_outbox(app))
    return codes[-1] if codes else None


def create_staff(app, name, email, phone, role, password, org_id=None, org_name=None):
    """Create an admin/examiner/proctor account the way the app now allows it —
    NOT through public registration (which is students-only) but directly, as
    the invitation flow or CLI would. Defaults to the shared Default
    Organization, which is where public registration used to put everyone."""
    from app import db
    from app.auth import _get_or_create_default_org, _unique_slug
    from app.models import Organization, User, gen_user_id

    with app.app_context():
        if role == "super_admin":
            org_id = None
        elif org_id is None and org_name:
            org = Organization.query.filter_by(name=org_name).first()
            if not org:
                org = Organization(name=org_name, slug=_unique_slug(org_name), status="active")
                db.session.add(org)
                db.session.flush()
            org_id = org.id
        elif org_id is None:
            org_id = _get_or_create_default_org().id
        user = User(user_id=gen_user_id(role), name=name, email=email.lower(), phone=phone, role=role,
                    status="active", email_verified=True, org_id=org_id,
                    terms_accepted_at=datetime.utcnow(), terms_version_accepted=app.config["TERMS_VERSION"])
        user.set_password(password)
        db.session.add(user)
        db.session.commit()
        return user.id


def register_and_verify(client, app, name, email, phone, role, password, org_name=None):
    """Students self-register + verify by emailed code (the real public flow).
    Any other role is created directly (see create_staff) because privileged
    accounts can no longer be self-registered."""
    if role != "student":
        create_staff(app, name, email, phone, role, password, org_name=org_name)
        return
    client.post("/register", data=dict(name=name, email=email, phone=phone, password=password))
    code = get_last_otp(app)
    client.post("/verify-email", data={"email": email, "code": code})


def login(client, email, password):
    r = client.get("/login")
    answer = get_captcha_answer(r.data.decode())
    return client.post(
        "/login",
        data={"email": email, "password": password, "captcha_answer": str(answer)},
        follow_redirects=True,
    )


def add_single_question(client, test_id, question_text, option_a, option_b, option_c, option_d, correct_letter, marks=1, time_limit_seconds=None):
    """POST a single-choice question the way the real form does — via the
    correct_radio field, not a plain correct_answer field."""
    data = {
        "question_type": "single", "question_text": question_text,
        "option_a": option_a, "option_b": option_b, "option_c": option_c, "option_d": option_d,
        "correct_radio": correct_letter, "marks": marks,
    }
    if time_limit_seconds is not None:
        data["time_limit_seconds"] = time_limit_seconds
    return client.post(f"/admin/tests/{test_id}/questions/add", data=data)


def add_multi_question(client, test_id, question_text, option_a, option_b, option_c, option_d, correct_letters, marks=1):
    """POST a multiple-choice question via the correct_options checkbox list."""
    from werkzeug.datastructures import MultiDict

    data = MultiDict([
        ("question_type", "multi"), ("question_text", question_text),
        ("option_a", option_a), ("option_b", option_b), ("option_c", option_c), ("option_d", option_d),
        ("marks", str(marks)),
    ])
    for letter in correct_letters:
        data.add("correct_options", letter)
    return client.post(f"/admin/tests/{test_id}/questions/add", data=data)


def add_short_question(client, test_id, question_text, answer_text, marks=1):
    """POST a short-answer question via the short_answer_text field."""
    return client.post(
        f"/admin/tests/{test_id}/questions/add",
        data={
            "question_type": "short", "question_text": question_text,
            "short_answer_text": answer_text, "marks": marks,
        },
    )
