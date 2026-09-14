import json

from tests.conftest import register_and_verify, login, add_single_question


def _setup(client, app, admin_email, student_email, test_code):
    register_and_verify(client, app, "Admin", admin_email, "9188800001", "admin", "Adminpass1!")
    register_and_verify(client, app, "Student", student_email, "9188800002", "student", "Studpass1!")
    login(client, admin_email, "Adminpass1!")
    client.post("/admin/tests/create", data=dict(
        test_code=test_code, title="Accommodation Test", description="d", duration_minutes=30,
        total_questions=1, passing_marks=1, status="published", max_attempts=1,
        negative_marks_per_wrong=0))
    with app.app_context():
        from app.models import Test, User
        test_id = Test.query.filter_by(test_code=test_code).first().id
        student_id = User.query.filter_by(email=student_email).first().id
    add_single_question(client, test_id, "2+2=?", "3", "4", "5", "6", "b", marks=1)
    client.post(f"/admin/tests/{test_id}/assign", data={"student_ids": [str(student_id)]})
    return test_id, student_id


def test_student_can_submit_accommodation_request(client, app):
    test_id, student_id = _setup(client, app, "acca1@test.com", "accs1@test.com", "ACC1")
    client.get("/logout")
    login(client, "accs1@test.com", "Studpass1!")
    r = client.post(f"/student/tests/{test_id}/request-accommodation", data={
        "requested_extra_minutes": "15", "reason": "I have a documented reading accommodation.",
    }, follow_redirects=True)
    assert r.status_code == 200
    assert b"sent to your test administrator" in r.data

    with app.app_context():
        from app.models import AccommodationRequest
        req = AccommodationRequest.query.filter_by(test_id=test_id, student_id=student_id).first()
        assert req is not None
        assert req.status == "pending"
        assert req.requested_extra_minutes == 15

    # Admin should have been notified.
    with app.app_context():
        from app.models import NotificationLog, User
        admin = User.query.filter_by(email="acca1@test.com").first()
        log = NotificationLog.query.filter_by(user_id=admin.id, notif_type="accommodation_requested").first()
        assert log is not None


def test_cannot_submit_second_request_while_one_pending(client, app):
    test_id, student_id = _setup(client, app, "acca2@test.com", "accs2@test.com", "ACC2")
    client.get("/logout")
    login(client, "accs2@test.com", "Studpass1!")
    client.post(f"/student/tests/{test_id}/request-accommodation", data={
        "requested_extra_minutes": "10", "reason": "First request.",
    })
    r = client.get(f"/student/tests/{test_id}/request-accommodation")
    assert b"pending request" in r.data

    with app.app_context():
        from app.models import AccommodationRequest
        count = AccommodationRequest.query.filter_by(test_id=test_id, student_id=student_id).count()
        assert count == 1


def test_admin_approve_grants_extra_time(client, app):
    test_id, student_id = _setup(client, app, "acca3@test.com", "accs3@test.com", "ACC3")
    client.get("/logout")
    login(client, "accs3@test.com", "Studpass1!")
    client.post(f"/student/tests/{test_id}/request-accommodation", data={
        "requested_extra_minutes": "20", "reason": "Extended time accommodation.",
    })
    with app.app_context():
        from app.models import AccommodationRequest
        req_id = AccommodationRequest.query.filter_by(test_id=test_id, student_id=student_id).first().id

    client.get("/logout")
    login(client, "acca3@test.com", "Adminpass1!")
    r = client.get("/admin/accommodation-requests")
    assert r.status_code == 200
    assert b"Extended time accommodation" in r.data

    r2 = client.post(f"/admin/accommodation-requests/{req_id}/approve", follow_redirects=True)
    assert b"Approved" in r2.data

    with app.app_context():
        from app.models import AccommodationRequest, TestEligibility
        req = AccommodationRequest.query.get(req_id)
        assert req.status == "approved"
        assert req.resolved_at is not None
        elig = TestEligibility.query.filter_by(test_id=test_id, student_id=student_id).first()
        assert elig.extra_time_minutes == 20

    with app.app_context():
        from app.models import NotificationLog, User
        student = User.query.filter_by(email="accs3@test.com").first()
        log = NotificationLog.query.filter_by(user_id=student.id, notif_type="accommodation_approved").first()
        assert log is not None


