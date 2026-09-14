import json

from tests.conftest import register_and_verify, login, add_single_question


def _setup_test(client, app, admin_email, student_email, test_code):
    register_and_verify(client, app, "Admin", admin_email, "9166600001", "admin", "Adminpass1!")
    register_and_verify(client, app, "Student", student_email, "9166600002", "student", "Studpass1!")
    login(client, admin_email, "Adminpass1!")
    client.post("/admin/tests/create", data=dict(
        test_code=test_code, title="Access Test", description="d", duration_minutes=20,
        total_questions=1, passing_marks=1, status="published", max_attempts=1,
        negative_marks_per_wrong=0))
    with app.app_context():
        from app.models import Test, User
        test_id = Test.query.filter_by(test_code=test_code).first().id
        student_id = User.query.filter_by(email=student_email).first().id
    add_single_question(client, test_id, "2+2=?", "3", "4", "5", "6", "b", marks=1)
    client.post(f"/admin/tests/{test_id}/assign", data={"student_ids": [str(student_id)]})
    return test_id, student_id


def test_ip_allowlist_settings_page_saves(client, app):
    register_and_verify(client, app, "Admin", "ipadmin@test.com", "9166600010", "admin", "Adminpass1!")
    login(client, "ipadmin@test.com", "Adminpass1!")

    r = client.post("/admin/access-control", data={
        "ip_ranges": "203.0.113.0/24\n198.51.100.42/32",
        "sso_domain": "myschool.edu",
    }, follow_redirects=True)
    assert r.status_code == 200
    assert b"Access control settings updated" in r.data

    with app.app_context():
        from app.models import User
        org = User.query.filter_by(email="ipadmin@test.com").first().organization
        ranges = json.loads(org.ip_allowlist)
        assert ranges == ["203.0.113.0/24", "198.51.100.42/32"]
        assert org.sso_domain == "myschool.edu"


def test_invalid_cidr_is_rejected(client, app):
    register_and_verify(client, app, "Admin", "ipadmin2@test.com", "9166600011", "admin", "Adminpass1!")
    login(client, "ipadmin2@test.com", "Adminpass1!")
    r = client.post("/admin/access-control", data={"ip_ranges": "not-an-ip", "sso_domain": ""}, follow_redirects=True)
    assert b"don&#39;t look like valid IP ranges" in r.data or b"valid IP ranges" in r.data
    with app.app_context():
        from app.models import User
        org = User.query.filter_by(email="ipadmin2@test.com").first().organization
        assert org.ip_allowlist is None


def test_exam_blocked_when_ip_outside_allowlist(client, app):
    test_id, student_id = _setup_test(client, app, "ipa3@test.com", "ips3@test.com", "IPBLOCK1")

    login(client, "ipa3@test.com", "Adminpass1!")
    client.post("/admin/access-control", data={"ip_ranges": "203.0.113.0/24", "sso_domain": ""})
    with app.app_context():
        from app.models import Test
        t = Test.query.get(test_id)
        t.enforce_ip_allowlist = True
        from app import db
        db.session.commit()

    client.get("/logout")
    login(client, "ips3@test.com", "Studpass1!")
    descriptor = [0.01 * i for i in range(128)]
    client.post("/api/proctor/enroll-face", data=json.dumps({"descriptor": descriptor}), content_type="application/json")
    # The Flask test client's default remote_addr is 127.0.0.1, which is
    # outside the 203.0.113.0/24 allowlist configured above.
    r = client.get(f"/student/tests/{test_id}/start", follow_redirects=True)
    assert b"could not be accessed from your current network" in r.data

    with app.app_context():
        from app.models import Attempt, ProctoringEvent
        attempt = Attempt.query.filter_by(test_id=test_id).order_by(Attempt.id.desc()).first()
        assert attempt.status == "terminated"
        ev = ProctoringEvent.query.filter_by(attempt_id=attempt.id, event_type="ip_out_of_range").first()
        assert ev is not None


def test_exam_allowed_when_ip_inside_allowlist(client, app):
    test_id, student_id = _setup_test(client, app, "ipa4@test.com", "ips4@test.com", "IPALLOW1")

    login(client, "ipa4@test.com", "Adminpass1!")
    # 127.0.0.0/8 covers the test client's default remote_addr (127.0.0.1).
    client.post("/admin/access-control", data={"ip_ranges": "127.0.0.0/8", "sso_domain": ""})
    with app.app_context():
        from app.models import Test
        from app import db
        t = Test.query.get(test_id)
        t.enforce_ip_allowlist = True
        db.session.commit()

    client.get("/logout")
    login(client, "ips4@test.com", "Studpass1!")
    descriptor = [0.01 * i for i in range(128)]
    client.post("/api/proctor/enroll-face", data=json.dumps({"descriptor": descriptor}), content_type="application/json")
    r = client.get(f"/student/tests/{test_id}/start")
    assert r.status_code == 200
    assert b"could not be accessed" not in r.data

    with app.app_context():
        from app.models import Attempt
        attempt = Attempt.query.filter_by(test_id=test_id).order_by(Attempt.id.desc()).first()
        assert attempt.status == "in_progress"


