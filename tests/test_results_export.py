import json

import pytest

from tests.conftest import register_and_verify, login, add_single_question


def _enroll_face(client):
    descriptor = [0.01 * i for i in range(128)]
    return client.post(
        "/api/proctor/enroll-face",
        data=json.dumps({"descriptor": descriptor}),
        content_type="application/json",
    )


@pytest.fixture()
def re_admin(client, app):
    register_and_verify(client, app, "Admin", "re_admin@test.com", "9000006001", "admin", "Adminpass1!")
    login(client, "re_admin@test.com", "Adminpass1!")
    return "re_admin@test.com"


def _create_test(client, code):
    client.post(
        "/admin/tests/create",
        data=dict(test_code=code, title=f"{code} Title", description="d", duration_minutes=20,
                   total_questions=1, passing_marks=1, status="published", max_attempts=1,
                   negative_marks_per_wrong=0),
    )


def _get_test_id(app, code):
    with app.app_context():
        from app.models import Test
        return Test.query.filter_by(test_code=code).first().id


def _student(client, app, email, phone):
    client.get("/logout")
    register_and_verify(client, app, "Stu", email, phone, "student", "Studpass1!")
    client.get("/logout")
    login(client, "re_admin@test.com", "Adminpass1!")
    with app.app_context():
        from app.models import User
        return User.query.filter_by(email=email).first().id


def test_results_page_shows_analytics_summary(client, app, re_admin):
    _create_test(client, "RE1")
    test_id = _get_test_id(app, "RE1")
    r = client.get(f"/admin/tests/{test_id}/results")
    assert r.status_code == 200
    assert b"Total Attempts" in r.data
    assert b"Average Score" in r.data
    assert b"Pass Rate" in r.data


def test_analytics_computed_correctly_across_attempts(client, app, re_admin):
    _create_test(client, "RE2")
    test_id = _get_test_id(app, "RE2")
    add_single_question(client, test_id, "Q1?", "A", "B", "C", "D", "a")

    s1 = _student(client, app, "re_stu1@test.com", "9000006002")
    s2 = _student(client, app, "re_stu2@test.com", "9000006003")
    client.post(f"/admin/tests/{test_id}/assign", data={"student_ids": [str(s1), str(s2)]})

    # Student 1 passes.
    client.get("/logout")
    login(client, "re_stu1@test.com", "Studpass1!")
    _enroll_face(client)
    client.get(f"/student/tests/{test_id}/start")
    with app.app_context():
        from app.models import Question, Attempt
        q_id = Question.query.filter_by(test_id=test_id).first().id
        a1 = Attempt.query.filter_by(test_id=test_id, student_id=s1).first().id
    client.post(f"/student/attempts/{a1}/submit", data={f"q_{q_id}": "a"})

    # Student 2 fails (wrong answer).
    client.get("/logout")
    login(client, "re_stu2@test.com", "Studpass1!")
    _enroll_face(client)
    client.get(f"/student/tests/{test_id}/start")
    with app.app_context():
        from app.models import Attempt
        a2 = Attempt.query.filter_by(test_id=test_id, student_id=s2).first().id
    client.post(f"/student/attempts/{a2}/submit", data={f"q_{q_id}": "b"})

    client.get("/logout")
    login(client, "re_admin@test.com", "Adminpass1!")
    r = client.get(f"/admin/tests/{test_id}/results")
    html = r.data.decode()
    assert ">2<" in html  # total attempts
    assert "50.0%" in html  # pass rate: 1 of 2 passed


def test_export_results_returns_csv_with_scores(client, app, re_admin):
    _create_test(client, "RE3")
    test_id = _get_test_id(app, "RE3")
    add_single_question(client, test_id, "Q1?", "A", "B", "C", "D", "a")
    s1 = _student(client, app, "re_stu3@test.com", "9000006004")
    client.post(f"/admin/tests/{test_id}/assign", data={"student_ids": [str(s1)]})

    client.get("/logout")
    login(client, "re_stu3@test.com", "Studpass1!")
    _enroll_face(client)
    client.get(f"/student/tests/{test_id}/start")
    with app.app_context():
        from app.models import Question, Attempt
        q_id = Question.query.filter_by(test_id=test_id).first().id
        attempt_id = Attempt.query.filter_by(test_id=test_id, student_id=s1).first().id
    client.post(f"/student/attempts/{attempt_id}/submit", data={f"q_{q_id}": "a"})

    client.get("/logout")
    login(client, "re_admin@test.com", "Adminpass1!")
    r = client.get(f"/admin/tests/{test_id}/results/export")
    assert r.status_code == 200
    assert r.mimetype == "text/csv"
    body = r.data.decode()
    assert "re_stu3@test.com" in body
    assert "pass" in body
    assert "1.0" in body or "1" in body


def test_export_results_requires_content_or_review_access(client, app, re_admin):
    _create_test(client, "RE4")
    test_id = _get_test_id(app, "RE4")
    client.get("/logout")
    register_and_verify(client, app, "Stu", "re_stu4@test.com", "9000006005", "student", "Studpass1!")
    login(client, "re_stu4@test.com", "Studpass1!")
    r = client.get(f"/admin/tests/{test_id}/results/export")
    assert r.status_code == 403
