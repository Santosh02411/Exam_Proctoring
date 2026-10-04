from datetime import datetime, timedelta

from tests._review_helpers import make_world, other_org_admin, start_attempt, terminate_by_violations, get_attempt


def _terminated(app, tag, **kw):
    w = make_world(app, tag, **kw)
    attempt_id = start_attempt(app, w)
    terminate_by_violations(w, attempt_id)
    assert get_attempt(app, attempt_id)["status"] == "terminated"
    return w, attempt_id


def _appeal(w, attempt_id, reason="My webcam kept dropping out and a family member walked in briefly."):
    return w["students"][1]["client"].post(
        f"/student/attempts/{attempt_id}/appeal", data={"reason": reason}, follow_redirects=True)


def _appeal_row(app, attempt_id):
    from app.models import Appeal
    with app.app_context():
        a = Appeal.query.filter_by(attempt_id=attempt_id).first()
        return None if a is None else {"id": a.id, "status": a.status, "note": a.admin_note}


def test_result_page_offers_appeal_for_terminated_attempt(client, app):
    w, attempt_id = _terminated(app, "ap1")
    r = w["students"][1]["client"].get(f"/student/attempts/{attempt_id}/result")
    assert b"Appeal this outcome" in r.data


def test_student_can_appeal_and_admin_is_notified(client, app):
    w, attempt_id = _terminated(app, "ap2")
    r = _appeal(w, attempt_id)
    assert r.status_code == 200
    assert b"appeal has been submitted" in r.data
    assert _appeal_row(app, attempt_id)["status"] == "pending"

    from app.models import NotificationLog, User
    with app.app_context():
        admin = User.query.filter_by(email=w["admin_email"]).first()
        assert NotificationLog.query.filter_by(user_id=admin.id, notif_type="appeal_submitted").count() == 1


def test_appeal_reason_must_be_substantive(client, app):
    w, attempt_id = _terminated(app, "ap3")
    r = _appeal(w, attempt_id, reason="unfair")
    assert b"at least 20 characters" in r.data
    assert _appeal_row(app, attempt_id) is None


def test_only_one_appeal_per_attempt(client, app):
    w, attempt_id = _terminated(app, "ap4")
    _appeal(w, attempt_id)
    r = _appeal(w, attempt_id, reason="Trying to submit a second appeal for the very same attempt.")
    assert b"already appealed" in r.data or b"Pending review" in r.data
    from app.models import Appeal
    with app.app_context():
        assert Appeal.query.filter_by(attempt_id=attempt_id).count() == 1


def test_clean_attempt_cannot_be_appealed(client, app):
    w = make_world(app, "ap5")
    attempt_id = start_attempt(app, w)
    c = w["students"][1]["client"]
    c.post(f"/student/attempts/{attempt_id}/submit", data={})
    assert get_attempt(app, attempt_id)["status"] == "submitted"
    r = c.get(f"/student/attempts/{attempt_id}/appeal")
    assert b"no proctoring outcome" in r.data
    c.post(f"/student/attempts/{attempt_id}/appeal", data={"reason": "x" * 40})
    assert _appeal_row(app, attempt_id) is None


def test_in_progress_attempt_cannot_be_appealed(client, app):
    w = make_world(app, "ap6")
    attempt_id = start_attempt(app, w)
    w["students"][1]["client"].post(f"/student/attempts/{attempt_id}/appeal", data={"reason": "x" * 40})
    assert _appeal_row(app, attempt_id) is None


def test_appeal_window_closes(client, app):
    w, attempt_id = _terminated(app, "ap7")
    from app import db
    from app.models import Attempt
    with app.app_context():
        Attempt.query.get(attempt_id).submitted_at = datetime.utcnow() - timedelta(days=30)
        db.session.commit()
    r = _appeal(w, attempt_id)
    assert b"appeal window" in r.data
    assert _appeal_row(app, attempt_id) is None


def test_other_student_cannot_appeal_someone_elses_attempt(client, app):
    w, attempt_id = _terminated(app, "ap8")
    r = w["students"][2]["client"].get(f"/student/attempts/{attempt_id}/appeal")
    assert r.status_code == 403
    r = w["students"][2]["client"].post(f"/student/attempts/{attempt_id}/appeal", data={"reason": "x" * 40})
    assert r.status_code == 403
    assert _appeal_row(app, attempt_id) is None


