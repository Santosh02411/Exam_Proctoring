import json
import os

from tests.conftest import register_and_verify, login, add_single_question


def _sms_outbox(app):
    path = os.path.join(app.instance_path, "sms_outbox.log")
    if not os.path.exists(path):
        return ""
    return open(path, encoding="utf-8").read()


def test_high_risk_alert_sends_sms_when_phone_on_file(client, app):
    register_and_verify(client, app, "Admin", "smsa1@test.com", "9166611001", "admin", "Adminpass1!")
    register_and_verify(client, app, "Student", "smss1@test.com", "9166611002", "student", "Studpass1!")
    login(client, "smsa1@test.com", "Adminpass1!")
    client.post("/admin/tests/create", data=dict(
        test_code="SMS1", title="SMS Test", description="d", duration_minutes=20,
        total_questions=1, passing_marks=1, status="published", max_attempts=1,
        negative_marks_per_wrong=0, proctoring_policy=""))
    with app.app_context():
        from app.models import Test, User
        test_id = Test.query.filter_by(test_code="SMS1").first().id
        student_id = User.query.filter_by(email="smss1@test.com").first().id
        # Make several event types immediately terminating so risk climbs
        # fast and predictably within this test's own scope.
    add_single_question(client, test_id, "2+2=?", "3", "4", "5", "6", "b", marks=1)
    client.post(f"/admin/tests/{test_id}/assign", data={"student_ids": [str(student_id)]})

    before = _sms_outbox(app)
    with app.test_request_context():
        from app.models import Attempt, Test
        from app.notifications import maybe_send_high_risk_alert
        from app import db
        test = Test.query.get(test_id)
        attempt = Attempt(test_id=test.id, student_id=student_id, status="in_progress", attempt_token="tok-sms-1")
        db.session.add(attempt)
        db.session.commit()
        maybe_send_high_risk_alert(attempt, {"score": 80, "level": "high"})

    after = _sms_outbox(app)
    assert len(after) > len(before)
    assert "SMS Test" in after
    assert "HIGH" in after

    with app.app_context():
        from app.models import NotificationLog, User
        admin = User.query.filter_by(email="smsa1@test.com").first()
        log = NotificationLog.query.filter_by(user_id=admin.id, notif_type="high_risk_alert", channel="sms").first()
        assert log is not None
        assert log.send_status == "logged"  # no Twilio configured in tests


def test_no_sms_sent_when_no_phone_on_file(client, app):
    register_and_verify(client, app, "Admin", "smsa2@test.com", "9166611003", "admin", "Adminpass1!")
    with app.app_context():
        from app.models import User
        from app import db
        admin = User.query.filter_by(email="smsa2@test.com").first()
        admin.phone = ""
        db.session.commit()

    with app.test_request_context():
        from app.models import Test, Attempt, User
        from app.notifications import maybe_send_high_risk_alert
        from app import db
        admin = User.query.filter_by(email="smsa2@test.com").first()
        test = Test(
            test_code="SMS2", title="SMS Test 2", duration_minutes=20, total_questions=1,
            passing_marks=1, status="published", created_by=admin.id, org_id=admin.org_id, max_attempts=1,
        )
        db.session.add(test)
        db.session.flush()
        student = User(
            user_id="SMSNOPH1", name="No Phone Student", email="nophone@test.com", phone="",
            role="student", org_id=admin.org_id, status="active", email_verified=True,
        )
        student.set_password("x")
        db.session.add(student)
        db.session.flush()
        attempt = Attempt(test_id=test.id, student_id=student.id, status="in_progress", attempt_token="tok-sms-2")
        db.session.add(attempt)
        db.session.commit()

        before = _sms_outbox(app)
        maybe_send_high_risk_alert(attempt, {"score": 90, "level": "critical"})
        after = _sms_outbox(app)
        # The admin has no phone (cleared above) — no SMS should be attempted.
        assert after == before


