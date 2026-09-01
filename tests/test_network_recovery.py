import json
from datetime import datetime, timedelta

import pytest

from tests.conftest import register_and_verify, login, add_single_question


@pytest.fixture()
def recovery_setup(client, app):
    register_and_verify(client, app, "Admin", "adminnr@test.com", "9000000060", "admin", "Adminpass1!")
    register_and_verify(client, app, "Student", "studentnr@test.com", "9000000061", "student", "Studpass1!")

    login(client, "adminnr@test.com", "Adminpass1!")
    client.post(
        "/admin/tests/create",
        data=dict(test_code="NR1", title="Recovery Test", description="d", duration_minutes=10,
                   total_questions=1, passing_marks=1, status="published", max_attempts=1,
                   negative_marks_per_wrong=0),
    )
    with app.app_context():
        from app.models import Test, User
        test = Test.query.filter_by(test_code="NR1").first()
        student = User.query.filter_by(email="studentnr@test.com").first()
    add_single_question(client, test.id, "Q1", "1", "2", "3", "4", "a", marks=1)
    client.post(f"/admin/tests/{test.id}/assign", data={"student_ids": [str(student.id)]})
    client.get("/logout")

    login(client, "studentnr@test.com", "Studpass1!")
    descriptor = [0.01 * i for i in range(128)]
    client.post("/api/proctor/enroll-face", data=json.dumps({"descriptor": descriptor}),
                content_type="application/json")

    return {"test_id": test.id, "student_email": "studentnr@test.com"}


def _get_attempt(app, test_id):
    from app.models import Attempt, User
    with app.app_context():
        student = User.query.filter_by(email="studentnr@test.com").first()
        return Attempt.query.filter_by(test_id=test_id, student_id=student.id).first()


# ---------- connection event logging ----------

def test_connection_lost_and_restored_are_valid_event_types(client, app, recovery_setup):
    client.get(f"/student/tests/{recovery_setup['test_id']}/start")
    attempt = _get_attempt(app, recovery_setup["test_id"])

    r = client.post(
        "/api/proctor/event",
        data=json.dumps({
            "attempt_id": attempt.id, "event_type": "connection_lost", "severity": "warning",
            "details": "Connection to the server was lost",
        }),
        content_type="application/json",
    )
    assert r.status_code == 200
    assert r.get_json()["ok"] is True

    r = client.post(
        "/api/proctor/event",
        data=json.dumps({
            "attempt_id": attempt.id, "event_type": "connection_restored", "severity": "warning",
            "details": "Connection restored after ~12s offline",
        }),
        content_type="application/json",
    )
    assert r.status_code == 200

    with app.app_context():
        from app.models import ProctoringEvent
        events = ProctoringEvent.query.filter_by(attempt_id=attempt.id).order_by(ProctoringEvent.created_at).all()
        types = [e.event_type for e in events]
        assert "connection_lost" in types
        assert "connection_restored" in types


def test_warning_severity_connection_events_do_not_count_as_violations(client, app, recovery_setup):
    client.get(f"/student/tests/{recovery_setup['test_id']}/start")
    attempt = _get_attempt(app, recovery_setup["test_id"])

    r = client.post(
        "/api/proctor/event",
        data=json.dumps({
            "attempt_id": attempt.id, "event_type": "connection_lost", "severity": "warning",
        }),
        content_type="application/json",
    )
    data = r.get_json()
    assert data["violation_count"] == 0


def test_long_outage_connection_restored_can_be_logged_as_violation(client, app, recovery_setup):
    """The client is expected to escalate connection_restored to
    severity=violation when the outage was unusually long (see
    proctor.js's handleOnline()); the server just needs to accept and
    record whatever severity it's told, same as any other event type."""
    client.get(f"/student/tests/{recovery_setup['test_id']}/start")
    attempt = _get_attempt(app, recovery_setup["test_id"])

    r = client.post(
        "/api/proctor/event",
        data=json.dumps({
            "attempt_id": attempt.id, "event_type": "connection_restored", "severity": "violation",
            "details": "Connection restored after ~300s offline",
        }),
        content_type="application/json",
    )
    data = r.get_json()
    assert data["violation_count"] == 1


# ---------- resume / preserved state ----------

