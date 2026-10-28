import re

from tests.conftest import register_and_verify, login, get_outbox


# --- Password complexity -----------------------------------------------

def test_registration_rejects_password_missing_uppercase(client, app):
    r = client.post(
        "/register",
        data=dict(name="Weak", email="weak_upper@test.com", phone="9000001020",
                   role="student", password="lowercase1!"),
    )
    assert b"uppercase" in r.data.lower()
    with app.app_context():
        from app.models import User
        assert User.query.filter_by(email="weak_upper@test.com").first() is None


def test_registration_rejects_password_missing_special_char(client, app):
    r = client.post(
        "/register",
        data=dict(name="Weak", email="weak_special@test.com", phone="9000001021",
                   role="student", password="Lowercase1"),
    )
    assert b"special character" in r.data.lower()
    with app.app_context():
        from app.models import User
        assert User.query.filter_by(email="weak_special@test.com").first() is None


def test_registration_rejects_password_missing_digit(client, app):
    r = client.post(
        "/register",
        data=dict(name="Weak", email="weak_digit@test.com", phone="9000001022",
                   role="student", password="Lowercase!"),
    )
    assert b"a number" in r.data.lower()


def test_registration_rejects_short_password(client, app):
    r = client.post(
        "/register",
        data=dict(name="Weak", email="weak_short@test.com", phone="9000001023",
                   role="student", password="Ab1!"),
    )
    assert b"8 characters" in r.data.lower()
    with app.app_context():
        from app.models import User
        assert User.query.filter_by(email="weak_short@test.com").first() is None


def test_registration_accepts_strong_password(client, app):
    r = client.post(
        "/register",
        data=dict(name="Strong", email="strong1@test.com", phone="9000001024",
                   role="student", password="Strongpass1!"),
        follow_redirects=True,
    )
    assert b"Registration successful" in r.data
    with app.app_context():
        from app.models import User
        assert User.query.filter_by(email="strong1@test.com").first() is not None


def test_reset_password_rejects_weak_new_password(client, app):
    register_and_verify(client, app, "Iris", "iris_rl@test.com", "9000001030", "student", "Password1!")
    client.post("/forgot-password", data={"email": "iris_rl@test.com"})
    outbox = get_outbox(app)
    token = re.findall(r"/reset-password/([^\s]+)", outbox)[-1]

    r = client.post(f"/reset-password/{token}", data={"password": "weakpass", "confirm_password": "weakpass"})
    assert b"must also include" in r.data.lower()

    # Old password should still work — the weak reset never took effect.
    r = login(client, "iris_rl@test.com", "Password1!")
    assert b"My Tests" in r.data


# --- Per-IP rate limiting -------------------------------------------------
# RATE_LIMIT_ENABLED defaults to False in TestConfig so fixtures that
# register many accounts in one test (all sharing the test client's fixed
# IP) aren't throttled. These tests turn it back on with tight limits.

def test_registration_blocked_after_ip_limit(client, app):
    app.config["RATE_LIMIT_ENABLED"] = True
    app.config["REGISTER_MAX_PER_IP"] = 2
    app.config["REGISTER_WINDOW_MINUTES"] = 60

    for i in range(2):
        r = client.post(
            "/register",
            data=dict(name=f"User{i}", email=f"rl{i}@test.com", phone=f"900000110{i}",
                      role="student", password="Password1!"),
            follow_redirects=True,
        )
        assert b"Registration successful" in r.data

    r = client.post(
        "/register",
        data=dict(name="Blocked", email="rlblocked@test.com", phone="9000001199",
                  role="student", password="Password1!"),
        follow_redirects=True,
    )
    assert b"Too many registration attempts" in r.data
    with app.app_context():
        from app.models import User
        assert User.query.filter_by(email="rlblocked@test.com").first() is None


def test_registration_ip_limit_counts_invalid_attempts_too(client, app):
    """A scripted attacker submitting garbage shouldn't dodge the limit —
    every POST counts, valid or not."""
    app.config["RATE_LIMIT_ENABLED"] = True
    app.config["REGISTER_MAX_PER_IP"] = 1
    app.config["REGISTER_WINDOW_MINUTES"] = 60

    # First attempt is deliberately invalid (bad phone) but still consumes the quota.
    client.post(
        "/register",
        data=dict(name="Bad", email="badphone@test.com", phone="123", role="student", password="Password1!"),
    )
    r = client.post(
        "/register",
        data=dict(name="Second", email="second@test.com", phone="9000001101",
                  role="student", password="Password1!"),
        follow_redirects=True,
    )
    assert b"Too many registration attempts" in r.data


def test_forgot_password_blocked_after_ip_limit(client, app):
    register_and_verify(client, app, "Hank", "hank_rl@test.com", "9000001010", "student", "Password1!")
    app.config["RATE_LIMIT_ENABLED"] = True
    app.config["FORGOT_PASSWORD_MAX_PER_IP"] = 2
    app.config["FORGOT_PASSWORD_WINDOW_MINUTES"] = 60

    for _ in range(2):
        r = client.post("/forgot-password", data={"email": "hank_rl@test.com"}, follow_redirects=True)
        assert b"reset link has been sent" in r.data

    r = client.post("/forgot-password", data={"email": "hank_rl@test.com"}, follow_redirects=True)
    assert b"Too many requests" in r.data

    outbox = get_outbox(app)
    tokens = re.findall(r"/reset-password/([^\s]+)", outbox)
    assert len(tokens) == 2


def test_resend_verification_blocked_after_ip_limit(client, app):
    client.post(
        "/register",
        data=dict(name="Jill", email="jill_rl@test.com", phone="9000001040", role="student", password="Password1!"),
    )
    app.config["RATE_LIMIT_ENABLED"] = True
    app.config["RESEND_VERIFICATION_MAX_PER_IP"] = 1
    app.config["RESEND_VERIFICATION_WINDOW_MINUTES"] = 60

    r = client.post("/resend-verification", data={"email": "jill_rl@test.com"}, follow_redirects=True)
    assert b"Verification email sent" in r.data

    r = client.post("/resend-verification", data={"email": "jill_rl@test.com"}, follow_redirects=True)
    assert b"Too many requests" in r.data


def test_rate_limit_disabled_flag_bypasses_throttling(client, app):
    app.config["RATE_LIMIT_ENABLED"] = False
    app.config["REGISTER_MAX_PER_IP"] = 1
    app.config["REGISTER_WINDOW_MINUTES"] = 60

    for i in range(3):
        r = client.post(
            "/register",
            data=dict(name=f"NoLimit{i}", email=f"nolimit{i}@test.com", phone=f"900000120{i}",
                      role="student", password="Password1!"),
            follow_redirects=True,
        )
        assert b"Registration successful" in r.data