def test_exam_warning_never_sends_sms(client, app):
    """exam_warning deliberately has no sms/ template — it fires too
    often to be a reasonable SMS notification type."""
    register_and_verify(client, app, "Admin", "smsa3@test.com", "9166611004", "admin", "Adminpass1!")
    register_and_verify(client, app, "Student", "smss3@test.com", "9166611005", "student", "Studpass1!")
    login(client, "smsa3@test.com", "Adminpass1!")
    client.post("/admin/tests/create", data=dict(
        test_code="SMS3", title="SMS Test 3", description="d", duration_minutes=20,
        total_questions=1, passing_marks=1, status="published", max_attempts=1,
        negative_marks_per_wrong=0))
    with app.app_context():
        from app.models import Test, User
        test_id = Test.query.filter_by(test_code="SMS3").first().id
        student_id = User.query.filter_by(email="smss3@test.com").first().id
    add_single_question(client, test_id, "2+2=?", "3", "4", "5", "6", "b", marks=1)
    client.post(f"/admin/tests/{test_id}/assign", data={"student_ids": [str(student_id)]})

    client.get("/logout")
    login(client, "smss3@test.com", "Studpass1!")
    descriptor = [0.01 * i for i in range(128)]
    client.post("/api/proctor/enroll-face", data=json.dumps({"descriptor": descriptor}), content_type="application/json")
    client.get(f"/student/tests/{test_id}/start")
    with app.app_context():
        from app.models import Attempt
        attempt_id = Attempt.query.filter_by(test_id=test_id).order_by(Attempt.id.desc()).first().id

    before = _sms_outbox(app)
    client.post("/api/proctor/event", data=json.dumps({
        "attempt_id": attempt_id, "event_type": "window_blur", "severity": "warning"}),
        content_type="application/json")
    after = _sms_outbox(app)
    assert after == before  # no SMS for exam_warning, by design

    with app.app_context():
        from app.models import NotificationLog
        sms_log = NotificationLog.query.filter_by(notif_type="exam_warning", channel="sms").first()
        assert sms_log is None


def test_termination_sends_sms_to_student(client, app):
    register_and_verify(client, app, "Admin", "smsa4@test.com", "9166611006", "admin", "Adminpass1!")
    register_and_verify(client, app, "Student", "smss4@test.com", "9166611007", "student", "Studpass1!")
    login(client, "smsa4@test.com", "Adminpass1!")
    client.post("/admin/tests/create", data=dict(
        test_code="SMS4", title="SMS Termination Test", description="d", duration_minutes=20,
        total_questions=1, passing_marks=1, status="published", max_attempts=1,
        negative_marks_per_wrong=0))
    with app.app_context():
        from app.models import Test, User
        from app import db
        test_id = Test.query.filter_by(test_code="SMS4").first().id
        student_id = User.query.filter_by(email="smss4@test.com").first().id
        t = Test.query.get(test_id)
        t.proctoring_policy = json.dumps({"phone_detected": "terminate"})
        db.session.commit()
    add_single_question(client, test_id, "2+2=?", "3", "4", "5", "6", "b", marks=1)
    client.post(f"/admin/tests/{test_id}/assign", data={"student_ids": [str(student_id)]})

    client.get("/logout")
    login(client, "smss4@test.com", "Studpass1!")
    descriptor = [0.01 * i for i in range(128)]
    client.post("/api/proctor/enroll-face", data=json.dumps({"descriptor": descriptor}), content_type="application/json")
    client.get(f"/student/tests/{test_id}/start")
    with app.app_context():
        from app.models import Attempt
        attempt_id = Attempt.query.filter_by(test_id=test_id).order_by(Attempt.id.desc()).first().id

    before = _sms_outbox(app)
    client.post("/api/proctor/event", data=json.dumps({
        "attempt_id": attempt_id, "event_type": "phone_detected", "severity": "violation"}),
        content_type="application/json")
    after = _sms_outbox(app)
    assert len(after) > len(before)
    assert "SMS Termination Test" in after
