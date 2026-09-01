import json
from datetime import datetime, timedelta

import pytest

from tests.conftest import register_and_verify, login, add_single_question, get_outbox


def _enroll_face(client):
    descriptor = [0.01 * i for i in range(128)]
    return client.post(
        "/api/proctor/enroll-face",
        data=json.dumps({"descriptor": descriptor}),
        content_type="application/json",
    )


@pytest.fixture()
def notif_setup(client, app):
    register_and_verify(client, app, "Admin", "adminnt@test.com", "9000000080", "admin", "Adminpass1!")
    register_and_verify(client, app, "Student", "studentnt@test.com", "9000000081", "student", "Studpass1!")
    login(client, "studentnt@test.com", "Studpass1!")
    _enroll_face(client)
    client.get("/logout")
    login(client, "adminnt@test.com", "Adminpass1!")
    return {}


def _create_test(client, app, code="NT1", **overrides):
    data = dict(test_code=code, title="Notif Test", description="d", duration_minutes=10,
                total_questions=1, passing_marks=1, status="published", max_attempts=1,
                negative_marks_per_wrong=0)
    data.update(overrides)
    client.post("/admin/tests/create", data=data)
    with app.app_context():
        from app.models import Test
        return Test.query.filter_by(test_code=code).first().id


def _assign(client, app, test_id, student_email="studentnt@test.com", notify=True):
    with app.app_context():
        from app.models import User
        student = User.query.filter_by(email=student_email).first()
    data = {"student_ids": [str(student.id)]}
    if notify:
        data["notify"] = "on"
    return client.post(f"/admin/tests/{test_id}/assign", data=data)


# ---------- exam scheduled ----------

def test_assigning_with_notify_sends_exam_scheduled_email_and_logs_it(client, app, notif_setup):
    test_id = _create_test(client, app)
    _assign(client, app, test_id, notify=True)

    outbox = get_outbox(app)
    assert "New test assigned: Notif Test" in outbox
    assert "studentnt@test.com" in outbox

    with app.app_context():
        from app.models import NotificationLog, User
        student = User.query.filter_by(email="studentnt@test.com").first()
        log = NotificationLog.query.filter_by(user_id=student.id, notif_type="exam_scheduled").first()
        assert log is not None
        assert log.send_status == "logged"  # no SMTP configured in tests
        assert log.test_id == test_id


def test_assigning_without_notify_sends_no_email(client, app, notif_setup):
    test_id = _create_test(client, app, code="NT2")
    _assign(client, app, test_id, notify=False)

    with app.app_context():
        from app.models import NotificationLog, User
        student = User.query.filter_by(email="studentnt@test.com").first()
        log = NotificationLog.query.filter_by(user_id=student.id, notif_type="exam_scheduled").first()
        assert log is None


# ---------- exam starting soon ----------

def test_starting_soon_reminder_sent_for_upcoming_test_not_yet_started(client, app, notif_setup):
    test_id = _create_test(client, app, code="NT3")
    _assign(client, app, test_id, notify=False)

    with app.app_context():
        from app import db
        from app.models import Test
        t = db.session.get(Test, test_id)
        t.start_time = datetime.utcnow() + timedelta(minutes=30)
        db.session.commit()

    r = client.post("/admin/notifications/send-reminders", follow_redirects=True)
    assert r.status_code == 200
    assert b"Sent 1 starting-soon reminder" in r.data

    outbox = get_outbox(app)
    assert "Starting soon: Notif Test" in outbox

    with app.app_context():
        from app.models import NotificationLog, User
        student = User.query.filter_by(email="studentnt@test.com").first()
        assert NotificationLog.query.filter_by(user_id=student.id, notif_type="exam_starting_soon").first()


def test_starting_soon_reminder_not_resent_on_second_run(client, app, notif_setup):
    test_id = _create_test(client, app, code="NT4")
    _assign(client, app, test_id, notify=False)
    with app.app_context():
        from app import db
        from app.models import Test
        t = db.session.get(Test, test_id)
        t.start_time = datetime.utcnow() + timedelta(minutes=10)
        db.session.commit()

    client.post("/admin/notifications/send-reminders")
    r2 = client.post("/admin/notifications/send-reminders", follow_redirects=True)
    assert b"Sent 0 starting-soon reminder" in r2.data


