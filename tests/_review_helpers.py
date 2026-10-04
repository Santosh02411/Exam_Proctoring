"""Shared setup for the Appeals and Live Monitor tests: one organization
with an admin, a proctor and two students, one published single-question
test assigned to both students, plus helpers to start/terminate attempts
and to stand up a second, unrelated organization's admin."""
import json
from datetime import datetime

from tests.conftest import register_and_verify, login, add_single_question

PW = {"admin": "Adminpass1!", "student": "Studpass1!", "proctor": "Proctor1!"}
_counter = {"n": 0}


def _phone():
    _counter["n"] += 1
    return f"91{_counter['n']:08d}"


def _client_for(app, name, email, role):
    c = app.test_client()
    register_and_verify(c, app, name, email, _phone(), role, PW[role])
    login(c, email, PW[role])
    return c


def _user_id(app, email):
    from app.models import User
    with app.app_context():
        return User.query.filter_by(email=email).first().id


def make_world(app, tag, max_attempts=1):
    admin = _client_for(app, "Admin", f"admin_{tag}@test.com", "admin")
    admin.post("/admin/tests/create", data=dict(
        test_code=f"T{tag}".upper(), title=f"Exam {tag}", description="d", duration_minutes=30,
        total_questions=1, passing_marks=1, status="published", max_attempts=max_attempts,
        negative_marks_per_wrong=0))
    from app.models import Test
    with app.app_context():
        test_id = Test.query.filter_by(test_code=f"T{tag}".upper()).first().id
    add_single_question(admin, test_id, "Q1", "1", "2", "3", "4", "a", marks=1)

    students = {}
    for n in (1, 2):
        email = f"student{n}_{tag}@test.com"
        c = _client_for(app, f"Student{n}", email, "student")
        c.post("/api/proctor/enroll-face", data=json.dumps({"descriptor": [0.01 * i for i in range(128)]}),
               content_type="application/json")
        students[n] = {"client": c, "email": email, "id": _user_id(app, email)}
    admin.post(f"/admin/tests/{test_id}/assign",
               data={"student_ids": [str(students[1]["id"]), str(students[2]["id"])]})

    proctor = _client_for(app, "Proctor", f"proctor_{tag}@test.com", "proctor")
    return {
        "admin": admin, "proctor": proctor, "test_id": test_id, "students": students,
        "admin_email": f"admin_{tag}@test.com",
    }


def other_org_admin(app, tag):
    """An admin who belongs to a different organization than make_world's."""
    email = f"otheradmin_{tag}@test.com"
    c = _client_for(app, "Other Admin", email, "admin")
    from app import db
    from app.models import Organization, User
    with app.app_context():
        org = Organization(name=f"Other Org {tag}", slug=f"other-{tag}", status="active")
        db.session.add(org)
        db.session.flush()
        User.query.filter_by(email=email).first().org_id = org.id
        db.session.commit()
    return c


def start_attempt(app, world, n=1):
    world["students"][n]["client"].get(f"/student/tests/{world['test_id']}/start")
    from app.models import Attempt
    with app.app_context():
        return (Attempt.query.filter_by(test_id=world["test_id"], student_id=world["students"][n]["id"])
                .order_by(Attempt.id.desc()).first().id)


def terminate_by_violations(world, attempt_id, n=1):
    for _ in range(5):
        world["students"][n]["client"].post(
            "/api/proctor/event",
            data=json.dumps({"attempt_id": attempt_id, "event_type": "tab_hidden", "severity": "violation"}),
            content_type="application/json")


def get_attempt(app, attempt_id):
    from app.models import Attempt
    with app.app_context():
        a = Attempt.query.get(attempt_id)
        return {"status": a.status, "reason": a.termination_reason, "violations": a.violation_count,
                "risk": a.suspicion_score, "submitted_at": a.submitted_at}
