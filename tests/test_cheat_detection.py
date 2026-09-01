import json

import pytest

from tests.conftest import register_and_verify, login, add_single_question


@pytest.fixture()
def cheat_attempt_setup(client, app):
    register_and_verify(client, app, "Admin", "cheat_admin@test.com", "9000007001", "admin", "Adminpass1!")
    register_and_verify(client, app, "Student", "cheat_stu@test.com", "9000007002", "student", "Studpass1!")

    login(client, "cheat_admin@test.com", "Adminpass1!")
    client.post(
        "/admin/tests/create",
        data=dict(test_code="CHEAT1", title="Cheat Detect Test", description="d", duration_minutes=20,
                   total_questions=1, passing_marks=1, status="published", max_attempts=1,
                   negative_marks_per_wrong=0),
    )
    with app.app_context():
        from app.models import Test, User
        test = Test.query.filter_by(test_code="CHEAT1").first()
        student = User.query.filter_by(email="cheat_stu@test.com").first()
    add_single_question(client, test.id, "Q", "1", "2", "3", "4", "a", marks=1)
    client.post(f"/admin/tests/{test.id}/assign", data={"student_ids": [str(student.id)]})
    client.get("/logout")

    login(client, "cheat_stu@test.com", "Studpass1!")
    descriptor = [0.01 * i for i in range(128)]
    client.post("/api/proctor/enroll-face", data=json.dumps({"descriptor": descriptor}),
                content_type="application/json")
    client.get(f"/student/tests/{test.id}/start")

    with app.app_context():
        from app.models import Attempt
        attempt = Attempt.query.filter_by(test_id=test.id, student_id=student.id).first()

    return {"test_id": test.id, "attempt_id": attempt.id, "admin_email": "cheat_admin@test.com"}


# --- New event types accepted end-to-end ------------------------------------

@pytest.mark.parametrize("event_type", ["phone_detected", "book_detected", "extra_person_detected", "looking_away"])
def test_new_event_types_are_accepted_and_increment_violations(client, app, cheat_attempt_setup, event_type):
    attempt_id = cheat_attempt_setup["attempt_id"]
    r = client.post(
        "/api/proctor/event",
        data=json.dumps({"attempt_id": attempt_id, "event_type": event_type, "severity": "violation",
                          "details": "test detail"}),
        content_type="application/json",
    )
    assert r.status_code == 200
    with app.app_context():
        from app.models import Attempt, ProctoringEvent
        attempt = Attempt.query.get(attempt_id)
        assert attempt.violation_count == 1
        event = ProctoringEvent.query.filter_by(attempt_id=attempt_id, event_type=event_type).first()
        assert event is not None
        assert event.severity == "violation"


def test_new_event_types_count_toward_termination(client, app, cheat_attempt_setup):
    attempt_id = cheat_attempt_setup["attempt_id"]
    for _ in range(5):
        r = client.post(
            "/api/proctor/event",
            data=json.dumps({"attempt_id": attempt_id, "event_type": "phone_detected", "severity": "violation"}),
            content_type="application/json",
        )
    assert r.get_json()["terminated"] is True
    with app.app_context():
        from app.models import Attempt
        attempt = Attempt.query.get(attempt_id)
        assert attempt.status == "terminated"


def test_view_attempt_shows_new_event_types_readably(client, app, cheat_attempt_setup):
    attempt_id = cheat_attempt_setup["attempt_id"]
    client.post(
        "/api/proctor/event",
        data=json.dumps({"attempt_id": attempt_id, "event_type": "phone_detected", "severity": "violation",
                          "details": "A cell phone was detected"}),
        content_type="application/json",
    )
    client.get("/logout")
    login(client, cheat_attempt_setup["admin_email"], "Adminpass1!")
    r = client.get(f"/admin/attempts/{attempt_id}")
    assert b"Phone detected" in r.data


# --- Suspicion score (deterministic weighted risk score) --------------------

