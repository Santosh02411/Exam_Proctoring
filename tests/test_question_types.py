import json

import pytest

from tests.conftest import register_and_verify, login, add_single_question, add_multi_question, add_short_question


@pytest.fixture()
def admin_and_student_qt(client, app):
    register_and_verify(client, app, "Admin", "adminqt@test.com", "9000000040", "admin", "adminpass")
    register_and_verify(client, app, "Student", "studentqt@test.com", "9000000041", "student", "studpass")
    return {"admin_email": "adminqt@test.com", "student_email": "studentqt@test.com"}


def _create_test(client, **overrides):
    data = dict(
        test_code="QT1", title="Question Types Test", description="d", duration_minutes=30,
        total_questions=3, passing_marks=1, status="published", max_attempts=1,
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


def test_multi_select_question_rejects_all_four_correct(client, app, admin_and_student_qt):
    login(client, "adminqt@test.com", "adminpass")
    _create_test(client)
    with app.app_context():
        from app.models import Test
        test = Test.query.filter_by(test_code="QT1").first()

    r = add_multi_question(client, test.id, "Pick all", "1", "2", "3", "4", ["a", "b", "c", "d"])
    assert b"at least one option must be marked incorrect" in r.data.lower()


def test_multi_select_requires_at_least_one_correct(client, app, admin_and_student_qt):
    login(client, "adminqt@test.com", "adminpass")
    _create_test(client)
    with app.app_context():
        from app.models import Test
        test = Test.query.filter_by(test_code="QT1").first()

    r = add_multi_question(client, test.id, "Pick none", "1", "2", "3", "4", [])
    assert b"select at least one correct option" in r.data.lower()


def test_single_choice_requires_all_options(client, app, admin_and_student_qt):
    login(client, "adminqt@test.com", "adminpass")
    _create_test(client)
    with app.app_context():
        from app.models import Test
        test = Test.query.filter_by(test_code="QT1").first()

    r = client.post(
        f"/admin/tests/{test.id}/questions/add",
        data={"question_type": "single", "question_text": "Missing options",
              "option_a": "1", "option_b": "", "option_c": "3", "option_d": "4",
              "correct_radio": "a", "marks": 1},
    )
    assert b"all four options are required" in r.data.lower()


def test_short_answer_requires_expected_text(client, app, admin_and_student_qt):
    login(client, "adminqt@test.com", "adminpass")
    _create_test(client)
    with app.app_context():
        from app.models import Test
        test = Test.query.filter_by(test_code="QT1").first()

    r = client.post(
        f"/admin/tests/{test.id}/questions/add",
        data={"question_type": "short", "question_text": "Capital of France?",
              "short_answer_text": "", "marks": 1},
    )
    assert b"enter the expected correct answer" in r.data.lower()


def test_mixed_question_types_full_exam_flow(client, app, admin_and_student_qt):
    login(client, "adminqt@test.com", "adminpass")
    _create_test(client, negative_marks_per_wrong=0, total_questions=3)
    with app.app_context():
        from app.models import Test, User
        test = Test.query.filter_by(test_code="QT1").first()
        student = User.query.filter_by(email="studentqt@test.com").first()

    add_single_question(client, test.id, "Single: 2+2?", "3", "4", "5", "6", "b", marks=2)
    add_multi_question(client, test.id, "Multi: prime numbers", "2", "3", "4", "9", ["a", "b"], marks=3)
    add_short_question(client, test.id, "Short: capital of France", "Paris", marks=2)

    with app.app_context():
        from app.models import Question
        qs = Question.query.filter_by(test_id=test.id).all()
        assert len(qs) == 3
        types = {q.question_type for q in qs}
        assert types == {"single", "multi", "short"}

    client.post(f"/admin/tests/{test.id}/assign", data={"student_ids": [str(student.id)]})
    client.get("/logout")

    login(client, "studentqt@test.com", "studpass")
    _enroll_face(client)

    r = client.get(f"/student/tests/{test.id}/start")
    assert r.status_code == 200
    # single/multi render as radio/checkbox inputs; short renders a text input
    assert b'type="radio"' in r.data
    assert b'type="checkbox"' in r.data
    assert b'type="text"' in r.data

    with app.app_context():
        from app.models import Attempt, Question
        attempt = Attempt.query.filter_by(test_id=test.id, student_id=student.id).first()
        by_text = {q.question_text: q for q in Question.query.filter_by(test_id=test.id).all()}

    single_q = by_text["Single: 2+2?"]
    multi_q = by_text["Multi: prime numbers"]
    short_q = by_text["Short: capital of France"]

    # All fully correct: single=b (+2), multi=a,b exact (+3), short="paris" case-insensitive (+2) = 7
    from werkzeug.datastructures import MultiDict
    form_data = MultiDict([
        (f"q_{single_q.id}", "b"),
        (f"q_{multi_q.id}", "a"),
        (f"q_{multi_q.id}", "b"),
        (f"q_{short_q.id}", "paris"),
    ])
    r = client.post(f"/student/attempts/{attempt.id}/submit", data=form_data)
    assert r.get_json()["ok"] is True

    with app.app_context():
        from app.models import Attempt as AttemptModel
        updated = AttemptModel.query.get(attempt.id)
        assert updated.score == 7.0

    r = client.get(f"/student/attempts/{attempt.id}/review")
    assert r.status_code == 200
    assert r.data.count(b"badge pass") >= 3  # all three questions correct


def test_multi_select_partial_selection_scores_zero(client, app, admin_and_student_qt):
    login(client, "adminqt@test.com", "adminpass")
    _create_test(client, negative_marks_per_wrong=0, total_questions=1)
    with app.app_context():
        from app.models import Test, User
        test = Test.query.filter_by(test_code="QT1").first()
        student = User.query.filter_by(email="studentqt@test.com").first()

    add_multi_question(client, test.id, "Pick both a and b", "1", "2", "3", "4", ["a", "b"], marks=5)
    client.post(f"/admin/tests/{test.id}/assign", data={"student_ids": [str(student.id)]})
    client.get("/logout")

    login(client, "studentqt@test.com", "studpass")
    _enroll_face(client)
    client.get(f"/student/tests/{test.id}/start")

    with app.app_context():
        from app.models import Attempt, Question
        attempt = Attempt.query.filter_by(test_id=test.id, student_id=student.id).first()
        q = Question.query.filter_by(test_id=test.id).first()

    # Only selecting ONE of the two correct options should score 0 (all-or-nothing grading).
    r = client.post(f"/student/attempts/{attempt.id}/submit", data={f"q_{q.id}": "a"})
    assert r.get_json()["ok"] is True

    with app.app_context():
        from app.models import Attempt as AttemptModel
        updated = AttemptModel.query.get(attempt.id)
        assert updated.score == 0.0


def test_short_answer_case_insensitive_grading(client, app, admin_and_student_qt):
    login(client, "adminqt@test.com", "adminpass")
    _create_test(client, negative_marks_per_wrong=0, total_questions=1)
    with app.app_context():
        from app.models import Test, User
        test = Test.query.filter_by(test_code="QT1").first()
        student = User.query.filter_by(email="studentqt@test.com").first()

    add_short_question(client, test.id, "Capital of Japan?", "Tokyo", marks=4)
    client.post(f"/admin/tests/{test.id}/assign", data={"student_ids": [str(student.id)]})
    client.get("/logout")

    login(client, "studentqt@test.com", "studpass")
    _enroll_face(client)
    client.get(f"/student/tests/{test.id}/start")

    with app.app_context():
        from app.models import Attempt, Question
        attempt = Attempt.query.filter_by(test_id=test.id, student_id=student.id).first()
        q = Question.query.filter_by(test_id=test.id).first()

    r = client.post(f"/student/attempts/{attempt.id}/submit", data={f"q_{q.id}": "  TOKYO  "})
    assert r.get_json()["ok"] is True

    with app.app_context():
        from app.models import Attempt as AttemptModel
        updated = AttemptModel.query.get(attempt.id)
        assert updated.score == 4.0