def test_starting_soon_reminder_skipped_once_attempt_started(client, app, notif_setup):
    test_id = _create_test(client, app, code="NT5")
    _assign(client, app, test_id, notify=False)
    with app.app_context():
        from app import db
        from app.models import Test, Attempt, User
        t = db.session.get(Test, test_id)
        t.start_time = datetime.utcnow() + timedelta(minutes=10)
        student = User.query.filter_by(email="studentnt@test.com").first()
        # A test that hasn't opened yet can't be started through the normal
        # route (is_open_now() blocks it) — insert the attempt directly to
        # simulate one that started before start_time was pushed out, or an
        # early admin-granted start; either way, "already has an attempt"
        # should be enough to skip the reminder regardless of how it started.
        db.session.add(Attempt(
            attempt_token="tok-nt5", test_id=test_id, student_id=student.id, status="in_progress",
        ))
        db.session.commit()

    r = client.post("/admin/notifications/send-reminders", follow_redirects=True)
    assert b"Sent 0 starting-soon reminder" in r.data


def test_starting_soon_reminder_ignores_tests_without_start_time(client, app, notif_setup):
    test_id = _create_test(client, app, code="NT6")  # no start_time set
    _assign(client, app, test_id, notify=False)
    r = client.post("/admin/notifications/send-reminders", follow_redirects=True)
    assert b"Sent 0 starting-soon reminder" in r.data


def test_send_reminders_cli_command(client, app, notif_setup):
    test_id = _create_test(client, app, code="NT7")
    _assign(client, app, test_id, notify=False)
    with app.app_context():
        from app import db
        from app.models import Test
        t = db.session.get(Test, test_id)
        t.start_time = datetime.utcnow() + timedelta(minutes=20)
        db.session.commit()

    runner = app.test_cli_runner()
    result = runner.invoke(args=["send-reminders"])
    assert result.exit_code == 0
    assert "Sent 1 starting-soon reminder" in result.output


# ---------- exam completed / result published ----------

def _assign_and_submit(client, app, test_id, correct=True):
    client.get("/logout")
    login(client, "studentnt@test.com", "Studpass1!")
    client.get(f"/student/tests/{test_id}/start")

    with app.app_context():
        from app.models import Question, Attempt, User
        qid = Question.query.filter_by(test_id=test_id).first().id
        student = User.query.filter_by(email="studentnt@test.com").first()
        attempt = Attempt.query.filter_by(test_id=test_id, student_id=student.id).first()

    r = client.post(f"/student/attempts/{attempt.id}/submit", data={f"q_{qid}": "a" if correct else "b"})
    client.get("/logout")
    login(client, "adminnt@test.com", "Adminpass1!")
    return attempt.id


def test_submit_sends_exam_completed_and_result_published_for_objective_test(client, app, notif_setup):
    test_id = _create_test(client, app, code="NT8")
    _assign(client, app, test_id, notify=False)
    add_single_question(client, test_id, "Q", "1", "2", "3", "4", "a", marks=1)

    attempt_id = _assign_and_submit(client, app, test_id, correct=True)

    outbox = get_outbox(app)
    assert "Submitted: Notif Test" in outbox
    assert "Your result is ready: Notif Test" in outbox

    with app.app_context():
        from app.models import NotificationLog, User
        student = User.query.filter_by(email="studentnt@test.com").first()
        types = {n.notif_type for n in NotificationLog.query.filter_by(user_id=student.id, attempt_id=attempt_id).all()}
        assert {"exam_completed", "result_published"} <= types


def test_manual_grading_defers_result_published_until_graded(client, app, notif_setup):
    test_id = _create_test(client, app, code="NT9")
    with app.app_context():
        from app import db
        from app.models import Question
        q = Question(
            test_id=test_id, question_type="descriptive", question_text="Explain your reasoning.",
            correct_answer="(model answer for grader reference)", marks=5,
        )
        db.session.add(q)
        db.session.commit()
        qid = q.id

    _assign(client, app, test_id, notify=False)

    client.get("/logout")
    login(client, "studentnt@test.com", "Studpass1!")
    client.get(f"/student/tests/{test_id}/start")
    with app.app_context():
        from app.models import Attempt, User
        student = User.query.filter_by(email="studentnt@test.com").first()
        attempt = Attempt.query.filter_by(test_id=test_id, student_id=student.id).first()
    client.post(f"/student/attempts/{attempt.id}/submit", data={f"q_{qid}": "My essay answer"})

    outbox_after_submit = get_outbox(app)
    assert "Submitted: Notif Test" in outbox_after_submit
    assert "Your result is ready" not in outbox_after_submit  # still pending manual grading

    client.get("/logout")
    login(client, "adminnt@test.com", "Adminpass1!")
    with app.app_context():
        from app.models import Answer
        answer_id = Answer.query.filter_by(attempt_id=attempt.id, question_id=qid).first().id
    client.post(f"/admin/attempts/{attempt.id}/grade", data={f"score_{answer_id}": "4"})

    outbox_after_grade = get_outbox(app)
    assert "Your result is ready: Notif Test" in outbox_after_grade

    with app.app_context():
        from app.models import NotificationLog
        assert NotificationLog.query.filter_by(attempt_id=attempt.id, notif_type="result_published").count() == 1