def test_admin_deny_with_note_does_not_grant_time(client, app):
    test_id, student_id = _setup(client, app, "acca4@test.com", "accs4@test.com", "ACC4")
    client.get("/logout")
    login(client, "accs4@test.com", "Studpass1!")
    client.post(f"/student/tests/{test_id}/request-accommodation", data={
        "requested_extra_minutes": "30", "reason": "Requesting a lot of extra time.",
    })
    with app.app_context():
        from app.models import AccommodationRequest
        req_id = AccommodationRequest.query.filter_by(test_id=test_id, student_id=student_id).first().id

    client.get("/logout")
    login(client, "acca4@test.com", "Adminpass1!")
    r = client.post(f"/admin/accommodation-requests/{req_id}/deny", data={
        "admin_note": "Please provide supporting documentation first.",
    }, follow_redirects=True)
    assert b"Request denied" in r.data

    with app.app_context():
        from app.models import AccommodationRequest, TestEligibility
        req = AccommodationRequest.query.get(req_id)
        assert req.status == "denied"
        assert req.admin_note == "Please provide supporting documentation first."
        elig = TestEligibility.query.filter_by(test_id=test_id, student_id=student_id).first()
        assert elig is None or elig.extra_time_minutes == 0

    with app.app_context():
        from app.models import NotificationLog, User
        student = User.query.filter_by(email="accs4@test.com").first()
        log = NotificationLog.query.filter_by(user_id=student.id, notif_type="accommodation_denied").first()
        assert log is not None


def test_approve_uses_max_of_existing_and_requested(client, app):
    """An admin who already manually granted more time than the student
    is now requesting shouldn't have that reduced by an approval."""
    test_id, student_id = _setup(client, app, "acca5@test.com", "accs5@test.com", "ACC5")
    with app.app_context():
        from app.models import TestEligibility
        from app import db
        elig = TestEligibility.query.filter_by(test_id=test_id, student_id=student_id).first()
        elig.extra_time_minutes = 25
        db.session.commit()

    client.get("/logout")
    login(client, "accs5@test.com", "Studpass1!")
    client.post(f"/student/tests/{test_id}/request-accommodation", data={
        "requested_extra_minutes": "10", "reason": "Smaller request than what I already have.",
    })
    with app.app_context():
        from app.models import AccommodationRequest
        req_id = AccommodationRequest.query.filter_by(test_id=test_id, student_id=student_id).first().id

    client.get("/logout")
    login(client, "acca5@test.com", "Adminpass1!")
    client.post(f"/admin/accommodation-requests/{req_id}/approve")

    with app.app_context():
        from app.models import TestEligibility
        elig = TestEligibility.query.filter_by(test_id=test_id, student_id=student_id).first()
        assert elig.extra_time_minutes == 25  # unchanged — 25 > 10


def test_cannot_resolve_already_resolved_request_twice(client, app):
    test_id, student_id = _setup(client, app, "acca6@test.com", "accs6@test.com", "ACC6")
    client.get("/logout")
    login(client, "accs6@test.com", "Studpass1!")
    client.post(f"/student/tests/{test_id}/request-accommodation", data={
        "requested_extra_minutes": "10", "reason": "Reason text.",
    })
    with app.app_context():
        from app.models import AccommodationRequest
        req_id = AccommodationRequest.query.filter_by(test_id=test_id, student_id=student_id).first().id

    client.get("/logout")
    login(client, "acca6@test.com", "Adminpass1!")
    client.post(f"/admin/accommodation-requests/{req_id}/approve")
    r = client.post(f"/admin/accommodation-requests/{req_id}/deny", follow_redirects=True)
    assert b"already been resolved" in r.data

    with app.app_context():
        from app.models import AccommodationRequest
        req = AccommodationRequest.query.get(req_id)
        assert req.status == "approved"  # the deny attempt didn't overwrite it