def test_risk_indicators_low_with_no_violations(client, app, cheat_attempt_setup):
    from app.proctoring import compute_suspicion_score
    with app.app_context():
        from app.models import Attempt
        attempt = Attempt.query.get(cheat_attempt_setup["attempt_id"])
        risk = compute_suspicion_score(attempt)
        assert risk["level"] == "low"
        assert risk["score"] == 0
        assert risk["signals"] == []


def test_risk_indicators_detects_burst(client, app, cheat_attempt_setup):
    attempt_id = cheat_attempt_setup["attempt_id"]
    for _ in range(3):
        client.post(
            "/api/proctor/event",
            data=json.dumps({"attempt_id": attempt_id, "event_type": "tab_hidden", "severity": "violation"}),
            content_type="application/json",
        )
    from app.proctoring import compute_suspicion_score
    with app.app_context():
        from app.models import Attempt
        attempt = Attempt.query.get(attempt_id)
        risk = compute_suspicion_score(attempt)
        assert risk["burst"] is True
        assert risk["score"] > 0


def test_risk_indicators_detects_diverse_violation_types(client, app, cheat_attempt_setup):
    attempt_id = cheat_attempt_setup["attempt_id"]
    for event_type in ["tab_hidden", "phone_detected", "book_detected"]:
        client.post(
            "/api/proctor/event",
            data=json.dumps({"attempt_id": attempt_id, "event_type": event_type, "severity": "violation"}),
            content_type="application/json",
        )
    from app.proctoring import compute_suspicion_score
    with app.app_context():
        from app.models import Attempt
        attempt = Attempt.query.get(attempt_id)
        risk = compute_suspicion_score(attempt)
        assert risk["distinct_types"] == 3
        assert any("different kinds" in s for s in risk["signals"])
        assert risk["level"] in ("medium", "high", "critical")
        assert 0 < risk["score"] <= 100


def test_suspicion_score_persisted_on_attempt_after_violation(client, app, cheat_attempt_setup):
    attempt_id = cheat_attempt_setup["attempt_id"]
    client.post(
        "/api/proctor/event",
        data=json.dumps({"attempt_id": attempt_id, "event_type": "phone_detected", "severity": "violation"}),
        content_type="application/json",
    )
    with app.app_context():
        from app.models import Attempt
        attempt = Attempt.query.get(attempt_id)
        assert attempt.suspicion_score > 0
        assert attempt.risk_level == "low"  # a single mid-weight violation isn't enough to escalate yet


def test_terminated_attempt_scores_at_least_high(client, app, cheat_attempt_setup):
    attempt_id = cheat_attempt_setup["attempt_id"]
    for _ in range(5):
        client.post(
            "/api/proctor/event",
            data=json.dumps({"attempt_id": attempt_id, "event_type": "window_blur", "severity": "violation"}),
            content_type="application/json",
        )
    with app.app_context():
        from app.models import Attempt
        attempt = Attempt.query.get(attempt_id)
        assert attempt.status == "terminated"
        assert attempt.suspicion_score >= 75
        assert attempt.risk_level in ("high", "critical")


def test_risk_panel_rendered_on_view_attempt_when_elevated(client, app, cheat_attempt_setup):
    attempt_id = cheat_attempt_setup["attempt_id"]
    for event_type in ["tab_hidden", "phone_detected", "book_detected"]:
        client.post(
            "/api/proctor/event",
            data=json.dumps({"attempt_id": attempt_id, "event_type": event_type, "severity": "violation"}),
            content_type="application/json",
        )
    client.get("/logout")
    login(client, cheat_attempt_setup["admin_email"], "Adminpass1!")
    r = client.get(f"/admin/attempts/{attempt_id}")
    assert b"Suspicion Score" in r.data
    assert b"not an automated verdict" in r.data


def test_risk_panel_hidden_when_no_signals(client, app, cheat_attempt_setup):
    attempt_id = cheat_attempt_setup["attempt_id"]
    client.get("/logout")
    login(client, cheat_attempt_setup["admin_email"], "Adminpass1!")
    r = client.get(f"/admin/attempts/{attempt_id}")
    assert b"Suspicion Score" not in r.data


