import json

from tests.conftest import register_and_verify, login, add_single_question


def _full_flow(client, app, admin_email, student_email, test_code):
    register_and_verify(client, app, "Admin", admin_email, "9122200001", "admin", "Adminpass1!")
    register_and_verify(client, app, "Student", student_email, "9122200002", "student", "Studpass1!")
    login(client, admin_email, "Adminpass1!")
    client.post("/admin/tests/create", data=dict(
        test_code=test_code, title="Regrade Test", description="d", duration_minutes=20,
        total_questions=1, passing_marks=1, status="published", max_attempts=1,
        negative_marks_per_wrong=0))
    with app.app_context():
        from app.models import Test, User
        test_id = Test.query.filter_by(test_code=test_code).first().id
        student_id = User.query.filter_by(email=student_email).first().id
    # Correct answer is (wrongly) set to "a" — the intended answer key bug.
    add_single_question(client, test_id, "2+2=?", "3", "4", "5", "6", "a", marks=2)
    client.post(f"/admin/tests/{test_id}/assign", data={"student_ids": [str(student_id)]})
    with app.app_context():
        from app.models import Question
        question_id = Question.query.filter_by(test_id=test_id).first().id
    return test_id, student_id, question_id


def _take_and_submit(client, app, test_id, student_email, answer):
    client.get("/logout")
    login(client, student_email, "Studpass1!")
    descriptor = [0.01 * i for i in range(128)]
    client.post("/api/proctor/enroll-face", data=json.dumps({"descriptor": descriptor}), content_type="application/json")
    client.get(f"/student/tests/{test_id}/start")
    with app.app_context():
        from app.models import Attempt, Question
        attempt_id = Attempt.query.filter_by(test_id=test_id).order_by(Attempt.id.desc()).first().id
        q_id = Question.query.filter_by(test_id=test_id).first().id
    client.post(f"/student/attempts/{attempt_id}/autosave", data={f"q_{q_id}": answer})
    client.post(f"/student/attempts/{attempt_id}/submit", data={f"q_{q_id}": answer})
    return attempt_id


def test_edit_question_page_shows_prefilled_correct_answer(client, app):
    test_id, student_id, question_id = _full_flow(client, app, "eqa1@test.com", "eqs1@test.com", "EQ1")
    login(client, "eqa1@test.com", "Adminpass1!")
    r = client.get(f"/admin/tests/{test_id}/questions/{question_id}/edit")
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert 'name="correct_radio" value="a" checked' in body
    assert "2" in body  # marks pre-filled


def test_edit_question_updates_correct_answer(client, app):
    test_id, student_id, question_id = _full_flow(client, app, "eqa2@test.com", "eqs2@test.com", "EQ2")
    login(client, "eqa2@test.com", "Adminpass1!")
    r = client.post(f"/admin/tests/{test_id}/questions/{question_id}/edit", data={
        "question_type": "single", "question_text": "2+2=?",
        "option_a": "3", "option_b": "4", "option_c": "5", "option_d": "6",
        "correct_radio": "b", "marks": "2", "difficulty": "medium",
    }, follow_redirects=True)
    assert r.status_code == 200
    with app.app_context():
        from app.models import Question
        q = Question.query.get(question_id)
        assert q.correct_answer == "b"


def test_bulk_regrade_fixes_scores_for_wrong_answer_key(client, app):
    """The core scenario: the answer key was wrong ('a' instead of 'b'),
    a student answered 'b' (the actually-correct answer) and got marked
    wrong under the old key. Fixing the key + regrading should flip their
    score without them doing anything."""
    test_id, student_id, question_id = _full_flow(client, app, "eqa3@test.com", "eqs3@test.com", "EQ3")
    attempt_id = _take_and_submit(client, app, test_id, "eqs3@test.com", "b")
    client.get("/logout")

    with app.app_context():
        from app.models import Attempt
        attempt = Attempt.query.get(attempt_id)
        assert attempt.score == 0  # wrongly marked incorrect under the buggy key

    login(client, "eqa3@test.com", "Adminpass1!")
    r = client.post(f"/admin/tests/{test_id}/questions/{question_id}/edit", data={
        "question_type": "single", "question_text": "2+2=?",
        "option_a": "3", "option_b": "4", "option_c": "5", "option_d": "6",
        "correct_radio": "b", "marks": "2", "difficulty": "medium",
        "regrade": "on",
    }, follow_redirects=True)
    assert b"Rescored 1 existing attempt" in r.data

    with app.app_context():
        from app.models import Attempt
        attempt = Attempt.query.get(attempt_id)
        assert attempt.score == 2  # now correctly credited


def test_regrade_checkbox_unchecked_does_not_rescore(client, app):
    test_id, student_id, question_id = _full_flow(client, app, "eqa4@test.com", "eqs4@test.com", "EQ4")
    attempt_id = _take_and_submit(client, app, test_id, "eqs4@test.com", "b")
    client.get("/logout")

    login(client, "eqa4@test.com", "Adminpass1!")
    r = client.post(f"/admin/tests/{test_id}/questions/{question_id}/edit", data={
        "question_type": "single", "question_text": "2+2=?",
        "option_a": "3", "option_b": "4", "option_c": "5", "option_d": "6",
        "correct_radio": "b", "marks": "2", "difficulty": "medium",
        # "regrade" intentionally omitted
    }, follow_redirects=True)
    assert b"were NOT rescored" in r.data

    with app.app_context():
        from app.models import Attempt
        attempt = Attempt.query.get(attempt_id)
        assert attempt.score == 0  # unchanged — regrade wasn't requested


def test_regrade_does_not_touch_in_progress_attempts(client, app):
    test_id, student_id, question_id = _full_flow(client, app, "eqa5@test.com", "eqs5@test.com", "EQ5")

    client.get("/logout")
    login(client, "eqs5@test.com", "Studpass1!")
    descriptor = [0.01 * i for i in range(128)]
    client.post("/api/proctor/enroll-face", data=json.dumps({"descriptor": descriptor}), content_type="application/json")
    client.get(f"/student/tests/{test_id}/start")
    with app.app_context():
        from app.models import Attempt
        attempt_id = Attempt.query.filter_by(test_id=test_id).order_by(Attempt.id.desc()).first().id
        q_id = question_id
    client.post(f"/student/attempts/{attempt_id}/autosave", data={f"q_{q_id}": "b"})
    # deliberately NOT submitted — still in_progress
    client.get("/logout")

    login(client, "eqa5@test.com", "Adminpass1!")
    client.post(f"/admin/tests/{test_id}/questions/{question_id}/edit", data={
        "question_type": "single", "question_text": "2+2=?",
        "option_a": "3", "option_b": "4", "option_c": "5", "option_d": "6",
        "correct_radio": "b", "marks": "2", "difficulty": "medium",
        "regrade": "on",
    })

    with app.app_context():
        from app.models import Attempt
        attempt = Attempt.query.get(attempt_id)
        assert attempt.status == "in_progress"
        assert attempt.score is None  # untouched while still in progress
