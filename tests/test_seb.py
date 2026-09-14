import json
import plistlib

from tests.conftest import register_and_verify, login, add_single_question


def _setup_test(client, app, admin_email, student_email, test_code):
    register_and_verify(client, app, "Admin", admin_email, "9155500001", "admin", "Adminpass1!")
    register_and_verify(client, app, "Student", student_email, "9155500002", "student", "Studpass1!")
    login(client, admin_email, "Adminpass1!")
    client.post("/admin/tests/create", data=dict(
        test_code=test_code, title="SEB Test", description="d", duration_minutes=20,
        total_questions=1, passing_marks=1, status="published", max_attempts=1,
        negative_marks_per_wrong=0))
    with app.app_context():
        from app.models import Test, User
        test_id = Test.query.filter_by(test_code=test_code).first().id
        student_id = User.query.filter_by(email=student_email).first().id
    add_single_question(client, test_id, "2+2=?", "3", "4", "5", "6", "b", marks=1)
    client.post(f"/admin/tests/{test_id}/assign", data={"student_ids": [str(student_id)]})
    return test_id, student_id


def test_download_seb_config_is_a_valid_plist(client, app):
    test_id, _ = _setup_test(client, app, "seba1@test.com", "sebs1@test.com", "SEB1")
    login(client, "seba1@test.com", "Adminpass1!")
    r = client.get(f"/admin/tests/{test_id}/seb-config")
    assert r.status_code == 200
    assert r.headers["Content-Disposition"].startswith("attachment;")
    assert "SEB1.seb" in r.headers["Content-Disposition"]

    config = plistlib.loads(r.data)
    assert config["startURL"].endswith(f"/student/tests/{test_id}/start")
    assert config["showTaskBar"] is False
    assert config["allowedDisplaysMaxNumber"] == 1
    assert config["sendBrowserExamKey"] is True


def test_require_seb_flags_non_seb_requests(client, app):
    test_id, student_id = _setup_test(client, app, "seba2@test.com", "sebs2@test.com", "SEB2")
    login(client, "seba2@test.com", "Adminpass1!")
    with app.app_context():
        from app.models import Test
        from app import db
        t = Test.query.get(test_id)
        t.require_seb = True
        db.session.commit()

    client.get("/logout")
    login(client, "sebs2@test.com", "Studpass1!")
    descriptor = [0.01 * i for i in range(128)]
    client.post("/api/proctor/enroll-face", data=json.dumps({"descriptor": descriptor}), content_type="application/json")
    # Plain test-client request, no SEB-identifying headers at all.
    r = client.get(f"/student/tests/{test_id}/start")
    assert r.status_code == 200  # a soft signal, never blocks

    with app.app_context():
        from app.models import Attempt, ProctoringEvent
        attempt = Attempt.query.filter_by(test_id=test_id).order_by(Attempt.id.desc()).first()
        assert attempt.status == "in_progress"
        ev = ProctoringEvent.query.filter_by(attempt_id=attempt.id, event_type="seb_not_detected").first()
        assert ev is not None
        assert ev.severity == "violation"


def test_require_seb_passes_with_seb_user_agent(client, app):
    test_id, student_id = _setup_test(client, app, "seba3@test.com", "sebs3@test.com", "SEB3")
    login(client, "seba3@test.com", "Adminpass1!")
    with app.app_context():
        from app.models import Test
        from app import db
        t = Test.query.get(test_id)
        t.require_seb = True
        db.session.commit()

    client.get("/logout")
    login(client, "sebs3@test.com", "Studpass1!")
    descriptor = [0.01 * i for i in range(128)]
    client.post("/api/proctor/enroll-face", data=json.dumps({"descriptor": descriptor}), content_type="application/json")
    client.get(f"/student/tests/{test_id}/start", headers={"User-Agent": "Mozilla/5.0 SEB/3.6.2"})

    with app.app_context():
        from app.models import Attempt, ProctoringEvent
        attempt = Attempt.query.filter_by(test_id=test_id).order_by(Attempt.id.desc()).first()
        ev = ProctoringEvent.query.filter_by(attempt_id=attempt.id, event_type="seb_not_detected").first()
        assert ev is None


def test_no_seb_check_when_not_required(client, app):
    test_id, student_id = _setup_test(client, app, "seba4@test.com", "sebs4@test.com", "SEB4")
    client.get("/logout")
    login(client, "sebs4@test.com", "Studpass1!")
    descriptor = [0.01 * i for i in range(128)]
    client.post("/api/proctor/enroll-face", data=json.dumps({"descriptor": descriptor}), content_type="application/json")
    client.get(f"/student/tests/{test_id}/start")
    with app.app_context():
        from app.models import Attempt, ProctoringEvent
        attempt = Attempt.query.filter_by(test_id=test_id).order_by(Attempt.id.desc()).first()
        ev = ProctoringEvent.query.filter_by(attempt_id=attempt.id, event_type="seb_not_detected").first()
        assert ev is None
