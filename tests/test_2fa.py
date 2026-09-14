import re
import pyotp

from tests.conftest import register_and_verify, login


def _extract_secret_from_setup_page(html):
    m = re.search(r'manually: <code class="mono">([A-Z2-7]+)</code>', html)
    assert m, "could not find TOTP secret in setup page HTML"
    return m.group(1)


def test_2fa_setup_and_login_flow(client, app):
    register_and_verify(client, app, "TwoFA User", "twofa@test.com", "9177777701", "student", "Studpass1!")
    login(client, "twofa@test.com", "Studpass1!")

    r = client.get("/profile/2fa/setup")
    assert r.status_code == 200
    secret = _extract_secret_from_setup_page(r.get_data(as_text=True))

    # Submit the correct current TOTP code to confirm setup.
    code = pyotp.TOTP(secret).now()
    r2 = client.post("/profile/2fa/setup", data={"code": code}, follow_redirects=True)
    assert r2.status_code == 200
    assert b"Save your backup codes" in r2.data
    backup_codes = re.findall(r'padding:8px 12px;text-align:center">([a-f0-9]+)<', r2.get_data(as_text=True))
    assert len(backup_codes) == 10

    with app.app_context():
        from app.models import User
        user = User.query.filter_by(email="twofa@test.com").first()
        assert user.totp_confirmed is True

    # Log out and log back in — this time it should require a 2FA step.
    client.get("/logout")
    r3 = login(client, "twofa@test.com", "Studpass1!")
    assert b"Two-factor verification" in r3.data or "verify-2fa" in r3.request.path

    # Wrong code should not let us in.
    r4 = client.post("/login/verify-2fa", data={"code": "000000"}, follow_redirects=True)
    assert b"Invalid or expired code" in r4.data

    # Correct live TOTP code should complete login.
    good_code = pyotp.TOTP(secret).now()
    r5 = client.post("/login/verify-2fa", data={"code": good_code}, follow_redirects=True)
    assert b"Welcome back" in r5.data

    # Confirm we're actually logged in now.
    dash = client.get("/student/dashboard")
    assert dash.status_code == 200


def test_2fa_backup_code_login_and_single_use(client, app):
    register_and_verify(client, app, "TwoFA User2", "twofa2@test.com", "9177777702", "student", "Studpass1!")
    login(client, "twofa2@test.com", "Studpass1!")

    r = client.get("/profile/2fa/setup")
    secret = _extract_secret_from_setup_page(r.get_data(as_text=True))
    code = pyotp.TOTP(secret).now()
    r2 = client.post("/profile/2fa/setup", data={"code": code}, follow_redirects=True)
    backup_codes = re.findall(r'padding:8px 12px;text-align:center">([a-f0-9]+)<', r2.get_data(as_text=True))
    one_code = backup_codes[0]

    client.get("/logout")
    login(client, "twofa2@test.com", "Studpass1!")
    r3 = client.post("/login/verify-2fa", data={"code": one_code}, follow_redirects=True)
    assert b"Welcome back" in r3.data

    # The same backup code must not work a second time.
    client.get("/logout")
    login(client, "twofa2@test.com", "Studpass1!")
    r4 = client.post("/login/verify-2fa", data={"code": one_code}, follow_redirects=True)
    assert b"Invalid or expired code" in r4.data


def test_2fa_can_be_disabled(client, app):
    register_and_verify(client, app, "TwoFA User3", "twofa3@test.com", "9177777703", "student", "Studpass1!")
    login(client, "twofa3@test.com", "Studpass1!")
    r = client.get("/profile/2fa/setup")
    secret = _extract_secret_from_setup_page(r.get_data(as_text=True))
    code = pyotp.TOTP(secret).now()
    client.post("/profile/2fa/setup", data={"code": code})

    with app.app_context():
        from app.models import User
        assert User.query.filter_by(email="twofa3@test.com").first().totp_confirmed is True

    r2 = client.post("/profile/2fa/disable", data={"password": "wrong-password"}, follow_redirects=True)
    assert b"Incorrect password" in r2.data

    r3 = client.post("/profile/2fa/disable", data={"password": "Studpass1!"}, follow_redirects=True)
    assert b"has been disabled" in r3.data

    with app.app_context():
        from app.models import User
        user = User.query.filter_by(email="twofa3@test.com").first()
        assert user.totp_confirmed is False
        assert user.totp_secret is None

    # Logging in again should NOT require a 2FA step anymore.
    client.get("/logout")
    r4 = login(client, "twofa3@test.com", "Studpass1!")
    assert b"Welcome back" in r4.data