# ---------- high-risk alert ----------

def test_high_risk_alert_sent_once_when_score_crosses_threshold(client, app, notif_setup):
    test_id = _create_test(client, app, code="NT10")
    add_single_question(client, test_id, "Q", "1", "2", "3", "4", "a", marks=1)
    _assign(client, app, test_id, notify=False)

    client.get("/logout")
    login(client, "studentnt@test.com", "Studpass1!")
    client.get(f"/student/tests/{test_id}/start")
    with app.app_context():
        from app.models import Attempt, User
        student = User.query.filter_by(email="studentnt@test.com").first()
        attempt = Attempt.query.filter_by(test_id=test_id, student_id=student.id).first()

    # identity_mismatch (20) + identity_spotcheck_failed (20) + phone_detected
    # (15) plus the distinct-event-type bonus comfortably clears the "high"
    # threshold (50) without needing to also hit the termination threshold.
    for event_type in ("identity_mismatch", "identity_spotcheck_failed", "phone_detected"):
        client.post(
            "/api/proctor/event",
            data=json.dumps({"attempt_id": attempt.id, "event_type": event_type, "severity": "violation"}),
            content_type="application/json",
        )

    outbox = get_outbox(app)
    assert "High-risk activity flagged" in outbox

    with app.app_context():
        from app.models import NotificationLog, Attempt as AttemptModel
        count = NotificationLog.query.filter_by(attempt_id=attempt.id, notif_type="high_risk_alert").count()
        assert count >= 1
        a = AttemptModel.query.get(attempt.id)
        assert a.high_risk_alert_sent is True

    # A further violation on an already-flagged attempt shouldn't send a second alert.
    with app.app_context():
        from app.models import NotificationLog
        count_before = NotificationLog.query.filter_by(attempt_id=attempt.id, notif_type="high_risk_alert").count()
    client.post(
        "/api/proctor/event",
        data=json.dumps({"attempt_id": attempt.id, "event_type": "book_detected", "severity": "violation"}),
        content_type="application/json",
    )
    with app.app_context():
        from app.models import NotificationLog
        count_after = NotificationLog.query.filter_by(attempt_id=attempt.id, notif_type="high_risk_alert").count()
    assert count_after == count_before


def test_low_severity_violations_do_not_trigger_high_risk_alert(client, app, notif_setup):
    test_id = _create_test(client, app, code="NT11")
    add_single_question(client, test_id, "Q", "1", "2", "3", "4", "a", marks=1)
    _assign(client, app, test_id, notify=False)

    client.get("/logout")
    login(client, "studentnt@test.com", "Studpass1!")
    client.get(f"/student/tests/{test_id}/start")
    with app.app_context():
        from app.models import Attempt, User
        student = User.query.filter_by(email="studentnt@test.com").first()
        attempt = Attempt.query.filter_by(test_id=test_id, student_id=student.id).first()

    client.post(
        "/api/proctor/event",
        data=json.dumps({"attempt_id": attempt.id, "event_type": "window_blur", "severity": "violation"}),
        content_type="application/json",
    )

    with app.app_context():
        from app.models import NotificationLog
        assert NotificationLog.query.filter_by(attempt_id=attempt.id, notif_type="high_risk_alert").count() == 0


# ---------- admin history page ----------

def test_admin_notification_history_lists_and_filters(client, app, notif_setup):
    test_id = _create_test(client, app, code="NT12")
    _assign(client, app, test_id, notify=True)

    r = client.get("/admin/notifications")
    assert r.status_code == 200
    assert b"Notif Test" in r.data or b"Exam scheduled" in r.data

    r_filtered = client.get("/admin/notifications?type=exam_scheduled")
    assert r_filtered.status_code == 200

    r_other = client.get("/admin/notifications?type=high_risk_alert")
    assert r_other.status_code == 200
    assert b"No notifications sent yet." in r_other.data


def test_notification_history_requires_admin(client, app, notif_setup):
    client.get("/logout")
    login(client, "studentnt@test.com", "Studpass1!")
    r = client.get("/admin/notifications")
    assert r.status_code == 403


def test_trigger_reminders_requires_admin(client, app, notif_setup):
    client.get("/logout")
    login(client, "studentnt@test.com", "Studpass1!")
    r = client.post("/admin/notifications/send-reminders")
    assert r.status_code == 403
