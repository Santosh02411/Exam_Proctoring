import io
import json

import pytest

from tests.conftest import register_and_verify, login, add_single_question


@pytest.fixture()
def admin_and_student_tl(client, app):
    register_and_verify(client, app, "Admin", "admintl@test.com", "9000000050", "admin", "Adminpass1!")
    register_and_verify(client, app, "Student", "studenttl@test.com", "9000000051", "student", "Studpass1!")
    return {"admin_email": "admintl@test.com", "student_email": "studenttl@test.com"}


def _create_test(client, **overrides):
    data = dict(
        test_code="TL1", title="Time Limit Test", description="d", duration_minutes=30,
        total_questions=2, passing_marks=1, status="published", max_attempts=1,
        negative_marks_per_wrong=0,
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


def test_question_time_limit_persisted_via_admin_form(client, app, admin_and_student_tl):
    login(client, "admintl@test.com", "Adminpass1!")
    _create_test(client)
    with app.app_context():
        from app.models import Test
        test = Test.query.filter_by(test_code="TL1").first()

    add_single_question(client, test.id, "Timed Q", "1", "2", "3", "4", "a", marks=1, time_limit_seconds=45)
    add_single_question(client, test.id, "Untimed Q", "1", "2", "3", "4", "a", marks=1)  # no limit

    with app.app_context():
        from app.models import Question
        timed = Question.query.filter_by(test_id=test.id, question_text="Timed Q").first()
        untimed = Question.query.filter_by(test_id=test.id, question_text="Untimed Q").first()
        assert timed.time_limit_seconds == 45
        assert untimed.time_limit_seconds is None


def test_question_time_limit_rejects_too_small_value(client, app, admin_and_student_tl):
    login(client, "admintl@test.com", "Adminpass1!")
    _create_test(client)
    with app.app_context():
        from app.models import Test
        test = Test.query.filter_by(test_code="TL1").first()

    # NumberRange(min=5) on the form field should reject a 1-second limit.
    r = add_single_question(client, test.id, "Too fast", "1", "2", "3", "4", "a", marks=1, time_limit_seconds=1)
    with app.app_context():
        from app.models import Question
        assert Question.query.filter_by(test_id=test.id, question_text="Too fast").first() is None


def test_time_limit_rendered_on_take_test_page(client, app, admin_and_student_tl):
    login(client, "admintl@test.com", "Adminpass1!")
    _create_test(client, total_questions=1)
    with app.app_context():
        from app.models import Test, User
        test = Test.query.filter_by(test_code="TL1").first()
        student = User.query.filter_by(email="studenttl@test.com").first()

    add_single_question(client, test.id, "Timed Q", "1", "2", "3", "4", "a", marks=1, time_limit_seconds=30)
    client.post(f"/admin/tests/{test.id}/assign", data={"student_ids": [str(student.id)]})
    client.get("/logout")

    login(client, "studenttl@test.com", "Studpass1!")
    _enroll_face(client)
    r = client.get(f"/student/tests/{test.id}/start")
    assert r.status_code == 200
    assert b'data-time-limit="30"' in r.data
    assert b"data-qtimer=" in r.data


def test_csv_import_supports_time_limit_column(client, app, admin_and_student_tl):
    login(client, "admintl@test.com", "Adminpass1!")
    _create_test(client, total_questions=1)
    with app.app_context():
        from app.models import Test
        test = Test.query.filter_by(test_code="TL1").first()

    csv_content = (
        "question_text,option_a,option_b,option_c,option_d,correct_answer,marks,time_limit_seconds\n"
        "CSV Timed,1,2,3,4,b,2,20\n"
    )
    r = client.post(
        f"/admin/tests/{test.id}/questions/import",
        data={"csv_file": (io.BytesIO(csv_content.encode()), "q.csv")},
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    assert r.status_code == 200
    assert b"Imported 1" in r.data

    with app.app_context():
        from app.models import Question
        q = Question.query.filter_by(test_id=test.id, question_text="CSV Timed").first()
        assert q.time_limit_seconds == 20


def test_untimed_question_has_no_time_limit_attribute(client, app, admin_and_student_tl):
    login(client, "admintl@test.com", "Adminpass1!")
    _create_test(client, total_questions=1)
    with app.app_context():
        from app.models import Test, User
        test = Test.query.filter_by(test_code="TL1").first()
        student = User.query.filter_by(email="studenttl@test.com").first()

    add_single_question(client, test.id, "No limit Q", "1", "2", "3", "4", "a", marks=1)
    client.post(f"/admin/tests/{test.id}/assign", data={"student_ids": [str(student.id)]})
    client.get("/logout")

    login(client, "studenttl@test.com", "Studpass1!")
    _enroll_face(client)
    r = client.get(f"/student/tests/{test.id}/start")
    assert b"data-time-limit" not in r.data