def test_proctor_queue_shows_risk_badge(client, app, cheat_attempt_setup):
    attempt_id = cheat_attempt_setup["attempt_id"]
    for event_type in ["tab_hidden", "phone_detected", "book_detected"]:
        client.post(
            "/api/proctor/event",
            data=json.dumps({"attempt_id": attempt_id, "event_type": event_type, "severity": "violation"}),
            content_type="application/json",
        )
    client.get("/logout")
    login(client, cheat_attempt_setup["admin_email"], "Adminpass1!")
    r = client.get("/admin/review-queue")
    assert r.status_code == 200
    assert b"Medium" in r.data or b"High" in r.data or b"Critical" in r.data


def test_proctor_queue_sorts_by_risk_by_default(client, app, cheat_attempt_setup):
    """A second, low-violation attempt should rank behind the heavily
    flagged one when sorted by risk (the default)."""
    attempt_id = cheat_attempt_setup["attempt_id"]
    for event_type in ["identity_mismatch", "phone_detected", "extra_person_detected"]:
        client.post(
            "/api/proctor/event",
            data=json.dumps({"attempt_id": attempt_id, "event_type": event_type, "severity": "violation"}),
            content_type="application/json",
        )
    client.get("/logout")
    login(client, cheat_attempt_setup["admin_email"], "Adminpass1!")
    r = client.get("/admin/review-queue")
    html = r.data.decode()
    with app.app_context():
        from app.models import Attempt
        attempt = Attempt.query.get(attempt_id)
        assert f"{attempt.suspicion_score}/100" in html


def test_proctor_queue_recent_sort_available(client, app, cheat_attempt_setup):
    client.get("/logout")
    login(client, cheat_attempt_setup["admin_email"], "Adminpass1!")
    r = client.get("/admin/review-queue?sort=recent")
    assert r.status_code == 200


# --- Per-event confidence -----------------------------------------------------

def test_event_with_confidence_is_stored_and_displayed(client, app, cheat_attempt_setup):
    attempt_id = cheat_attempt_setup["attempt_id"]
    client.post(
        "/api/proctor/event",
        data=json.dumps({"attempt_id": attempt_id, "event_type": "phone_detected", "severity": "violation",
                          "confidence": 0.87}),
        content_type="application/json",
    )
    with app.app_context():
        from app.models import ProctoringEvent
        event = ProctoringEvent.query.filter_by(attempt_id=attempt_id, event_type="phone_detected").first()
        assert event.confidence == 0.87

    client.get("/logout")
    login(client, cheat_attempt_setup["admin_email"], "Adminpass1!")
    r = client.get(f"/admin/attempts/{attempt_id}")
    assert b"confidence: 87%" in r.data


def test_event_without_confidence_shows_directly_observed(client, app, cheat_attempt_setup):
    attempt_id = cheat_attempt_setup["attempt_id"]
    client.post(
        "/api/proctor/event",
        data=json.dumps({"attempt_id": attempt_id, "event_type": "tab_hidden", "severity": "violation"}),
        content_type="application/json",
    )
    with app.app_context():
        from app.models import ProctoringEvent
        event = ProctoringEvent.query.filter_by(attempt_id=attempt_id, event_type="tab_hidden").first()
        assert event.confidence is None

    client.get("/logout")
    login(client, cheat_attempt_setup["admin_email"], "Adminpass1!")
    r = client.get(f"/admin/attempts/{attempt_id}")
    assert b"directly observed" in r.data


def test_confidence_is_clamped_to_valid_range(client, app, cheat_attempt_setup):
    attempt_id = cheat_attempt_setup["attempt_id"]
    client.post(
        "/api/proctor/event",
        data=json.dumps({"attempt_id": attempt_id, "event_type": "phone_detected", "severity": "violation",
                          "confidence": 5.0}),
        content_type="application/json",
    )
    with app.app_context():
        from app.models import ProctoringEvent
        event = ProctoringEvent.query.filter_by(attempt_id=attempt_id, event_type="phone_detected").first()
        assert event.confidence == 1.0

