import json

import pytest

from tests.conftest import register_and_verify, login


@pytest.fixture()
def admin_and_student(client, app):
    register_and_verify(client, app, "Admin", "admin@test.com", "9000000010", "admin", "adminpass")
    register_and_verify(client, app, "Student", "student@test.com", "9000000011", "student", "studpass")
    return {"admin_email": "admin@test.com", "student_email": "student@test.com"}


def _create_test(client, **overrides):
    data = dict(
        test_code="T1", title="Sample Test", description="desc", duration_minutes=30,
        total_questions=2, passing_marks=1, status="published", max_attempts=1,
        negative_marks_per_wrong=0, allow_review="y",
    )
    data.update(overrides)
    return client.post("/admin/tests/create", data=data, follow_redirects=True)


def _enroll_face(client):
    descriptor = [0.01 * i for i in range(128)]
    return client.post(
        "/api/proctor/enroll-face",
        data=json.dumps({"descriptor": descriptor}),
        content_type="application/json",
    )


def test_admin_can_create_test_and_add_questions(client, app, admin_and_student):
    login(client, "admin@test.com", "adminpass")
    r = _create_test(client)
    assert r.status_code == 200

    with app.app_context():
        from app.models import Test
        test = Test.query.filter_by(test_code="T1").first()
        assert test is not None
        assert test.status == "published"

    r = client.post(
        f"/admin/tests/{test.id}/questions/add",
        data=dict(question_text="2+2?", option_a="3", option_b="4", option_c="5", option_d="6",
                   correct_answer="b", marks=1),
    )
    assert r.status_code in (200, 302)

    with app.app_context():
        from app.models import Question
        assert Question.query.filter_by(test_id=test.id).count() == 1


def test_student_cannot_start_without_face_enrollment(client, app, admin_and_student):
    login(client, "admin@test.com", "adminpass")
    _create_test(client)
    with app.app_context():
        from app.models import Test, User
        test = Test.query.filter_by(test_code="T1").first()
        student = User.query.filter_by(email="student@test.com").first()
    client.post(f"/admin/tests/{test.id}/assign", data={"student_ids": [str(student.id)]})
    client.get("/logout")

    login(client, "student@test.com", "studpass")
    r = client.get(f"/student/tests/{test.id}/start", follow_redirects=True)
    assert b"Enroll your face" in r.data


def test_full_exam_flow_end_to_end(client, app, admin_and_student):
    login(client, "admin@test.com", "adminpass")
    _create_test(client, negative_marks_per_wrong=0.5, max_attempts=1)

    with app.app_context():
        from app.models import Test, User
        test = Test.query.filter_by(test_code="T1").first()
        student = User.query.filter_by(email="student@test.com").first()

    client.post(
        f"/admin/tests/{test.id}/questions/add",
        data=dict(question_text="Q1", option_a="1", option_b="2", option_c="3", option_d="4",
                   correct_answer="b", marks=2),
    )
    client.post(
        f"/admin/tests/{test.id}/questions/add",
        data=dict(question_text="Q2", option_a="w", option_b="x", option_c="y", option_d="z",
                   correct_answer="d", marks=2),
    )
    client.post(f"/admin/tests/{test.id}/assign", data={"student_ids": [str(student.id)]})
    client.get("/logout")

    login(client, "student@test.com", "studpass")
    _enroll_face(client)

    r = client.get(f"/student/tests/{test.id}/start")
    assert r.status_code == 200
    assert b"Q1" in r.data and b"Q2" in r.data

    with app.app_context():
        from app.models import Attempt, Question
        attempt = Attempt.query.filter_by(test_id=test.id, student_id=student.id).first()
        questions = {q.question_text: q for q in Question.query.filter_by(test_id=test.id).all()}

    # Q1 correct (+2), Q2 wrong (-0.5) => 1.5
    form = {f"q_{questions['Q1'].id}": "b", f"q_{questions['Q2'].id}": "a"}
    r = client.post(f"/student/attempts/{attempt.id}/submit", data=form)
    payload = r.get_json()
    assert payload["ok"] is True

    with app.app_context():
        from app.models import Attempt
        updated = Attempt.query.get(attempt.id)
        assert updated.score == 1.5
        assert updated.status == "submitted"

    r = client.get(f"/student/attempts/{attempt.id}/review")
    assert r.status_code == 200
    assert b"Correct" in r.data and b"Incorrect" in r.data


def test_retake_limit_enforced(client, app, admin_and_student):
    login(client, "admin@test.com", "adminpass")
    _create_test(client, max_attempts=2, total_questions=1)

    with app.app_context():
        from app.models import Test, User
        test = Test.query.filter_by(test_code="T1").first()
        student = User.query.filter_by(email="student@test.com").first()

    client.post(
        f"/admin/tests/{test.id}/questions/add",
        data=dict(question_text="Only Q", option_a="1", option_b="2", option_c="3", option_d="4",
                   correct_answer="a", marks=1),
    )
    client.post(f"/admin/tests/{test.id}/assign", data={"student_ids": [str(student.id)]})
    client.get("/logout")

    login(client, "student@test.com", "studpass")
    _enroll_face(client)

    for i in range(2):
        client.get(f"/student/tests/{test.id}/start")
        with app.app_context():
            from app.models import Attempt
            attempt = Attempt.query.filter_by(test_id=test.id, student_id=student.id).order_by(
                Attempt.id.desc()
            ).first()
        client.post(f"/student/attempts/{attempt.id}/submit", data={})

    r = client.get(f"/student/tests/{test.id}/start", follow_redirects=True)
    assert b"used all 2 attempt" in r.data


def test_extra_time_accommodation_applied(client, app, admin_and_student):
    login(client, "admin@test.com", "adminpass")
    _create_test(client, duration_minutes=10, total_questions=1)

    with app.app_context():
        from app.models import Test, User
        test = Test.query.filter_by(test_code="T1").first()
        student = User.query.filter_by(email="student@test.com").first()

    client.post(
        f"/admin/tests/{test.id}/questions/add",
        data=dict(question_text="Q", option_a="1", option_b="2", option_c="3", option_d="4",
                   correct_answer="a", marks=1),
    )
    client.post(f"/admin/tests/{test.id}/assign", data={"student_ids": [str(student.id)], "extra_time_minutes": "5"})
    client.get("/logout")

    login(client, "student@test.com", "studpass")
    _enroll_face(client)
    r = client.get(f"/student/tests/{test.id}/start")
    assert b"durationSeconds: 900" in r.data  # (10 + 5) * 60


def test_csv_question_import(client, app, admin_and_student):
    import io

    login(client, "admin@test.com", "adminpass")
    _create_test(client, total_questions=2)
    with app.app_context():
        from app.models import Test
        test = Test.query.filter_by(test_code="T1").first()

    csv_content = (
        "question_text,option_a,option_b,option_c,option_d,correct_answer,marks\n"
        "Imported Q1,1,2,3,4,b,2\n"
        "Imported Q2,w,x,y,z,d,2\n"
        "bad row,,,,,,\n"
    )
    r = client.post(
        f"/admin/tests/{test.id}/questions/import",
        data={"csv_file": (io.BytesIO(csv_content.encode()), "questions.csv")},
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    assert r.status_code == 200
    assert b"Imported 2" in r.data

    with app.app_context():
        from app.models import Question
        assert Question.query.filter_by(test_id=test.id).count() == 2


def test_activity_log_records_admin_actions(client, app, admin_and_student):
    login(client, "admin@test.com", "adminpass")
    _create_test(client)

    r = client.get("/admin/activity-log")
    assert r.status_code == 200
    assert b"Created test" in r.data
