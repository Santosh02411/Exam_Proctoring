import re
import io

import pytest

from tests.conftest import register_and_verify, login, get_captcha_answer, add_single_question, get_outbox


def test_health_endpoint_returns_ok(client, app):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.get_json()["status"] == "ok"


def test_create_app_survives_schema_creation_race(monkeypatch):
    """Regression test for a real bug found in production testing: with
    multiple Gunicorn workers, each process independently calls create_app(),
    and db.create_all() can race between them — two processes can both decide
    the table doesn't exist yet and both issue CREATE TABLE; one wins, the
    other used to crash the whole worker with 'table already exists'.

    True concurrent DDL isn't reliably reproducible in a single-threaded
    test, so this exercises the actual fix directly: force db.create_all() to
    raise the exact error SQLite raises in that race, and confirm
    create_app() absorbs it instead of propagating a crash.
    """
    from app import create_app
    from flask_sqlalchemy import SQLAlchemy
    from sqlalchemy.exc import OperationalError

    def boom(self, *args, **kwargs):
        raise OperationalError("CREATE TABLE users (...)", {}, Exception("table users already exists"))

    monkeypatch.setattr(SQLAlchemy, "create_all", boom)

    # Should not raise — the fix catches OperationalError and rolls back.
    app = create_app()
    assert app is not None


def test_security_headers_present_on_every_response(client, app):
    r = client.get("/login")
    assert r.headers.get("X-Content-Type-Options") == "nosniff"
    assert r.headers.get("X-Frame-Options") == "DENY"
    assert r.headers.get("Referrer-Policy") == "strict-origin-when-cross-origin"


# ---------------------------------------------------------------------------
# Change password (while logged in)
# ---------------------------------------------------------------------------

def test_change_password_requires_login(client, app):
    r = client.get("/change-password")
    assert r.status_code == 302


def test_change_password_wrong_current_password_rejected(client, app):
    register_and_verify(client, app, "CP1", "cp1@test.com", "9000000200", "student", "oldpass1")
    login(client, "cp1@test.com", "oldpass1")
    r = client.post("/change-password", data={
        "current_password": "wrongpass", "new_password": "newpass1", "confirm_password": "newpass1",
    })
    assert b"incorrect" in r.data.lower()


def test_change_password_mismatch_rejected(client, app):
    register_and_verify(client, app, "CP2", "cp2@test.com", "9000000201", "student", "oldpass1")
    login(client, "cp2@test.com", "oldpass1")
    r = client.post("/change-password", data={
        "current_password": "oldpass1", "new_password": "newpass1", "confirm_password": "different1",
    })
    assert b"do not match" in r.data.lower()


def test_change_password_success_updates_login(client, app):
    register_and_verify(client, app, "CP3", "cp3@test.com", "9000000202", "student", "oldpass1")
    login(client, "cp3@test.com", "oldpass1")
    r = client.post("/change-password", data={
        "current_password": "oldpass1", "new_password": "newpass1", "confirm_password": "newpass1",
    }, follow_redirects=True)
    assert r.status_code == 200

    client.get("/logout")
    r = login(client, "cp3@test.com", "oldpass1")
    assert b"My Tests" not in r.data
    r = login(client, "cp3@test.com", "newpass1")
    assert b"My Tests" in r.data


# ---------------------------------------------------------------------------
# Password-reset request cooldown
# ---------------------------------------------------------------------------

def test_reset_request_cooldown_throttles_repeat_emails(client, app):
    register_and_verify(client, app, "CD1", "cd1@test.com", "9000000203", "student", "pass1234")

    client.post("/forgot-password", data={"email": "cd1@test.com"})
    outbox = get_outbox(app)
    count1 = outbox.count("Reset your Exam Proctoring password")
    assert count1 == 1

    client.post("/forgot-password", data={"email": "cd1@test.com"})
    outbox2 = get_outbox(app)
    count2 = outbox2.count("Reset your Exam Proctoring password")
    assert count2 == count1  # no new email sent while cooling down


def test_reset_request_does_not_leak_account_existence(client, app):
    r = client.post("/forgot-password", data={"email": "doesnotexist@test.com"}, follow_redirects=True)
    assert b"If that email is registered" in r.data


# ---------------------------------------------------------------------------
# CSV export of results
# ---------------------------------------------------------------------------

