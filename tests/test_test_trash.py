import json
from datetime import datetime

from tests.conftest import register_and_verify, login, add_single_question


def _setup(client, app, admin_email, student_email, test_code):
    register_and_verify(client, app, "Admin", admin_email, "9188877001", "admin", "Adminpass1!")
    register_and_verify(client, app, "Student", student_email, "9188877002", "student", "Studpass1!")
    login(client, admin_email, "Adminpass1!")
    client.post("/admin/tests/create", data=dict(
        test_code=test_code, title="Trash Test", description="d", duration_minutes=20,
        total_questions=1, passing_marks=1, status="published", max_attempts=1, negative_marks_per_wrong=0))
    with app.app_context():
        from app.models import Test, User
        test_id = Test.query.filter_by(test_code=test_code).first().id
        student_id = User.query.filter_by(email=student_email).first().id
    add_single_question(client, test_id, "2+2=?", "3", "4", "5", "6", "b", marks=1)
    client.post(f"/admin/tests/{test_id}/assign", data={"student_ids": [str(student_id)], "notify": "on"})
    return test_id, student_id


def test_delete_moves_to_trash_not_hard_delete(client, app):
    test_id, _ = _setup(client, app, "tta1@test.com", "tts1@test.com", "TT1")
    r = client.post(f"/admin/tests/{test_id}/delete", follow_redirects=True)
    assert b"moved to Trash" in r.data

    with app.app_context():
        from app.models import Test
        test = Test.query.get(test_id)
        assert test is not None  # row still exists
        assert test.deleted_at is not None


def test_trashed_test_disappears_from_manage_tests(client, app):
    test_id, _ = _setup(client, app, "tta2@test.com", "tts2@test.com", "TT2")
    client.post(f"/admin/tests/{test_id}/delete")
    r = client.get("/admin/tests")
    assert b"Trash Test" not in r.data


def test_trashed_test_appears_in_trash_page(client, app):
    test_id, _ = _setup(client, app, "tta3@test.com", "tts3@test.com", "TT3")
    client.post(f"/admin/tests/{test_id}/delete")
    r = client.get("/admin/tests/trash")
    assert b"Trash Test" in r.data


def test_restore_brings_test_back(client, app):
    test_id, _ = _setup(client, app, "tta4@test.com", "tts4@test.com", "TT4")
    client.post(f"/admin/tests/{test_id}/delete")
    r = client.post(f"/admin/tests/{test_id}/restore", follow_redirects=True)
    assert b"restored" in r.data

    with app.app_context():
        from app.models import Test
        assert Test.query.get(test_id).deleted_at is None

    r2 = client.get("/admin/tests")
    assert b"Trash Test" in r2.data


def test_permanent_delete_actually_removes_the_row(client, app):
    test_id, _ = _setup(client, app, "tta5@test.com", "tts5@test.com", "TT5")
    client.post(f"/admin/tests/{test_id}/delete")
    r = client.post(f"/admin/tests/{test_id}/delete-permanently", follow_redirects=True)
    assert b"permanently deleted" in r.data

    with app.app_context():
        from app.models import Test
        assert Test.query.get(test_id) is None


def test_cannot_permanently_delete_a_test_not_in_trash(client, app):
    test_id, _ = _setup(client, app, "tta6@test.com", "tts6@test.com", "TT6")
    r = client.post(f"/admin/tests/{test_id}/delete-permanently", follow_redirects=True)
    assert b"Move this test to Trash first" in r.data
    with app.app_context():
        from app.models import Test
        assert Test.query.get(test_id) is not None


def test_trashed_test_cannot_be_started_by_student(client, app):
    """The critical safety case: a trashed test must not be startable,
    even by a student who already had eligibility and a direct URL."""
    test_id, student_id = _setup(client, app, "tta7@test.com", "tts7@test.com", "TT7")
    client.post(f"/admin/tests/{test_id}/delete")

    client.get("/logout")
    login(client, "tts7@test.com", "Studpass1!")
    descriptor = [0.01 * i for i in range(128)]
    client.post("/api/proctor/enroll-face", data=json.dumps({"descriptor": descriptor}), content_type="application/json")
    r = client.get(f"/student/tests/{test_id}/start")
    assert r.status_code == 404

    with app.app_context():
        from app.models import Attempt
        assert Attempt.query.filter_by(test_id=test_id, student_id=student_id).count() == 0


def test_trashed_test_hidden_from_student_dashboard(client, app):
    test_id, student_id = _setup(client, app, "tta8@test.com", "tts8@test.com", "TT8")
    client.post(f"/admin/tests/{test_id}/delete")

    client.get("/logout")
    login(client, "tts8@test.com", "Studpass1!")
    r = client.get("/student/dashboard")
    assert b"Trash Test" not in r.data


def test_trashed_test_hidden_from_student_calendar(client, app):
    test_id, student_id = _setup(client, app, "tta9@test.com", "tts9@test.com", "TT9")
    with app.app_context():
        from app.models import Test
        from app import db
        t = Test.query.get(test_id)
        t.start_time = datetime(2026, 6, 15, 10, 0)
        db.session.commit()
    client.post(f"/admin/tests/{test_id}/delete")

    client.get("/logout")
    login(client, "tts9@test.com", "Studpass1!")
    r = client.get("/student/calendar?year=2026&month=6")
    assert b"Trash Test" not in r.data


def test_trashed_test_hidden_from_admin_calendar(client, app):
    test_id, _ = _setup(client, app, "tta10@test.com", "tts10@test.com", "TT10")
    with app.app_context():
        from app.models import Test
        from app import db
        t = Test.query.get(test_id)
        t.start_time = datetime(2026, 7, 10, 9, 0)
        db.session.commit()
    client.post(f"/admin/tests/{test_id}/delete")
    r = client.get("/admin/calendar?year=2026&month=7")
    assert b"Trash Test" not in r.data


def test_trashed_test_excluded_from_api_published_list(client, app):
    import secrets
    from werkzeug.security import generate_password_hash
    test_id, _ = _setup(client, app, "tta11@test.com", "tts11@test.com", "TT11")
    with app.app_context():
        from app.models import User, ApiKey
        from app import db
        raw_key = secrets.token_urlsafe(24)
        admin = User.query.filter_by(email="tta11@test.com").first()
        db.session.add(ApiKey(
            org_id=admin.org_id, label="trash-test-key", key_hash=generate_password_hash(raw_key),
            prefix=raw_key[:16], created_by_id=admin.id,
        ))
        db.session.commit()

    client.post(f"/admin/tests/{test_id}/delete")
    r = client.get("/api/v1/tests", headers={"Authorization": f"Bearer {raw_key}"})
    assert r.status_code == 200
    codes = [t["test_code"] for t in r.get_json()["tests"]]
    assert "TT11" not in codes
