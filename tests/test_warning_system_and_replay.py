import io
import json

import pytest

from tests.conftest import register_and_verify, login, add_single_question


@pytest.fixture()
def attempt_setup(client, app):
    register_and_verify(client, app, "Admin", "admin3@test.com", "9000000030", "admin", "Adminpass1!")
    register_and_verify(client, app, "Student", "student3@test.com", "9000000031", "student", "Studpass1!")

    login(client, "admin3@test.com", "Adminpass1!")
    client.post(
        "/admin/tests/create",
        data=dict(test_code="W1", title="Warning Test", description="d", duration_minutes=20,
                   total_questions=1, passing_marks=1, status="published", max_attempts=1,
                   negative_marks_per_wrong=0),
    )
    with app.app_context():
        from app.models import Test, User
        test = Test.query.filter_by(test_code="W1").first()
        student = User.query.filter_by(email="student3@test.com").first()
        test_id, student_id = test.id, student.id
    add_single_question(client, test_id, "Q1", "1", "2", "3", "4", "a", marks=1)
    add_single_question(client, test_id, "Q2", "1", "2", "3", "4", "b", marks=1)
    client.post(f"/admin/tests/{test_id}/assign", data={"student_ids": [str(student_id)]})

    return {"test_id": test_id, "student_id": student_id, "admin_email": "admin3@test.com"}


def _start_attempt(client, app, test_id):
    client.get("/logout")
    login(client, "student3@test.com", "Studpass1!")
    descriptor = [0.01 * i for i in range(128)]
    client.post("/api/proctor/enroll-face", data=json.dumps({"descriptor": descriptor}),
                content_type="application/json")
    client.get(f"/student/tests/{test_id}/start")
    with app.app_context():
        from app.models import Attempt
        attempt = Attempt.query.filter_by(test_id=test_id).order_by(Attempt.id.desc()).first()
        return attempt.id


# ---------------------------------------------------------------------------
# Customizable Warning System
# ---------------------------------------------------------------------------

def test_admin_can_save_warning_limit_message_and_grace_period(client, app, attempt_setup):
    test_id = attempt_setup["test_id"]
    r = client.post(
        f"/admin/tests/{test_id}/proctoring-policy",
        data={
            "policy_window_blur": "warning",
            "warning_limit_window_blur": "2",
            "escalate_window_blur": "flag",
            "message_window_blur": "Please keep the exam window focused.",
            "grace_window_blur": "10",
        },
    )
    assert r.status_code in (200, 302)

    with app.app_context():
        from app.models import Test
        from app import proctoring
        test = Test.query.get(test_id)
        policy = proctoring.get_policy(test)
        entry = policy["window_blur"]
        assert entry["action"] == "warning"
        assert entry["warning_limit"] == 2
        assert entry["escalate_action"] == "flag"
        assert entry["message"] == "Please keep the exam window focused."
        assert entry["grace_period_seconds"] == 10


def test_warning_limit_escalates_after_configured_count(client, app, attempt_setup):
    test_id = attempt_setup["test_id"]
    client.post(
        f"/admin/tests/{test_id}/proctoring-policy",
        data={
            "policy_window_blur": "warning",
            "warning_limit_window_blur": "2",
            "escalate_window_blur": "flag",
            "message_window_blur": "Stay focused on the exam window.",
        },
    )
    attempt_id = _start_attempt(client, app, test_id)

    def fire():
        r = client.post(
            "/api/proctor/event",
            data=json.dumps({"attempt_id": attempt_id, "event_type": "window_blur", "severity": "warning"}),
            content_type="application/json",
        )
        return r.get_json()

    d1 = fire()
    assert d1["violation_count"] == 0
    assert d1["message"] == "Stay focused on the exam window. (1 warning left before this counts as a violation.)"

    d2 = fire()
    assert d2["violation_count"] == 0
    assert "Last warning" in d2["message"]

    d3 = fire()
    assert d3["violation_count"] == 1  # 3rd occurrence escalates to "flag"

    with app.app_context():
        from app.models import Attempt
        attempt = Attempt.query.get(attempt_id)
        assert json.loads(attempt.warning_counts)["window_blur"] == 3


def test_warning_limit_escalates_to_terminate(client, app, attempt_setup):
    test_id = attempt_setup["test_id"]
    client.post(
        f"/admin/tests/{test_id}/proctoring-policy",
        data={
            "policy_phone_detected": "warning",
            "warning_limit_phone_detected": "1",
            "escalate_phone_detected": "terminate",
        },
    )
    attempt_id = _start_attempt(client, app, test_id)

    def fire():
        r = client.post(
            "/api/proctor/event",
            data=json.dumps({"attempt_id": attempt_id, "event_type": "phone_detected", "severity": "violation"}),
            content_type="application/json",
        )
        return r.get_json()

    d1 = fire()
    assert d1["terminated"] is False
    d2 = fire()
    assert d2["terminated"] is True

    with app.app_context():
        from app.models import Attempt
        attempt = Attempt.query.get(attempt_id)
        assert attempt.status == "terminated"


