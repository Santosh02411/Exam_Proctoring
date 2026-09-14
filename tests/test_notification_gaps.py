import json
import secrets

from werkzeug.security import generate_password_hash

from tests.conftest import register_and_verify, login, add_single_question


def test_warning_events_send_an_email_to_the_student(client, app):
    register_and_verify(client, app, "Admin", "wnadmin@test.com", "9188888801", "admin", "Adminpass1!")
    register_and_verify(client, app, "Student", "wnstudent@test.com", "9188888802", "student", "Studpass1!")
    login(client, "wnadmin@test.com", "Adminpass1!")
    client.post("/admin/tests/create", data=dict(
        test_code="WN1", title="Warning Notif Test", description="d", duration_minutes=20,
        total_questions=1, passing_marks=1, status="published", max_attempts=1,
        negative_marks_per_wrong=0))
    with app.app_context():
        from app.models import Test, User
        test_id = Test.query.filter_by(test_code="WN1").first().id
        student_id = User.query.filter_by(email="wnstudent@test.com").first().id
    add_single_question(client, test_id, "2+2=?", "3", "4", "5", "6", "b", marks=1)
    client.post(f"/admin/tests/{test_id}/assign", data={"student_ids": [str(student_id)]})

    client.get("/logout")
    login(client, "wnstudent@test.com", "Studpass1!")
    descriptor = [0.01 * i for i in range(128)]
    client.post("/api/proctor/enroll-face", data=json.dumps({"descriptor": descriptor}), content_type="application/json")
    client.get(f"/student/tests/{test_id}/start")
    with app.app_context():
        from app.models import Attempt
        attempt_id = Attempt.query.filter_by(test_id=test_id).order_by(Attempt.id.desc()).first().id

    client.post("/api/proctor/event", data=json.dumps({
        "attempt_id": attempt_id, "event_type": "window_blur", "severity": "warning"}),
        content_type="application/json")

    with app.app_context():
        from app.models import NotificationLog
        log = NotificationLog.query.filter_by(user_id=student_id, notif_type="exam_warning").first()
        assert log is not None
        assert log.send_status in ("sent", "logged")
        assert "Warning Notif Test" in log.subject


def test_api_enrollment_sends_assignment_notification(client, app):
    register_and_verify(client, app, "Admin", "apinadmin@test.com", "9188888803", "admin", "Adminpass1!")
    login(client, "apinadmin@test.com", "Adminpass1!")
    client.post("/admin/tests/create", data=dict(
        test_code="APIN1", title="API Notif Test", description="d", duration_minutes=20,
        total_questions=1, passing_marks=1, status="published", max_attempts=1,
        negative_marks_per_wrong=0))

    with app.app_context():
        from app.models import User, ApiKey
        from app import db
        raw_key = secrets.token_urlsafe(24)
        admin = User.query.filter_by(email="apinadmin@test.com").first()
        db.session.add(ApiKey(
            org_id=admin.org_id, label="test-key", key_hash=generate_password_hash(raw_key),
            prefix=raw_key[:16], created_by_id=admin.id,
        ))
        db.session.commit()

    r = client.post(
        "/api/v1/tests/APIN1/enroll",
        headers={"Authorization": f"Bearer {raw_key}"},
        json={"students": [{"name": "API Student", "email": "apistudent@test.com"}]},
    )
    assert r.status_code == 200
    assert r.get_json()["newly_enrolled"] == 1

    with app.app_context():
        from app.models import NotificationLog, User
        api_student = User.query.filter_by(email="apistudent@test.com").first()
        log = NotificationLog.query.filter_by(user_id=api_student.id, notif_type="exam_scheduled").first()
        assert log is not None
        assert "API Notif Test" in log.subject


def test_api_enrollment_can_suppress_notification(client, app):
    register_and_verify(client, app, "Admin", "apinadmin2@test.com", "9188888804", "admin", "Adminpass1!")
    login(client, "apinadmin2@test.com", "Adminpass1!")
    client.post("/admin/tests/create", data=dict(
        test_code="APIN2", title="API Notif Test 2", description="d", duration_minutes=20,
        total_questions=1, passing_marks=1, status="published", max_attempts=1,
        negative_marks_per_wrong=0))

    with app.app_context():
        from app.models import User, ApiKey
        from app import db
        raw_key = secrets.token_urlsafe(24)
        admin = User.query.filter_by(email="apinadmin2@test.com").first()
        db.session.add(ApiKey(
            org_id=admin.org_id, label="test-key-2", key_hash=generate_password_hash(raw_key),
            prefix=raw_key[:16], created_by_id=admin.id,
        ))
        db.session.commit()

    r = client.post(
        "/api/v1/tests/APIN2/enroll",
        headers={"Authorization": f"Bearer {raw_key}"},
        json={"students": [{"name": "Silent Student", "email": "silentstudent@test.com"}], "notify": False},
    )
    assert r.status_code == 200

    with app.app_context():
        from app.models import NotificationLog, User
        student = User.query.filter_by(email="silentstudent@test.com").first()
        log = NotificationLog.query.filter_by(user_id=student.id, notif_type="exam_scheduled").first()
        assert log is None