def test_grant_gives_extra_attempt_and_notifies_student(client, app):
    w, attempt_id = _terminated(app, "ap9")
    _appeal(w, attempt_id)
    appeal_id = _appeal_row(app, attempt_id)["id"]

    r = w["admin"].post(f"/admin/appeals/{appeal_id}/grant", data={"admin_note": "Webcam fault confirmed."},
                        follow_redirects=True)
    assert r.status_code == 200
    assert _appeal_row(app, attempt_id) == {"id": appeal_id, "status": "granted", "note": "Webcam fault confirmed."}

    from app.models import TestEligibility, NotificationLog, Attempt
    with app.app_context():
        elig = TestEligibility.query.filter_by(test_id=w["test_id"], student_id=w["students"][1]["id"]).first()
        assert elig.extra_attempts == 1
        assert NotificationLog.query.filter_by(user_id=w["students"][1]["id"], notif_type="appeal_resolved").count() == 1
    # the original attempt's record is untouched
    assert get_attempt(app, attempt_id)["status"] == "terminated"

    # ...and the extra attempt is real: with max_attempts=1 the student could not retake before.
    start_attempt(app, w)
    with app.app_context():
        assert Attempt.query.filter_by(test_id=w["test_id"], student_id=w["students"][1]["id"]).count() == 2


def test_deny_leaves_attempts_unchanged(client, app):
    w, attempt_id = _terminated(app, "ap10")
    _appeal(w, attempt_id)
    appeal_id = _appeal_row(app, attempt_id)["id"]
    w["admin"].post(f"/admin/appeals/{appeal_id}/deny", data={"admin_note": "Footage confirms the violations."})
    assert _appeal_row(app, attempt_id)["status"] == "denied"
    from app.models import TestEligibility
    with app.app_context():
        elig = TestEligibility.query.filter_by(test_id=w["test_id"], student_id=w["students"][1]["id"]).first()
        assert elig.extra_attempts == 0

    r = w["students"][1]["client"].get(f"/student/attempts/{attempt_id}/appeal")
    assert b"Footage confirms the violations." in r.data


def test_appeal_cannot_be_decided_twice(client, app):
    w, attempt_id = _terminated(app, "ap11")
    _appeal(w, attempt_id)
    appeal_id = _appeal_row(app, attempt_id)["id"]
    w["admin"].post(f"/admin/appeals/{appeal_id}/grant", data={})
    r = w["admin"].post(f"/admin/appeals/{appeal_id}/grant", data={}, follow_redirects=True)
    assert b"already been decided" in r.data
    from app.models import TestEligibility
    with app.app_context():
        elig = TestEligibility.query.filter_by(test_id=w["test_id"], student_id=w["students"][1]["id"]).first()
        assert elig.extra_attempts == 1


def test_unknown_decision_is_404(client, app):
    w, attempt_id = _terminated(app, "ap12")
    _appeal(w, attempt_id)
    appeal_id = _appeal_row(app, attempt_id)["id"]
    assert w["admin"].post(f"/admin/appeals/{appeal_id}/approve-everything", data={}).status_code == 404


def test_proctor_can_review_appeals_but_students_cannot(client, app):
    w, attempt_id = _terminated(app, "ap13")
    _appeal(w, attempt_id)
    r = w["proctor"].get("/admin/appeals")
    assert r.status_code == 200
    assert b"My webcam kept dropping out" in r.data
    assert w["students"][1]["client"].get("/admin/appeals").status_code == 403
    appeal_id = _appeal_row(app, attempt_id)["id"]
    assert w["students"][1]["client"].post(f"/admin/appeals/{appeal_id}/grant", data={}).status_code == 403


def test_other_organization_cannot_see_or_decide_appeal(client, app):
    w, attempt_id = _terminated(app, "ap14")
    _appeal(w, attempt_id)
    appeal_id = _appeal_row(app, attempt_id)["id"]
    outsider = other_org_admin(app, "ap14")
    assert b"My webcam kept dropping out" not in outsider.get("/admin/appeals").data
    assert outsider.post(f"/admin/appeals/{appeal_id}/grant", data={}).status_code == 403
    assert _appeal_row(app, attempt_id)["status"] == "pending"
