import re

from tests.conftest import register_and_verify, login, get_outbox, get_captcha_answer


def test_register_creates_unverified_user(client, app):
    r = client.post(
        "/register",
        data=dict(name="Alice", email="alice@test.com", phone="9000000001", role="student", password="Password1!"),
        follow_redirects=True,
    )
    assert r.status_code == 200

    with app.app_context():
        from app.models import User
        user = User.query.filter_by(email="alice@test.com").first()
        assert user is not None
        assert user.email_verified is False


def test_login_blocked_before_verification(client, app):
    client.post(
        "/register",
        data=dict(name="Bob", email="bob@test.com", phone="9000000002", role="student", password="Password1!"),
    )
    r = client.get("/login")
    answer = get_captcha_answer(r.data.decode())
    r = client.post("/login", data={"email": "bob@test.com", "password": "Password1!", "captcha_answer": str(answer)})
    assert b"My Tests" not in r.data
    assert b"verify your email" in r.data.lower()


def test_verification_link_unlocks_login(client, app):
    register_and_verify(client, app, "Carl", "carl@test.com", "9000000003", "student", "Password1!")
    r = login(client, "carl@test.com", "Password1!")
    assert b"My Tests" in r.data


def test_wrong_captcha_rejected(client, app):
    register_and_verify(client, app, "Dana", "dana@test.com", "9000000004", "student", "Password1!")
    r = client.post(
        "/login",
        data={"email": "dana@test.com", "password": "Password1!", "captcha_answer": "999999"},
    )
    assert b"verification answer" in r.data.lower()


def test_login_lockout_after_repeated_failures(client, app):
    register_and_verify(client, app, "Erin", "erin@test.com", "9000000005", "student", "Password1!")

    for _ in range(5):
        r = client.get("/login")
        answer = get_captcha_answer(r.data.decode())
        r = client.post(
            "/login",
            data={"email": "erin@test.com", "password": "wrongpass", "captcha_answer": str(answer)},
        )

    assert b"locked" in r.data.lower()

    with app.app_context():
        from app.models import User
        user = User.query.filter_by(email="erin@test.com").first()
        assert user.locked_until is not None

    # Correct password should still be rejected while locked.
    r = client.get("/login")
    answer = get_captcha_answer(r.data.decode())
    r = client.post(
        "/login",
        data={"email": "erin@test.com", "password": "Password1!", "captcha_answer": str(answer)},
    )
    assert b"too many failed attempts" in r.data.lower()


def test_forgot_and_reset_password(client, app):
    register_and_verify(client, app, "Faye", "faye@test.com", "9000000006", "student", "Oldpass1!")

    client.post("/forgot-password", data={"email": "faye@test.com"})
    outbox = get_outbox(app)
    tokens = re.findall(r"/reset-password/([^\s]+)", outbox)
    assert tokens, "reset link should be logged when no SMTP is configured"

    token = tokens[-1]
    r = client.post(
        f"/reset-password/{token}",
        data={"password": "Newpass1!", "confirm_password": "Newpass1!"},
        follow_redirects=True,
    )
    assert r.status_code == 200

    # Old password fails, new password works.
    r = client.get("/login")
    answer = get_captcha_answer(r.data.decode())
    r = client.post("/login", data={"email": "faye@test.com", "password": "Oldpass1!", "captcha_answer": str(answer)})
    assert b"My Tests" not in r.data

    r = login(client, "faye@test.com", "Newpass1!")
    assert b"My Tests" in r.data


def test_reset_password_mismatch_rejected(client, app):
    register_and_verify(client, app, "Gwen", "gwen@test.com", "9000000007", "student", "Oldpass1!")
    client.post("/forgot-password", data={"email": "gwen@test.com"})
    outbox = get_outbox(app)
    token = re.findall(r"/reset-password/([^\s]+)", outbox)[-1]

    r = client.post(f"/reset-password/{token}", data={"password": "abcdefg", "confirm_password": "xyz1234"})
    assert b"do not match" in r.data.lower()
