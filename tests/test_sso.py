from tests.conftest import register_and_verify, login


def test_login_page_hides_sso_buttons_when_not_configured(client, app):
    r = client.get("/login")
    assert b"Sign in with Google" not in r.data
    assert b"Sign in with Microsoft" not in r.data


def test_login_page_shows_configured_provider_only(client, app):
    app.config["GOOGLE_CLIENT_ID"] = "fake-id"
    app.config["GOOGLE_CLIENT_SECRET"] = "fake-secret"
    try:
        r = client.get("/login")
        assert b"Sign in with Google" in r.data
        assert b"Sign in with Microsoft" not in r.data
    finally:
        app.config["GOOGLE_CLIENT_ID"] = None
        app.config["GOOGLE_CLIENT_SECRET"] = None


def test_sso_links_to_existing_account_by_email(client, app):
    register_and_verify(client, app, "Existing User", "existing@test.com", "9144400001", "student", "Studpass1!")
    with app.app_context():
        from app.sso import find_or_create_sso_user
        from app.models import User

        user, error = find_or_create_sso_user("google", "google-sub-123", "existing@test.com", "Existing User")
        assert error is None
        assert user.email == "existing@test.com"
        assert user.sso_provider == "google"
        assert user.sso_subject == "google-sub-123"

        # The account is unchanged otherwise — still logs in with the
        # original password too.
        refreshed = User.query.filter_by(email="existing@test.com").first()
        assert refreshed.check_password("Studpass1!")


def test_sso_repeat_login_finds_same_linked_account(client, app):
    register_and_verify(client, app, "Domain Admin2", "domainadmin2@test.com", "9144400003", "admin", "Adminpass1!")
    login(client, "domainadmin2@test.com", "Adminpass1!")
    client.post("/admin/access-control", data={"ip_ranges": "", "sso_domain": "repeatdomain.edu"})

    with app.app_context():
        from app.sso import find_or_create_sso_user

        user1, err1 = find_or_create_sso_user("google", "google-sub-999", "repeat@repeatdomain.edu", "Repeat User")
        assert err1 is None
        user2, error = find_or_create_sso_user("google", "google-sub-999", "repeat@repeatdomain.edu", "Repeat User")
        assert error is None
        assert user1.id == user2.id


def test_sso_self_service_creates_account_for_claimed_domain(client, app):
    register_and_verify(client, app, "Domain Admin", "domainadmin@test.com", "9144400002", "admin", "Adminpass1!")
    login(client, "domainadmin@test.com", "Adminpass1!")
    client.post("/admin/access-control", data={"ip_ranges": "", "sso_domain": "claimeddomain.edu"})

    with app.app_context():
        from app.sso import find_or_create_sso_user
        from app.models import User, Organization

        user, error = find_or_create_sso_user("microsoft", "ms-sub-1", "newstudent@claimeddomain.edu", "New Student")
        assert error is None
        assert user is not None
        assert user.role == "student"
        assert user.email_verified is True

        admin_org = User.query.filter_by(email="domainadmin@test.com").first().org_id
        assert user.org_id == admin_org


def test_sso_rejects_unclaimed_domain_with_no_existing_account(client, app):
    with app.app_context():
        from app.sso import find_or_create_sso_user

        user, error = find_or_create_sso_user("google", "google-sub-777", "nobody@unclaimed-domain.example", "Nobody")
        assert user is None
        assert error is not None
        assert "unclaimed-domain.example" in error


def test_sso_rejects_missing_email(client, app):
    with app.app_context():
        from app.sso import find_or_create_sso_user

        user, error = find_or_create_sso_user("google", "google-sub-000", "", "No Email")
        assert user is None
        assert "verified email" in error