@pytest.fixture()
def graded_test_setup(client, app):
    register_and_verify(client, app, "Exp Admin", "expadmin@test.com", "9000000210", "admin", "adminpass")
    register_and_verify(client, app, "Exp Stu", "expstu@test.com", "9000000211", "student", "studpass")

    login(client, "expadmin@test.com", "adminpass")
    client.post(
        "/admin/tests/create",
        data=dict(test_code="EXP1", title="Export Test", description="d", duration_minutes=10,
                   total_questions=1, passing_marks=1, status="published", max_attempts=1),
    )
    with app.app_context():
        from app.models import Test, User
        test = Test.query.filter_by(test_code="EXP1").first()
        student = User.query.filter_by(email="expstu@test.com").first()

    add_single_question(client, test.id, "Q", "1", "2", "3", "4", "a", marks=1)
    client.post(f"/admin/tests/{test.id}/assign", data={"student_ids": [str(student.id)]})
    client.get("/logout")

    import json
    login(client, "expstu@test.com", "studpass")
    descriptor = [0.01 * i for i in range(128)]
    client.post("/api/proctor/enroll-face", data=json.dumps({"descriptor": descriptor}),
                content_type="application/json")
    client.get(f"/student/tests/{test.id}/start")

    with app.app_context():
        from app.models import Attempt, Question
        attempt = Attempt.query.filter_by(test_id=test.id, student_id=student.id).first()
        q = Question.query.filter_by(test_id=test.id).first()
    client.post(f"/student/attempts/{attempt.id}/submit", data={f"q_{q.id}": "a"})
    client.get("/logout")

    return {"test_id": test.id}


def test_export_csv_contains_correct_data(client, app, graded_test_setup):
    login(client, "expadmin@test.com", "adminpass")
    r = client.get(f"/admin/tests/{graded_test_setup['test_id']}/results/export.csv")
    assert r.status_code == 200
    assert r.content_type.startswith("text/csv")
    assert "attachment" in r.headers.get("Content-Disposition", "")

    text = r.data.decode()
    assert "student_name,student_email,status,score" in text
    assert "Exp Stu,expstu@test.com,submitted,1.0,1,yes" in text


def test_export_csv_requires_admin(client, app, graded_test_setup):
    login(client, "expstu@test.com", "studpass")
    r = client.get(f"/admin/tests/{graded_test_setup['test_id']}/results/export.csv")
    assert r.status_code in (302, 403)


# ---------------------------------------------------------------------------
# Bulk student import
# ---------------------------------------------------------------------------

def test_bulk_student_import_creates_accounts_and_skips_invalid_rows(client, app):
    register_and_verify(client, app, "BSI Admin", "bsiadmin2@test.com", "9000000220", "admin", "adminpass")
    login(client, "bsiadmin2@test.com", "adminpass")

    csv_content = (
        "name,email,phone\n"
        "Valid One,valid1@test.com,9000000221\n"
        "Valid Two,valid2@test.com,9000000222\n"
        "Valid One,valid1@test.com,9000000221\n"  # intra-file duplicate
        ",noname@test.com,9000000223\n"            # missing name
        "Bad Email,not-an-email,9000000224\n"       # invalid email
    )
    r = client.post(
        "/admin/students/import",
        data={"csv_file": (io.BytesIO(csv_content.encode()), "students.csv")},
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    assert r.status_code == 200
    assert b"Imported 2" in r.data

    with app.app_context():
        from app.models import User
        assert User.query.filter_by(email="valid1@test.com").first() is not None
        assert User.query.filter_by(email="valid2@test.com").first() is not None
        assert User.query.filter_by(email="noname@test.com").first() is None
        assert User.query.filter_by(email="not-an-email").first() is None


def test_bulk_imported_student_can_set_password_via_invite_and_login(client, app):
    register_and_verify(client, app, "BSI Admin", "bsiadmin3@test.com", "9000000230", "admin", "adminpass")
    login(client, "bsiadmin3@test.com", "adminpass")

    csv_content = "name,email,phone\nInvited Student,invited@test.com,9000000231\n"
    client.post(
        "/admin/students/import",
        data={"csv_file": (io.BytesIO(csv_content.encode()), "students.csv")},
        content_type="multipart/form-data",
    )
    client.get("/logout")

    outbox = get_outbox(app)
    m = re.search(r"(http://\S*reset-password/\S+)", outbox)
    assert m, "invite link should be logged when no SMTP is configured"
    token = m.group(1).split("/reset-password/")[-1]

    r = client.post(
        f"/reset-password/{token}",
        data={"password": "chosenpass1", "confirm_password": "chosenpass1"},
        follow_redirects=True,
    )
    assert r.status_code == 200

    r = login(client, "invited@test.com", "chosenpass1")
    assert b"My Tests" in r.data


def test_bulk_student_import_requires_admin(client, app):
    register_and_verify(client, app, "Not Admin", "notadmin@test.com", "9000000240", "student", "studpass")
    login(client, "notadmin@test.com", "studpass")
    r = client.get("/admin/students/import")
    assert r.status_code == 403
