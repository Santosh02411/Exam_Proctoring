from datetime import datetime, timedelta

from tests._review_helpers import make_world, other_org_admin, start_attempt, get_attempt


def _roster(client):
    r = client.get("/admin/live/data")
    assert r.status_code == 200
    return r.get_json()["attempts"]


def _hb(w, attempt_id, n=1):
    r = w["students"][n]["client"].get(f"/student/attempts/{attempt_id}/heartbeat")
    assert r.status_code == 200
    return r.get_json()


def test_roster_lists_only_in_progress_attempts(client, app):
    w = make_world(app, "lm1")
    a1 = start_attempt(app, w, 1)
    a2 = start_attempt(app, w, 2)
    w["students"][2]["client"].post(f"/student/attempts/{a2}/submit", data={})

    rows = _roster(w["admin"])
    assert [r["attempt_id"] for r in rows] == [a1]
    row = rows[0]
    assert row["student_name"] == "Student1"
    assert row["connection"] == "online"
    assert row["violation_count"] == 0
    assert row["last_event"] is None
    assert row["view_url"].endswith(f"/admin/attempts/{a1}")


def test_roster_orders_highest_risk_first_and_shows_latest_event(client, app):
    import json
    w = make_world(app, "lm2")
    a1 = start_attempt(app, w, 1)
    a2 = start_attempt(app, w, 2)
    w["students"][2]["client"].post(
        "/api/proctor/event",
        data=json.dumps({"attempt_id": a2, "event_type": "tab_hidden", "severity": "violation"}),
        content_type="application/json")
    rows = _roster(w["proctor"])
    assert [r["attempt_id"] for r in rows] == [a2, a1]
    assert rows[0]["violation_count"] == 1
    assert rows[0]["last_event"]["severity"] == "violation"


def test_stale_heartbeat_shows_no_signal(client, app):
    w = make_world(app, "lm3")
    a1 = start_attempt(app, w, 1)
    from app import db
    from app.models import Attempt
    with app.app_context():
        Attempt.query.get(a1).session_last_seen_at = datetime.utcnow() - timedelta(minutes=5)
        db.session.commit()
    assert _roster(w["admin"])[0]["connection"] == "no_signal"


def test_roster_and_actions_are_role_and_org_scoped(client, app):
    w = make_world(app, "lm4")
    a1 = start_attempt(app, w, 1)
    student = w["students"][2]["client"]
    assert student.get("/admin/live").status_code == 403
    assert student.get("/admin/live/data").status_code == 403
    assert student.post(f"/admin/attempts/{a1}/message", data={"body": "hi"}).status_code == 403
    assert student.post(f"/admin/attempts/{a1}/terminate", data={"reason": "because"}).status_code == 403

    outsider = other_org_admin(app, "lm4")
    assert _roster(outsider) == []
    assert outsider.post(f"/admin/attempts/{a1}/message", data={"body": "hi"}).status_code == 403
    assert outsider.post(f"/admin/attempts/{a1}/terminate", data={"reason": "because"}).status_code == 403
    assert get_attempt(app, a1)["status"] == "in_progress"


def test_live_page_renders_for_proctor(client, app):
    w = make_world(app, "lm5")
    r = w["proctor"].get("/admin/live")
    assert r.status_code == 200
    assert b"Live Monitor" in r.data


def test_message_is_delivered_once_via_heartbeat(client, app):
    w = make_world(app, "lm6")
    a1 = start_attempt(app, w, 1)
    assert _hb(w, a1)["proctor_messages"] == []

    r = w["proctor"].post(f"/admin/attempts/{a1}/message", data={"body": "Please move your camera so we can see your desk."})
    assert r.status_code == 200 and r.get_json()["ok"] is True

    msgs = _hb(w, a1)["proctor_messages"]
    assert len(msgs) == 1
    assert msgs[0]["body"] == "Please move your camera so we can see your desk."
    # exactly once
    assert _hb(w, a1)["proctor_messages"] == []
    # a message to student 1 is never delivered to student 2
    a2 = start_attempt(app, w, 2)
    assert _hb(w, a2, 2)["proctor_messages"] == []


