import os
import re
import shutil

import pytest

from app import create_app


class TestConfig:
    SECRET_KEY = "test-secret-key"
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    WTF_CSRF_ENABLED = False
    MAX_VIOLATIONS_BEFORE_TERMINATION = 5
    FACE_MATCH_THRESHOLD = 0.6
    MAIL_SERVER = None
    MAIL_PORT = 587
    MAIL_USE_TLS = True
    MAIL_USERNAME = None
    MAIL_PASSWORD = None
    MAIL_SENDER = "no-reply@test.local"
    MAX_CONTENT_LENGTH = 25 * 1024 * 1024

    _base = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "instance")
    SNAPSHOT_UPLOAD_DIR = os.path.join(_base, "snapshots")
    RECORDINGS_DIR = os.path.join(_base, "recordings")


@pytest.fixture()
def app():
    application = create_app(config_object=TestConfig())

    # Each test gets an in-memory DB, but the outbox log lives on disk under
    # instance/ — reset it so tests don't see other tests' emails.
    outbox_path = os.path.join(application.instance_path, "outbox.log")
    if os.path.exists(outbox_path):
        os.remove(outbox_path)

    yield application

    shutil.rmtree(application.config["SNAPSHOT_UPLOAD_DIR"], ignore_errors=True)
    shutil.rmtree(application.config["RECORDINGS_DIR"], ignore_errors=True)
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


def register_and_verify(client, app, name, email, phone, role, password):
    client.post("/register", data=dict(name=name, email=email, phone=phone, role=role, password=password))
    content = get_outbox(app)
    tokens = re.findall(r"/verify-email/([^\s]+)", content)
    token = tokens[-1]
    client.get(f"/verify-email/{token}")


def login(client, email, password):
    r = client.get("/login")
    answer = get_captcha_answer(r.data.decode())
    return client.post(
        "/login",
        data={"email": email, "password": password, "captcha_answer": str(answer)},
        follow_redirects=True,
    )


def add_single_question(client, test_id, question_text, option_a, option_b, option_c, option_d, correct_letter, marks=1):
    """POST a single-choice question the way the real form does — via the
    correct_radio field, not a plain correct_answer field."""
    return client.post(
        f"/admin/tests/{test_id}/questions/add",
        data={
            "question_type": "single", "question_text": question_text,
            "option_a": option_a, "option_b": option_b, "option_c": option_c, "option_d": option_d,
            "correct_radio": correct_letter, "marks": marks,
        },
    )


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