def test_ip_check_does_nothing_when_not_enforced(client, app):
    """A test that never opts in (enforce_ip_allowlist False, the
    default) should start normally even with an org allowlist configured
    that would otherwise block it — enforcement is per-test, not
    org-wide-automatic."""
    test_id, student_id = _setup_test(client, app, "ipa5@test.com", "ips5@test.com", "IPNOENF1")
    login(client, "ipa5@test.com", "Adminpass1!")
    client.post("/admin/access-control", data={"ip_ranges": "203.0.113.0/24", "sso_domain": ""})

    client.get("/logout")
    login(client, "ips5@test.com", "Studpass1!")
    descriptor = [0.01 * i for i in range(128)]
    client.post("/api/proctor/enroll-face", data=json.dumps({"descriptor": descriptor}), content_type="application/json")
    r = client.get(f"/student/tests/{test_id}/start")
    assert r.status_code == 200
    with app.app_context():
        from app.models import Attempt
        attempt = Attempt.query.filter_by(test_id=test_id).order_by(Attempt.id.desc()).first()
        assert attempt.status == "in_progress"


def test_geofence_flags_but_does_not_block(client, app):
    test_id, student_id = _setup_test(client, app, "geoa1@test.com", "geos1@test.com", "GEO1")

    with app.app_context():
        from app.models import Test
        from app import db
        t = Test.query.get(test_id)
        # Center on Bengaluru, small radius — the coordinates the test
        # will report (New York) are far outside it.
        t.geofence_lat, t.geofence_lng, t.geofence_radius_km = 12.9716, 77.5946, 5.0
        db.session.commit()

    client.get("/logout")
    login(client, "geos1@test.com", "Studpass1!")
    descriptor = [0.01 * i for i in range(128)]
    client.post("/api/proctor/enroll-face", data=json.dumps({"descriptor": descriptor}), content_type="application/json")
    client.get(f"/student/tests/{test_id}/start")
    with app.app_context():
        from app.models import Attempt
        attempt_id = Attempt.query.filter_by(test_id=test_id).order_by(Attempt.id.desc()).first().id

    r = client.post("/api/proctor/geo-check", data=json.dumps({
        "attempt_id": attempt_id, "lat": 40.7128, "lng": -74.0060,
    }), content_type="application/json")
    assert r.status_code == 200
    data = r.get_json()
    assert data["terminated"] is False  # a signal, never a hard block

    with app.app_context():
        from app.models import Attempt, ProctoringEvent
        attempt = Attempt.query.get(attempt_id)
        assert attempt.status == "in_progress"  # still allowed to continue
        ev = ProctoringEvent.query.filter_by(attempt_id=attempt_id, event_type="location_out_of_range").first()
        assert ev is not None


def test_geofence_inside_radius_logs_nothing(client, app):
    test_id, student_id = _setup_test(client, app, "geoa2@test.com", "geos2@test.com", "GEO2")
    with app.app_context():
        from app.models import Test
        from app import db
        t = Test.query.get(test_id)
        t.geofence_lat, t.geofence_lng, t.geofence_radius_km = 12.9716, 77.5946, 50.0
        db.session.commit()

    client.get("/logout")
    login(client, "geos2@test.com", "Studpass1!")
    descriptor = [0.01 * i for i in range(128)]
    client.post("/api/proctor/enroll-face", data=json.dumps({"descriptor": descriptor}), content_type="application/json")
    client.get(f"/student/tests/{test_id}/start")
    with app.app_context():
        from app.models import Attempt
        attempt_id = Attempt.query.filter_by(test_id=test_id).order_by(Attempt.id.desc()).first().id

    # A point ~20km away — inside the 50km radius.
    client.post("/api/proctor/geo-check", data=json.dumps({
        "attempt_id": attempt_id, "lat": 13.05, "lng": 77.58,
    }), content_type="application/json")

    with app.app_context():
        from app.models import ProctoringEvent
        ev = ProctoringEvent.query.filter_by(attempt_id=attempt_id, event_type="location_out_of_range").first()
        assert ev is None
