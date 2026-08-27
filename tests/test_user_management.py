import io
import json

import pytest

from tests.conftest import register_and_verify, login, add_single_question


def _enroll_face(client):
    descriptor = [0.01 * i for i in range(128)]
    return client.post(
        "/api/proctor/enroll-face",
        data=json.dumps({"descriptor": descriptor}),
        content_type="application/json",
    )


@pytest.fixture()
def admin_and_student(client, app):
    register_and_verify(client, app, "Admin", "admin_um@test.com", "9000000070", "admin", "Adminpass1!")
    register_and_verify(client, app, "Stu One", "stu_um1@test.com", "9000000071", "student", "Studpass1!")
    register_and_verify(client, app, "Stu Two", "stu_um2@test.com", "9000000072", "student", "Studpass1!")
    login(client, "admin_um@test.com", "Adminpass1!")
    with app.app_context():
        from app.models import User
        s1 = User.query.filter_by(email="stu_um1@test.com").first()
        s2 = User.query.filter_by(email="stu_um2@test.com").first()
        admin = User.query.filter_by(email="admin_um@test.com").first()
    return {"s1": s1.id, "s2": s2.id, "admin": admin.id}


def test_manage_users_lists_everyone(client, app, admin_and_student):
    r = client.get("/admin/users")
    assert r.status_code == 200
    assert b"stu_um1@test.com" in r.data
    assert b"stu_um2@test.com" in r.data
    assert b"admin_um@test.com" in r.data


def test_manage_users_search_filters(client, app, admin_and_student):
    r = client.get("/admin/users?q=stu_um1")
    assert r.status_code == 200
    assert b"stu_um1@test.com" in r.data
    assert b"stu_um2@test.com" not in r.data


def test_toggle_status_deactivates_and_blocks_login(client, app, admin_and_student):
    s1 = admin_and_student["s1"]
    r = client.post(f"/admin/users/{s1}/toggle-status", follow_redirects=True)
    assert r.status_code == 200

    with app.app_context():
        from app.models import User
        user = User.query.get(s1)
        assert user.status == "inactive"

    client.get("/logout")
    r = login(client, "stu_um1@test.com", "Studpass1!")
    assert b"Account is not active" in r.data


def test_toggle_status_reactivates(client, app, admin_and_student):
    s1 = admin_and_student["s1"]
    client.post(f"/admin/users/{s1}/toggle-status")
    client.post(f"/admin/users/{s1}/toggle-status")
    with app.app_context():
        from app.models import User
        user = User.query.get(s1)
        assert user.status == "active"


def test_admin_cannot_deactivate_self(client, app, admin_and_student):
    admin_id = admin_and_student["admin"]
    client.post(f"/admin/users/{admin_id}/toggle-status")
    with app.app_context():
        from app.models import User
        user = User.query.get(admin_id)
        assert user.status == "active"


def test_delete_user_without_history_removes_account(client, app, admin_and_student):
    s2 = admin_and_student["s2"]
    r = client.post(f"/admin/users/{s2}/delete", follow_redirects=True)
    assert r.status_code == 200
    with app.app_context():
        from app.models import User
        assert User.query.get(s2) is None


def test_delete_user_with_attempt_history_is_blocked(client, app, admin_and_student):
    s1 = admin_and_student["s1"]
    # Create a published test, assign, and have the student attempt it.
    client.post(
        "/admin/tests/create",
        data=dict(test_code="UMHIST", title="History Test", description="d", duration_minutes=20,
                   total_questions=1, passing_marks=1, status="published", max_attempts=1,
                   negative_marks_per_wrong=0),
    )
    with app.app_context():
        from app.models import Test
        test = Test.query.filter_by(test_code="UMHIST").first()
        test_id = test.id
    add_single_question(client, test_id, "Q1?", "A", "B", "C", "D", "a")
    client.post(f"/admin/tests/{test_id}/assign", data={"student_ids": [str(s1)]})

    client.get("/logout")
    login(client, "stu_um1@test.com", "Studpass1!")
    _enroll_face(client)
    client.get(f"/student/tests/{test_id}/start")

    client.get("/logout")
    login(client, "admin_um@test.com", "Adminpass1!")
    r = client.post(f"/admin/users/{s1}/delete", follow_redirects=True)
    assert b"exam history" in r.data
    with app.app_context():
        from app.models import User
        assert User.query.get(s1) is not None


def test_export_users_returns_csv(client, app, admin_and_student):
    r = client.get("/admin/users/export")
    assert r.status_code == 200
    assert r.mimetype == "text/csv"
    body = r.data.decode()
    assert "stu_um1@test.com" in body
    assert "stu_um2@test.com" in body


def test_import_users_creates_accounts_and_shows_preview(client, app, admin_and_student):
    csv_content = "name,email,phone,password\nNew Kid,newkid@test.com,9000000099,newkidpass\n"
    r = client.post(
        "/admin/users/import",
        data={"csv_file": (io.BytesIO(csv_content.encode()), "students.csv")},
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    assert r.status_code == 200
    assert b"newkid@test.com" in r.data

    with app.app_context():
        from app.models import User
        u = User.query.filter_by(email="newkid@test.com").first()
        assert u is not None
        assert u.role == "student"
        assert u.status == "active"
        assert u.email_verified is True
        assert u.check_password("newkidpass")


def test_import_users_skips_duplicate_emails(client, app, admin_and_student):
    csv_content = "name,email\nDupe,stu_um1@test.com\n"
    r = client.post(
        "/admin/users/import",
        data={"csv_file": (io.BytesIO(csv_content.encode()), "students.csv")},
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    assert b"Skipped 1 row" in r.data


def test_admin_can_grant_extra_attempts(client, app, admin_and_student):
    s1 = admin_and_student["s1"]
    client.post(
        "/admin/tests/create",
        data=dict(test_code="EXTRA1", title="Extra Attempts Test", description="d", duration_minutes=20,
                   total_questions=1, passing_marks=1, status="published", max_attempts=1,
                   negative_marks_per_wrong=0),
    )
    with app.app_context():
        from app.models import Test
        test = Test.query.filter_by(test_code="EXTRA1").first()
        test_id = test.id
    add_single_question(client, test_id, "Q1?", "A", "B", "C", "D", "a")
    client.post(f"/admin/tests/{test_id}/assign", data={"student_ids": [str(s1)]})

    client.get("/logout")
    login(client, "stu_um1@test.com", "Studpass1!")
    _enroll_face(client)
    r = client.get(f"/student/tests/{test_id}/start")
    with app.app_context():
        from app.models import Attempt, Question
        attempt = Attempt.query.filter_by(test_id=test_id, student_id=s1).first()
        attempt_id = attempt.id
        question_id = Question.query.filter_by(test_id=test_id).first().id
    client.post(f"/student/attempts/{attempt_id}/submit", data={f"q_{question_id}": "a"})

    # No extra attempts granted yet — a second start should be blocked (max_attempts=1).
    r = client.get(f"/student/tests/{test_id}/start", follow_redirects=True)
    assert b"used all 1 attempt" in r.data

    client.get("/logout")
    login(client, "admin_um@test.com", "Adminpass1!")
    client.post(
        f"/admin/tests/{test_id}/eligibility/{s1}/update",
        data={"extra_time_minutes": "0", "extra_attempts": "1"},
    )

    client.get("/logout")
    login(client, "stu_um1@test.com", "Studpass1!")
    r = client.get(f"/student/tests/{test_id}/start")
    assert r.status_code == 200
    assert b"used all" not in r.data
