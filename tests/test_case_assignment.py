import json

from tests.conftest import register_and_verify, login, add_single_question


def _flagged_attempt(client, app, admin_email, student_email, test_code):
    register_and_verify(client, app, "Admin", admin_email, "9100077001", "admin", "Adminpass1!")
    register_and_verify(client, app, "Student", student_email, "9100077002", "student", "Studpass1!")
    login(client, admin_email, "Adminpass1!")
    client.post("/admin/tests/create", data=dict(
        test_code=test_code, title="Case Test", description="d", duration_minutes=20,
        total_questions=1, passing_marks=1, status="published", max_attempts=1, negative_marks_per_wrong=0))
    with app.app_context():
        from app.models import Test, User
        test_id = Test.query.filter_by(test_code=test_code).first().id
        student_id = User.query.filter_by(email=student_email).first().id
    add_single_question(client, test_id, "2+2=?", "3", "4", "5", "6", "b", marks=1)
    client.post(f"/admin/tests/{test_id}/assign", data={"student_ids": [str(student_id)], "notify": "on"})

    client.get("/logout")
    login(client, student_email, "Studpass1!")
    descriptor = [0.01 * i for i in range(128)]
    client.post("/api/proctor/enroll-face", data=json.dumps({"descriptor": descriptor}), content_type="application/json")
    client.get(f"/student/tests/{test_id}/start")
    with app.app_context():
        from app.models import Attempt
        attempt_id = Attempt.query.filter_by(test_id=test_id).order_by(Attempt.id.desc()).first().id
    client.post("/api/proctor/event", data=json.dumps({
        "attempt_id": attempt_id, "event_type": "copy_paste_attempt", "severity": "violation"}),
        content_type="application/json")
    client.get("/logout")
    return attempt_id


def test_review_queue_shows_unassigned_by_default(client, app):
    attempt_id = _flagged_attempt(client, app, "caa1@test.com", "cas1@test.com", "CASE1")
    login(client, "caa1@test.com", "Adminpass1!")
    r = client.get("/admin/review-queue")
    assert r.status_code == 200
    assert b"Unassigned" in r.data


def test_assign_case_to_proctor(client, app):
    attempt_id = _flagged_attempt(client, app, "caa2@test.com", "cas2@test.com", "CASE2")
    login(client, "caa2@test.com", "Adminpass1!")
    with app.app_context():
        from app.models import User
        admin_id = User.query.filter_by(email="caa2@test.com").first().id

    r = client.post(f"/admin/attempts/{attempt_id}/assign-proctor", data={"proctor_id": str(admin_id)}, follow_redirects=True)
    assert r.status_code == 200

    with app.app_context():
        from app.models import Attempt
        attempt = Attempt.query.get(attempt_id)
        assert attempt.assigned_proctor_id == admin_id


def test_my_cases_filter_only_shows_assigned_to_current_user(client, app):
    attempt_id = _flagged_attempt(client, app, "caa3@test.com", "cas3@test.com", "CASE3")
    login(client, "caa3@test.com", "Adminpass1!")
    with app.app_context():
        from app.models import User
        admin_id = User.query.filter_by(email="caa3@test.com").first().id

    # Unassigned initially — shouldn't show up under "My Cases".
    r = client.get("/admin/review-queue?mine=1")
    assert b"Case Test" not in r.data

    client.post(f"/admin/attempts/{attempt_id}/assign-proctor", data={"proctor_id": str(admin_id)})
    r2 = client.get("/admin/review-queue?mine=1")
    assert b"Case Test" in r2.data


def test_clearing_assignment_sets_unassigned(client, app):
    attempt_id = _flagged_attempt(client, app, "caa4@test.com", "cas4@test.com", "CASE4")
    login(client, "caa4@test.com", "Adminpass1!")
    with app.app_context():
        from app.models import User
        admin_id = User.query.filter_by(email="caa4@test.com").first().id
    client.post(f"/admin/attempts/{attempt_id}/assign-proctor", data={"proctor_id": str(admin_id)})

    client.post(f"/admin/attempts/{attempt_id}/assign-proctor", data={"proctor_id": ""})
    with app.app_context():
        from app.models import Attempt
        assert Attempt.query.get(attempt_id).assigned_proctor_id is None


def test_cannot_assign_proctor_from_other_org(client, app):
    attempt_id = _flagged_attempt(client, app, "caa5@test.com", "cas5@test.com", "CASE5")
    login(client, "caa5@test.com", "Adminpass1!")

    client.get("/logout")
    register_and_verify(client, app, "OutsideAdmin", "caa5b@test.com", "9100077003", "admin", "Adminpass1!")
    with app.app_context():
        from app.models import User, Organization
        from app import db
        other_org = Organization(name="Case Other Org", slug="case-other-org", status="active")
        db.session.add(other_org)
        db.session.flush()
        outsider = User.query.filter_by(email="caa5b@test.com").first()
        outsider.org_id = other_org.id
        db.session.commit()
        outsider_id = outsider.id

    login(client, "caa5@test.com", "Adminpass1!")
    r = client.post(f"/admin/attempts/{attempt_id}/assign-proctor", data={"proctor_id": str(outsider_id)}, follow_redirects=True)

    with app.app_context():
        from app.models import Attempt
        # The outsider isn't a valid reviewer for this org, so the
        # assignment should not have gone through as requested.
        attempt = Attempt.query.get(attempt_id)
        assert attempt.assigned_proctor_id != outsider_id
