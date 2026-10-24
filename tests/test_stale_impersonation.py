from tests.conftest import register_and_verify, login


def _setup(client, app, role):
    register_and_verify(client, app, "Saru", "saru@test.com", "9000000020", role, "Saru-pass1!")
    login(client, "saru@test.com", "Saru-pass1!")
    client.post("/admin/tests/create", data=dict(
        test_code="CS005", title="Python", description="d", duration_minutes=10,
        total_questions=3, passing_marks=2, status="published", max_attempts=1,
        negative_marks_per_wrong=0, allow_review="y"), follow_redirects=True)


def test_stale_impersonation_key_does_not_lock_examiner_out_of_actions(client, app):
    _setup(client, app, "examiner")
    # A leftover key from an earlier super_admin session in the same browser.
    with client.session_transaction() as sess:
        sess["impersonate_org_id"] = 1
    r = client.get("/admin/tests")
    html = r.data.decode()
    assert "Read-only support view" not in html
    assert ">Assign<" in html and ">Edit<" in html


def test_login_clears_stale_impersonation_key(client, app):
    register_and_verify(client, app, "Saru", "saru2@test.com", "9000000021", "examiner", "Saru-pass1!")
    with client.session_transaction() as sess:
        sess["impersonate_org_id"] = 1
    login(client, "saru2@test.com", "Saru-pass1!")
    with client.session_transaction() as sess:
        assert "impersonate_org_id" not in sess


def test_real_super_admin_impersonation_is_still_read_only(client, app):
    register_and_verify(client, app, "Saru", "saru3@test.com", "9000000022", "examiner", "Saru-pass1!")
    with app.app_context():
        from app import db
        from app.models import User, Organization
        org_id = User.query.filter_by(email="saru3@test.com").first().org_id
        sa = User(name="Root", email="root@test.com", phone="9000000099", role="super_admin", org_id=None)
        sa.set_password("Root-pass1!")
        sa.email_verified = True
        db.session.add(sa)
        db.session.commit()
    login(client, "root@test.com", "Root-pass1!")
    with client.session_transaction() as sess:
        sess["impersonate_org_id"] = org_id
    r = client.get("/admin/tests")
    assert b"Read-only support view" in r.data
    assert b">Assign<" not in r.data
