import re

from tests.conftest import register_and_verify, login, get_outbox, get_captcha_answer, get_last_otp


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


def test_forgot_and_reset_password_with_otp(client, app):
    register_and_verify(client, app, "Faye", "faye@test.com", "9000000006", "student", "Oldpass1!")

    r = client.post("/forgot-password", data={"email": "faye@test.com"}, follow_redirects=True)
    assert b"6-digit" in r.data
    assert "/reset-password/" not in get_outbox(app), "no reset link should be emailed any more"
    code = get_last_otp(app)
    assert code and len(code) == 6

    # Password form is NOT available until the code is verified.
    r = client.get("/reset-password")
    assert b"New Password" not in r.data

    r = client.post("/reset-password", data={"code": code}, follow_redirects=True)
    assert b"Set a new password" in r.data

    r = client.post(
        "/reset-password",
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
    client.post("/reset-password", data={"code": get_last_otp(app)})

    r = client.post("/reset-password", data={"password": "abcdefg", "confirm_password": "xyz1234"})
    assert b"do not match" in r.data.lower()


def test_reset_wrong_otp_rejected_and_locks_after_max_attempts(client, app):
    register_and_verify(client, app, "Hal", "hal@test.com", "9000000008", "student", "Oldpass1!")
    client.post("/forgot-password", data={"email": "hal@test.com"})
    real = get_last_otp(app)
    wrong = "000000" if real != "000000" else "111111"

    for _ in range(5):
        r = client.post("/reset-password", data={"code": wrong})
    assert b"too many incorrect attempts" in r.data.lower()

    # Even the correct code is dead now — a new one is required.
    r = client.post("/reset-password", data={"code": real}, follow_redirects=True)
    assert b"Set a new password" not in r.data


def test_reset_otp_is_single_use_and_cannot_be_replayed(client, app):
    register_and_verify(client, app, "Ida", "ida@test.com", "9000000009", "student", "Oldpass1!")
    client.post("/forgot-password", data={"email": "ida@test.com"})
    code = get_last_otp(app)
    client.post("/reset-password", data={"code": code})
    client.post("/reset-password", data={"password": "Newpass1!", "confirm_password": "Newpass1!"})

    # Same browser session, same code again: nothing left to redeem.
    client.post("/forgot-password", data={"email": "ida@test.com"})
    r = client.post("/reset-password", data={"code": code}, follow_redirects=True)
    assert b"Set a new password" not in r.data


def test_reset_otp_expires(client, app):
    from datetime import datetime, timedelta
    register_and_verify(client, app, "Jo", "jo@test.com", "9000000010", "student", "Oldpass1!")
    client.post("/forgot-password", data={"email": "jo@test.com"})
    code = get_last_otp(app)
    with app.app_context():
        from app import db
        from app.models import EmailOtp
        for row in EmailOtp.query.filter_by(email="jo@test.com", purpose="password_reset").all():
            row.expires_at = datetime.utcnow() - timedelta(minutes=1)
        db.session.commit()
    r = client.post("/reset-password", data={"code": code})
    assert b"expired" in r.data.lower()


def test_forgot_password_does_not_reveal_unknown_email(client, app):
    r = client.post("/forgot-password", data={"email": "nobody@test.com"}, follow_redirects=True)
    assert b"If that email is registered" in r.data
    assert get_last_otp(app) is None


def test_email_verification_requires_correct_otp(client, app):
    client.post(
        "/register",
        data=dict(name="Kim", email="kim@test.com", phone="9000000011", role="student", password="Password1!"),
    )
    assert "/verify-email/" not in get_outbox(app), "no verification link should be emailed any more"
    code = get_last_otp(app)
    wrong = "000000" if code != "000000" else "111111"

    r = client.post("/verify-email", data={"code": wrong})
    assert b"incorrect code" in r.data.lower()
    with app.app_context():
        from app.models import User
        assert User.query.filter_by(email="kim@test.com").first().email_verified is False

    r = client.post("/verify-email", data={"code": code}, follow_redirects=True)
    assert b"Email verified" in r.data
    with app.app_context():
        from app.models import User
        assert User.query.filter_by(email="kim@test.com").first().email_verified is True


def test_resend_verification_sends_new_code_and_invalidates_old(client, app):
    client.post(
        "/register",
        data=dict(name="Lee", email="lee@test.com", phone="9000000012", role="student", password="Password1!"),
    )
    old = get_last_otp(app)
    app.config["OTP_RESEND_COOLDOWN_SECONDS"] = 0
    client.post("/resend-verification", data={"email": "lee@test.com"})
    new = get_last_otp(app)
    if new != old:  # (1-in-a-million collision otherwise)
        r = client.post("/verify-email", data={"code": old})
        assert b"incorrect code" in r.data.lower() or b"no active code" in r.data.lower()
    r = client.post("/verify-email", data={"code": new}, follow_redirects=True)
    assert b"Email verified" in r.data
