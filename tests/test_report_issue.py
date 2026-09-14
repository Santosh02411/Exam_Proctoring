import json

from tests.conftest import register_and_verify, login, add_single_question


def _setup(client, app, admin_email, student_email, test_code):
    register_and_verify(client, app, "Admin", admin_email, "9111100001", "admin", "Adminpass1!")
    register_and_verify(client, app, "Student", student_email, "9111100002", "student", "Studpass1!")
    login(client, admin_email, "Adminpass1!")
    client.post("/admin/tests/create", data=dict(
        test_code=test_code, title="Report Issue Test", description="d", duration_minutes=20,
        total_questions=1, passing_marks=1, status="published", max_attempts=1,
        negative_marks_per_wrong=0))
    with app.app_context():
        from app.models import Test, User
        test_id = Test.query.filter_by(test_code=test_code).first().id
        student_id = User.query.filter_by(email=student_email).first().id
    add_single_question(client, test_id, "2+2=?", "3", "4", "5", "6", "b", marks=1)
    client.post(f"/admin/tests/{test_id}/assign", data={"student_ids": [str(student_id)]})
    client.get("/logout")
    login(client, student_email, "Studpass1!")
    descriptor = [0.01 * i for i in range(128)]
    client.post("/api/proctor/enroll-face", data=json.dumps({"descriptor": descriptor}), content_type="application/json")
    client.get(f"/student/tests/{test_id}/start")
    with app.app_context():
        from app.models import Attempt
        attempt_id = Attempt.query.filter_by(test_id=test_id).order_by(Attempt.id.desc()).first().id
    return test_id, attempt_id


def test_report_issue_logs_an_info_event(client, app):
    test_id, attempt_id = _setup(client, app, "ria1@test.com", "ris1@test.com", "RI1")
    r = client.post("/api/proctor/report-issue", data=json.dumps({
        "attempt_id": attempt_id, "message": "My webcam keeps disconnecting.",
    }), content_type="application/json")
    assert r.status_code == 200
    assert r.get_json()["ok"] is True

    with app.app_context():
        from app.models import ProctoringEvent, Attempt
        ev = ProctoringEvent.query.filter_by(attempt_id=attempt_id, event_type="student_reported_issue").first()
        assert ev is not None
        assert ev.severity == "info"
        assert ev.details == "My webcam keeps disconnecting."

        # Never counts as a violation, regardless of anything else.
        attempt = Attempt.query.get(attempt_id)
        assert attempt.violation_count == 0


def test_report_issue_requires_a_message(client, app):
    test_id, attempt_id = _setup(client, app, "ria2@test.com", "ris2@test.com", "RI2")
    r = client.post("/api/proctor/report-issue", data=json.dumps({
        "attempt_id": attempt_id, "message": "   ",
    }), content_type="application/json")
    assert r.status_code == 400


def test_report_issue_truncates_overlong_messages(client, app):
    test_id, attempt_id = _setup(client, app, "ria3@test.com", "ris3@test.com", "RI3")
    long_message = "x" * 1000
    client.post("/api/proctor/report-issue", data=json.dumps({
        "attempt_id": attempt_id, "message": long_message,
    }), content_type="application/json")
    with app.app_context():
        from app.models import ProctoringEvent
        ev = ProctoringEvent.query.filter_by(attempt_id=attempt_id, event_type="student_reported_issue").first()
        assert len(ev.details) == 500


def test_report_issue_appears_in_live_alerts(client, app):
    test_id, attempt_id = _setup(client, app, "ria4@test.com", "ris4@test.com", "RI4")
    client.post("/api/proctor/report-issue", data=json.dumps({
        "attempt_id": attempt_id, "message": "I think I was flagged by mistake.",
    }), content_type="application/json")

    with app.app_context():
        from app.models import User
        from app.proctoring import get_live_alerts_since
        admin = User.query.filter_by(email="ria4@test.com").first()
        alerts = get_live_alerts_since(0, admin.org_id)
        matching = [a for a in alerts if a.event_type == "student_reported_issue"]
        assert len(matching) == 1
        assert matching[0].details == "I think I was flagged by mistake."


def test_report_issue_shows_in_behavior_timeline_with_report_styling(client, app):
    test_id, attempt_id = _setup(client, app, "ria5@test.com", "ris5@test.com", "RI5")
    client.post("/api/proctor/report-issue", data=json.dumps({
        "attempt_id": attempt_id, "message": "Timer seems wrong.",
    }), content_type="application/json")

    with app.app_context():
        from app.models import Attempt
        from app import proctoring
        attempt = Attempt.query.get(attempt_id)
        timeline = proctoring.build_timeline(attempt)
        matching = [i for i in timeline["entries"] if "Timer seems wrong" in (i["detail"] or "")]
        assert len(matching) == 1
        assert matching[0]["severity"] == "report"


def test_report_issue_rejected_after_attempt_ends(client, app):
    test_id, attempt_id = _setup(client, app, "ria6@test.com", "ris6@test.com", "RI6")
    client.post(f"/student/attempts/{attempt_id}/submit", data={})
    r = client.post("/api/proctor/report-issue", data=json.dumps({
        "attempt_id": attempt_id, "message": "Too late now.",
    }), content_type="application/json")
    assert r.status_code == 400