def test_refresh_resumes_same_attempt_and_prefills_autosaved_answers(client, app, recovery_setup):
    test_id = recovery_setup["test_id"]
    client.get(f"/student/tests/{test_id}/start")
    attempt = _get_attempt(app, test_id)

    with app.app_context():
        from app.models import Question
        q = Question.query.filter_by(test_id=test_id).first()
        qid = q.id

    client.post(f"/student/attempts/{attempt.id}/autosave", data={f"q_{qid}": "a"})

    # Simulate a browser crash/refresh: hit the start route again.
    r = client.get(f"/student/tests/{test_id}/start")
    assert r.status_code == 200
    assert b"isResume: true" in r.data

    attempt_after = _get_attempt(app, test_id)
    assert attempt_after.id == attempt.id  # same attempt, not a new one
    assert json.loads(attempt_after.autosaved_answers) == {str(qid): "a"}
    # The previously saved value should be pre-filled into the rendered form.
    assert f'name="q_{qid}"'.encode() in r.data


def test_resumed_attempt_timer_reflects_elapsed_time_not_full_duration(client, app, recovery_setup):
    test_id = recovery_setup["test_id"]
    client.get(f"/student/tests/{test_id}/start")
    attempt = _get_attempt(app, test_id)

    with app.app_context():
        from app import db
        from app.models import Attempt
        a = db.session.get(Attempt, attempt.id)
        a.started_at = datetime.utcnow() - timedelta(minutes=4)  # 4 of the 10 minutes already used
        db.session.commit()

    r = client.get(f"/student/tests/{test_id}/start")
    import re
    match = re.search(rb"durationSeconds: (\d+)", r.data)
    assert match is not None
    remaining = int(match.group(1))
    # ~6 minutes (360s) should be left, not a fresh 600s.
    assert 355 <= remaining <= 360


def test_attempt_auto_finalized_when_time_expired_while_disconnected(client, app, recovery_setup):
    """If the student never gets a chance to submit (e.g. was offline right
    up to the deadline) and instead just reloads later, the server should
    grade on the last autosaved answers instead of leaving the attempt
    stuck in_progress or discarding it."""
    test_id = recovery_setup["test_id"]
    client.get(f"/student/tests/{test_id}/start")
    attempt = _get_attempt(app, test_id)

    with app.app_context():
        from app.models import Question
        qid = Question.query.filter_by(test_id=test_id).first().id

    client.post(f"/student/attempts/{attempt.id}/autosave", data={f"q_{qid}": "a"})

    with app.app_context():
        from app import db
        from app.models import Attempt
        a = db.session.get(Attempt, attempt.id)
        a.started_at = datetime.utcnow() - timedelta(minutes=30)  # well past the 10-minute duration
        db.session.commit()

    r = client.get(f"/student/tests/{test_id}/start", follow_redirects=True)
    assert r.status_code == 200

    with app.app_context():
        from app.models import Attempt, Answer
        a = Attempt.query.get(attempt.id)
        assert a.status == "submitted"
        assert a.score == 1.0  # the autosaved "a" was correct
        ans = Answer.query.filter_by(attempt_id=attempt.id, question_id=qid).first()
        assert ans.selected_option == "a"


def test_heartbeat_reports_status_and_remaining_seconds(client, app, recovery_setup):
    test_id = recovery_setup["test_id"]
    client.get(f"/student/tests/{test_id}/start")
    attempt = _get_attempt(app, test_id)

    r = client.get(f"/student/attempts/{attempt.id}/heartbeat")
    assert r.status_code == 200
    data = r.get_json()
    assert data["ok"] is True
    assert data["status"] == "in_progress"
    assert isinstance(data["remaining_seconds"], int)
    assert data["remaining_seconds"] <= 600


def test_heartbeat_reflects_terminated_status_with_no_remaining_time(client, app, recovery_setup):
    test_id = recovery_setup["test_id"]
    client.get(f"/student/tests/{test_id}/start")
    attempt = _get_attempt(app, test_id)

    for _ in range(5):
        client.post(
            "/api/proctor/event",
            data=json.dumps({"attempt_id": attempt.id, "event_type": "tab_hidden", "severity": "violation"}),
            content_type="application/json",
        )

    r = client.get(f"/student/attempts/{attempt.id}/heartbeat")
    data = r.get_json()
    assert data["status"] == "terminated"
    assert data["remaining_seconds"] is None


def test_heartbeat_rejects_other_students_attempt(client, app, recovery_setup):
    test_id = recovery_setup["test_id"]
    client.get(f"/student/tests/{test_id}/start")
    attempt = _get_attempt(app, test_id)
    client.get("/logout")

    register_and_verify(client, app, "Other", "othernr@test.com", "9000000062", "student", "Studpass1!")
    login(client, "othernr@test.com", "Studpass1!")
    r = client.get(f"/student/attempts/{attempt.id}/heartbeat")
    assert r.status_code == 403