def test_message_is_recorded_on_timeline_without_counting_as_violation(client, app):
    w = make_world(app, "lm7")
    a1 = start_attempt(app, w, 1)
    w["admin"].post(f"/admin/attempts/{a1}/message", data={"body": "Eyes on your own screen, please."})
    from app.models import ProctoringEvent
    with app.app_context():
        ev = ProctoringEvent.query.filter_by(attempt_id=a1, event_type="proctor_message").one()
        assert ev.severity == "info"
        assert "Eyes on your own screen" in ev.details
    state = get_attempt(app, a1)
    assert state["violations"] == 0 and state["risk"] == 0
    # the admin's attempt page (timeline) still renders with the new event type present
    assert w["admin"].get(f"/admin/attempts/{a1}").status_code == 200


def test_message_validation(client, app):
    w = make_world(app, "lm8")
    a1 = start_attempt(app, w, 1)
    for bad in ("", "   ", "x" * 301):
        r = w["proctor"].post(f"/admin/attempts/{a1}/message", data={"body": bad})
        assert r.status_code == 400
        assert r.get_json()["ok"] is False
    from app.models import ProctorMessage
    with app.app_context():
        assert ProctorMessage.query.count() == 0


def test_cannot_message_or_terminate_finished_attempt(client, app):
    w = make_world(app, "lm9")
    a1 = start_attempt(app, w, 1)
    w["students"][1]["client"].post(f"/student/attempts/{a1}/submit", data={})
    assert w["proctor"].post(f"/admin/attempts/{a1}/message", data={"body": "too late"}).status_code == 409
    assert w["proctor"].post(f"/admin/attempts/{a1}/terminate", data={"reason": "too late"}).status_code == 409
    assert get_attempt(app, a1)["status"] == "submitted"


def test_terminate_ends_attempt_like_an_automatic_termination(client, app):
    w = make_world(app, "lm10")
    a1 = start_attempt(app, w, 1)
    r = w["proctor"].post(f"/admin/attempts/{a1}/terminate", data={"reason": "Caught reading from a second device"})
    assert r.status_code == 200 and r.get_json()["ok"] is True

    state = get_attempt(app, a1)
    assert state["status"] == "terminated"
    assert state["reason"] == "Ended by a proctor: Caught reading from a second device"
    assert state["submitted_at"] is not None
    assert state["violations"] == 0  # a human decision, not a counted violation

    # The student's own tab learns about it on its next heartbeat and is sent to the result page.
    hb = _hb(w, a1)
    assert hb["status"] == "terminated" and hb["remaining_seconds"] is None
    result = w["students"][1]["client"].get(f"/student/attempts/{a1}/result")
    assert b"Ended by a proctor" in result.data
    assert b"Appeal this outcome" in result.data  # and the human decision is appealable

    # Off the roster, recorded on the timeline.
    assert _roster(w["admin"]) == []
    from app.models import ProctoringEvent
    with app.app_context():
        assert ProctoringEvent.query.filter_by(attempt_id=a1, event_type="proctor_terminated").count() == 1


def test_terminate_requires_a_reason(client, app):
    w = make_world(app, "lm11")
    a1 = start_attempt(app, w, 1)
    for bad in ("", "ab"):
        assert w["proctor"].post(f"/admin/attempts/{a1}/terminate", data={"reason": bad}).status_code == 400
    assert get_attempt(app, a1)["status"] == "in_progress"


def test_dashboard_nav_links_to_new_pages(client, app):
    w = make_world(app, "lm12")
    admin_page = w["admin"].get("/admin/dashboard").data
    assert b"/admin/live" in admin_page and b"/admin/appeals" in admin_page
    proctor_page = w["proctor"].get("/admin/review-queue").data
    assert b"/admin/live" in proctor_page and b"/admin/appeals" in proctor_page