def test_grace_period_suppresses_event_entirely(client, app, attempt_setup):
    test_id = attempt_setup["test_id"]
    client.post(
        f"/admin/tests/{test_id}/proctoring-policy",
        data={"policy_tab_hidden": "flag", "grace_tab_hidden": "600"},
    )
    attempt_id = _start_attempt(client, app, test_id)

    r = client.post(
        "/api/proctor/event",
        data=json.dumps({"attempt_id": attempt_id, "event_type": "tab_hidden", "severity": "violation"}),
        content_type="application/json",
    )
    data = r.get_json()
    assert data["violation_count"] == 0
    assert data["terminated"] is False

    with app.app_context():
        from app.models import ProctoringEvent
        ev = ProctoringEvent.query.filter_by(attempt_id=attempt_id, event_type="tab_hidden").first()
        assert ev.severity == "info"


def test_legacy_string_policy_still_works(client, app, attempt_setup):
    """A policy stored in the pre-Customizable-Warning-System format (a
    bare action string) should still parse and behave as before."""
    test_id = attempt_setup["test_id"]
    with app.app_context():
        from app import db
        from app.models import Test
        test = Test.query.get(test_id)
        test.proctoring_policy = json.dumps({"copy_paste_attempt": "terminate"})
        db.session.commit()

    attempt_id = _start_attempt(client, app, test_id)
    r = client.post(
        "/api/proctor/event",
        data=json.dumps({"attempt_id": attempt_id, "event_type": "copy_paste_attempt", "severity": "violation"}),
        content_type="application/json",
    )
    assert r.get_json()["terminated"] is True


# ---------------------------------------------------------------------------
# Complete Exam Replay
# ---------------------------------------------------------------------------

def test_webcam_and_screen_chunks_stored_separately(client, app, attempt_setup):
    attempt_id = _start_attempt(client, app, attempt_setup["test_id"])

    for kind in ("webcam", "screen"):
        r = client.post(
            "/api/proctor/recording/chunk",
            data={"attempt_id": str(attempt_id), "chunk_index": "0", "kind": kind,
                  "chunk": (io.BytesIO(b"fake video bytes"), "chunk_0.webm")},
            content_type="multipart/form-data",
        )
        assert r.status_code == 200

    with app.app_context():
        from app.models import Recording
        recs = Recording.query.filter_by(attempt_id=attempt_id).all()
        kinds = sorted(r.kind for r in recs)
        assert kinds == ["screen", "webcam"]


def test_answer_change_logged_on_autosave(client, app, attempt_setup):
    test_id = attempt_setup["test_id"]
    attempt_id = _start_attempt(client, app, test_id)

    with app.app_context():
        from app.models import Question
        q1 = Question.query.filter_by(test_id=test_id).order_by(Question.id).first()

    client.post(
        f"/student/attempts/{attempt_id}/autosave",
        data={f"q_{q1.id}": "a"},
    )
    with app.app_context():
        from app.models import AnswerEvent, Attempt
        attempt = Attempt.query.get(attempt_id)
        events = AnswerEvent.query.filter_by(attempt_id=attempt_id).all()
        assert len(events) == 1
        assert events[0].action == "first_answered"

    # Autosaving the same value again should NOT log a second event.
    client.post(
        f"/student/attempts/{attempt_id}/autosave",
        data={f"q_{q1.id}": "a"},
    )
    with app.app_context():
        from app.models import AnswerEvent
        assert AnswerEvent.query.filter_by(attempt_id=attempt_id).count() == 1

    # Changing the answer logs a second, "changed" event.
    client.post(
        f"/student/attempts/{attempt_id}/autosave",
        data={f"q_{q1.id}": "b"},
    )
    with app.app_context():
        from app.models import AnswerEvent
        events = AnswerEvent.query.filter_by(attempt_id=attempt_id).order_by(AnswerEvent.id).all()
        assert len(events) == 2
        assert events[1].action == "changed"


def test_build_timeline_includes_answers_and_split_segments(client, app, attempt_setup):
    test_id = attempt_setup["test_id"]
    attempt_id = _start_attempt(client, app, test_id)

    with app.app_context():
        from app.models import Question
        q1 = Question.query.filter_by(test_id=test_id).order_by(Question.id).first()
    client.post(f"/student/attempts/{attempt_id}/autosave", data={f"q_{q1.id}": "a"})

    for kind in ("webcam", "screen"):
        client.post(
            "/api/proctor/recording/chunk",
            data={"attempt_id": str(attempt_id), "chunk_index": "0", "kind": kind,
                  "chunk": (io.BytesIO(b"fake video bytes"), "chunk_0.webm")},
            content_type="multipart/form-data",
        )

    with app.test_request_context():
        from app.models import Attempt
        from app import proctoring
        attempt = Attempt.query.get(attempt_id)
        timeline = proctoring.build_timeline(attempt)
        types = {item["type"] for item in timeline["entries"]}
        assert "answer" in types
        assert set(timeline["segments_by_kind"].keys()) == {"webcam", "screen"}
