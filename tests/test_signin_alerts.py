import os

from tests.conftest import register_and_verify, login


def _outbox(app):
    path = os.path.join(app.instance_path, "outbox.log")
    if not os.path.exists(path):
        return ""
    return open(path, encoding="utf-8").read()


def test_first_ever_login_sends_no_alert(client, app):
    register_and_verify(client, app, "User", "sia1@test.com", "9155566001", "student", "Studpass1!")
    before = _outbox(app)
    login(client, "sia1@test.com", "Studpass1!")
    after = _outbox(app)
    assert "New sign-in" not in after[len(before):]

    with app.app_context():
        from app.models import LoginSecurityEvent, User
        user = User.query.filter_by(email="sia1@test.com").first()
        assert LoginSecurityEvent.query.filter_by(user_id=user.id).count() == 0


def test_repeat_login_same_ip_and_device_sends_no_alert(client, app):
    register_and_verify(client, app, "User", "sia2@test.com", "9155566002", "student", "Studpass1!")
    login(client, "sia2@test.com", "Studpass1!")
    client.get("/logout")

    before = _outbox(app)
    login(client, "sia2@test.com", "Studpass1!")
    after = _outbox(app)
    assert "New sign-in" not in after[len(before):]


def test_login_from_new_ip_triggers_alert(client, app):
    register_and_verify(client, app, "User", "sia3@test.com", "9155566003", "student", "Studpass1!")
    login(client, "sia3@test.com", "Studpass1!")
    client.get("/logout")

    before = _outbox(app)
    login(client, "sia3@test.com", "Studpass1!")
    # The above uses the default test-client IP; force a different one to
    # simulate a genuinely new location for a THIRD login.
    client.get("/logout")
    with client.session_transaction():
        pass
    r = client.get("/login", environ_overrides={"REMOTE_ADDR": "203.0.113.55"})
    from tests.conftest import get_captcha_answer
    answer = get_captcha_answer(r.data.decode())
    client.post("/login", data={"email": "sia3@test.com", "password": "Studpass1!", "captcha_answer": str(answer)},
                environ_overrides={"REMOTE_ADDR": "203.0.113.55"}, follow_redirects=True)
    after = _outbox(app)
    assert "New sign-in" in after[len(before):]
    assert "new IP address" in after[len(before):] or "a new IP address" in after[len(before):]

    with app.app_context():
        from app.models import LoginSecurityEvent, User
        user = User.query.filter_by(email="sia3@test.com").first()
        ev = LoginSecurityEvent.query.filter_by(user_id=user.id, event_type="new_location").first()
        assert ev is not None


def test_login_from_new_device_triggers_alert(client, app):
    register_and_verify(client, app, "User", "sia4@test.com", "9155566004", "student", "Studpass1!")
    login(client, "sia4@test.com", "Studpass1!")
    client.get("/logout")

    before = _outbox(app)
    r = client.get("/login", headers={"User-Agent": "SomeBrandNewBrowser/1.0"})
    from tests.conftest import get_captcha_answer
    answer = get_captcha_answer(r.data.decode())
    client.post("/login", data={"email": "sia4@test.com", "password": "Studpass1!", "captcha_answer": str(answer)},
                headers={"User-Agent": "SomeBrandNewBrowser/1.0"}, follow_redirects=True)
    after = _outbox(app)
    assert "New sign-in" in after[len(before):]

    with app.app_context():
        from app.models import LoginSecurityEvent, User
        user = User.query.filter_by(email="sia4@test.com").first()
        ev = LoginSecurityEvent.query.filter_by(user_id=user.id, event_type="new_device").first()
        assert ev is not None


def test_signin_activity_page_shows_sessions_and_flags(client, app):
    register_and_verify(client, app, "User", "sia5@test.com", "9155566005", "student", "Studpass1!")
    login(client, "sia5@test.com", "Studpass1!")
    client.get("/logout")
    r = client.get("/login", headers={"User-Agent": "AnotherNewBrowser/2.0"})
    from tests.conftest import get_captcha_answer
    answer = get_captcha_answer(r.data.decode())
    client.post("/login", data={"email": "sia5@test.com", "password": "Studpass1!", "captcha_answer": str(answer)},
                headers={"User-Agent": "AnotherNewBrowser/2.0"}, follow_redirects=True)

    r2 = client.get("/profile/sign-in-activity")
    assert r2.status_code == 200
    body = r2.get_data(as_text=True)
    assert "Flagged Activity" in body
    assert "New device" in body
    assert "Recent Sessions" in body
    assert "AnotherNewBrowser/2.0" in body
