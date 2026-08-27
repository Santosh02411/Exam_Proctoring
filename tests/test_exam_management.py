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
def em_admin_and_student(client, app):
    register_and_verify(client, app, "Admin", "em_admin@test.com", "9000003001", "admin", "Adminpass1!")
    register_and_verify(client, app, "Stu", "em_stu@test.com", "9000003002", "student", "Studpass1!")
    login(client, "em_admin@test.com", "Adminpass1!")
    with app.app_context():
        from app.models import User
        return {"student_id": User.query.filter_by(email="em_stu@test.com").first().id}


def _create_test(client, code, **overrides):
    data = dict(
        test_code=code, title=f"{code} Title", description="d", duration_minutes=20,
        total_questions=1, passing_marks=1, status="published", max_attempts=1,
        negative_marks_per_wrong=0,
    )
    data.update(overrides)
    return client.post("/admin/tests/create", data=data, follow_redirects=True)


def _get_test_id(app, code):
    with app.app_context():
        from app.models import Test
        return Test.query.filter_by(test_code=code).first().id


# --- Instructions -----------------------------------------------------------

def test_instructions_field_saved_and_shown_on_consent_screen(client, app, em_admin_and_student):
    _create_test(client, "EMI1", instructions="Bring a calculator. No phones.")
    test_id = _get_test_id(app, "EMI1")
    add_single_question(client, test_id, "Q1?", "A", "B", "C", "D", "a")
    client.post(f"/admin/tests/{test_id}/assign", data={"student_ids": [str(em_admin_and_student['student_id'])]})
    client.get("/logout")

    login(client, "em_stu@test.com", "Studpass1!")
    _enroll_face(client)
    r = client.get(f"/student/tests/{test_id}/start")
    assert b"Bring a calculator. No phones." in r.data


def test_view_test_shows_instructions(client, app, em_admin_and_student):
    _create_test(client, "EMI2", instructions="Read each question twice.")
    test_id = _get_test_id(app, "EMI2")
    r = client.get(f"/admin/tests/{test_id}/view")
    assert b"Read each question twice." in r.data


# --- Split randomize toggles -------------------------------------------------

def test_randomize_question_and_option_order_are_independent(client, app, em_admin_and_student):
    _create_test(client, "EMR1", randomize_questions="y")  # randomize_options omitted -> False
    with app.app_context():
        from app.models import Test
        test = Test.query.filter_by(test_code="EMR1").first()
        assert test.randomize_questions is True
        assert test.randomize_options is False


def test_edit_test_can_flip_randomize_toggles_independently(client, app, em_admin_and_student):
    _create_test(client, "EMR2", randomize_questions="y", randomize_options="y")
    test_id = _get_test_id(app, "EMR2")
    client.post(
        f"/admin/tests/{test_id}/edit",
        data=dict(
            test_code="EMR2", title="EMR2 Title", description="d", duration_minutes=20,
            total_questions=1, passing_marks=1, status="published", max_attempts=1,
            negative_marks_per_wrong=0, randomize_options="y",  # randomize_questions omitted -> False
        ),
    )
    with app.app_context():
        from app.models import Test
        test = Test.query.filter_by(test_code="EMR2").first()
        assert test.randomize_questions is False
        assert test.randomize_options is True


# --- Categories / difficulty --------------------------------------------------

def test_question_category_and_difficulty_saved(client, app, em_admin_and_student):
    _create_test(client, "EMC1")
    test_id = _get_test_id(app, "EMC1")
    client.post(
        f"/admin/tests/{test_id}/questions/add",
        data=dict(
            question_type="single", question_text="Capital of Peru?",
            option_a="Lima", option_b="Quito", option_c="Bogota", option_d="Caracas",
            correct_radio="a", marks=1, category="Geography", difficulty="hard",
        ),
    )
    with app.app_context():
        from app.models import Question
        q = Question.query.filter_by(test_id=test_id).first()
        assert q.category == "Geography"
        assert q.difficulty == "hard"


def test_category_and_difficulty_carry_through_bank_reuse(client, app, em_admin_and_student):
    _create_test(client, "EMC2")
    test_id = _get_test_id(app, "EMC2")
    client.post(
        f"/admin/tests/{test_id}/questions/add",
        data=dict(
            question_type="short", question_text="2+2=?", short_answer_text="4",
            marks=1, category="Math", difficulty="easy",
        ),
    )
    with app.app_context():
        from app.models import Question
        q_id = Question.query.filter_by(test_id=test_id).first().id
    client.post(f"/admin/tests/{test_id}/questions/{q_id}/save-to-bank", data={"category": "Math"})
    with app.app_context():
        from app.models import QuestionBankItem
        item = QuestionBankItem.query.filter_by(question_text="2+2=?").first()
        assert item.difficulty == "easy"
        item_id = item.id

    test2_id = _get_test_id(app, "EMC2") if False else None
    _create_test(client, "EMC3")
    test3_id = _get_test_id(app, "EMC3")
    client.post(f"/admin/tests/{test3_id}/questions/from-bank", data={"item_ids": [str(item_id)]})
    with app.app_context():
        from app.models import Question
        copied = Question.query.filter_by(test_id=test3_id).first()
        assert copied.category == "Math"
        assert copied.difficulty == "easy"


def test_csv_import_supports_category_and_difficulty_columns(client, app, em_admin_and_student):
    import io
    _create_test(client, "EMC4")
    test_id = _get_test_id(app, "EMC4")
    csv_content = (
        "question_text,correct_answer,option_a,option_b,option_c,option_d,category,difficulty\n"
        "Capital of Italy?,a,Rome,Milan,Venice,Turin,Geography,medium\n"
    )
    client.post(
        f"/admin/tests/{test_id}/questions/import",
        data={"csv_file": (io.BytesIO(csv_content.encode()), "q.csv")},
        content_type="multipart/form-data",
    )
    with app.app_context():
        from app.models import Question
        q = Question.query.filter_by(test_id=test_id).first()
        assert q.category == "Geography"
        assert q.difficulty == "medium"


# --- Sections -----------------------------------------------------------------

def test_create_edit_delete_section(client, app, em_admin_and_student):
    _create_test(client, "EMS1")
    test_id = _get_test_id(app, "EMS1")

    r = client.post(f"/admin/tests/{test_id}/sections", data={"name": "Part A", "duration_minutes": "10"}, follow_redirects=True)
    assert b"Section added" in r.data
    with app.app_context():
        from app.models import Section
        section = Section.query.filter_by(test_id=test_id).first()
        assert section.name == "Part A"
        assert section.duration_minutes == 10
        section_id = section.id

    r = client.post(
        f"/admin/tests/{test_id}/sections/{section_id}/edit",
        data={"name": "Part A Renamed", "duration_minutes": "15", "description": "updated"},
        follow_redirects=True,
    )
    assert b"Section updated" in r.data
    with app.app_context():
        from app.models import Section
        section = Section.query.get(section_id)
        assert section.name == "Part A Renamed"
        assert section.duration_minutes == 15

    r = client.post(f"/admin/tests/{test_id}/sections/{section_id}/delete", follow_redirects=True)
    assert b"Section removed" in r.data
    with app.app_context():
        from app.models import Section
        assert Section.query.get(section_id) is None


def test_deleting_section_unsections_its_questions_instead_of_deleting_them(client, app, em_admin_and_student):
    _create_test(client, "EMS2")
    test_id = _get_test_id(app, "EMS2")
    client.post(f"/admin/tests/{test_id}/sections", data={"name": "Part A"})
    with app.app_context():
        from app.models import Section
        section_id = Section.query.filter_by(test_id=test_id).first().id

    client.post(
        f"/admin/tests/{test_id}/questions/add",
        data=dict(
            question_type="single", question_text="Sectioned Q", option_a="A", option_b="B",
            option_c="C", option_d="D", correct_radio="a", marks=1, section_id=str(section_id),
        ),
    )
    with app.app_context():
        from app.models import Question
        q = Question.query.filter_by(test_id=test_id).first()
        assert q.section_id == section_id
        q_id = q.id

    client.post(f"/admin/tests/{test_id}/sections/{section_id}/delete")
    with app.app_context():
        from app.models import Question
        q = Question.query.get(q_id)
        assert q is not None
        assert q.section_id is None


def test_question_assigned_to_section_via_add_question(client, app, em_admin_and_student):
    _create_test(client, "EMS3")
    test_id = _get_test_id(app, "EMS3")
    client.post(f"/admin/tests/{test_id}/sections", data={"name": "Verbal"})
    with app.app_context():
        from app.models import Section
        section_id = Section.query.filter_by(test_id=test_id).first().id

    client.post(
        f"/admin/tests/{test_id}/questions/add",
        data=dict(
            question_type="single", question_text="Synonym of happy?", option_a="Sad", option_b="Joyful",
            option_c="Angry", option_d="Tired", correct_radio="b", marks=1, section_id=str(section_id),
        ),
    )
    with app.app_context():
        from app.models import Question
        q = Question.query.filter_by(test_id=test_id).first()
        assert q.section_id == section_id


def test_take_test_groups_questions_by_section_and_shows_timer(client, app, em_admin_and_student):
    _create_test(client, "EMS4")
    test_id = _get_test_id(app, "EMS4")
    client.post(f"/admin/tests/{test_id}/sections", data={"name": "Timed Section", "duration_minutes": "5"})
    with app.app_context():
        from app.models import Section
        section_id = Section.query.filter_by(test_id=test_id).first().id

    client.post(
        f"/admin/tests/{test_id}/questions/add",
        data=dict(
            question_type="single", question_text="In-section Q", option_a="A", option_b="B",
            option_c="C", option_d="D", correct_radio="a", marks=1, section_id=str(section_id),
        ),
    )
    client.post(
        f"/admin/tests/{test_id}/questions/add",
        data=dict(
            question_type="single", question_text="Unsectioned Q", option_a="A", option_b="B",
            option_c="C", option_d="D", correct_radio="a", marks=1, section_id="",
        ),
    )
    client.post(f"/admin/tests/{test_id}/assign", data={"student_ids": [str(em_admin_and_student['student_id'])]})
    client.get("/logout")

    login(client, "em_stu@test.com", "Studpass1!")
    _enroll_face(client)
    r = client.get(f"/student/tests/{test_id}/start")
    html = r.data.decode()
    assert "Timed Section" in html
    assert 'data-section-duration="300"' in html
    assert "In-section Q" in html
    assert "Unsectioned Q" in html


def test_test_with_no_sections_behaves_as_flat_list(client, app, em_admin_and_student):
    _create_test(client, "EMS5")
    test_id = _get_test_id(app, "EMS5")
    add_single_question(client, test_id, "Flat Q", "A", "B", "C", "D", "a")
    client.post(f"/admin/tests/{test_id}/assign", data={"student_ids": [str(em_admin_and_student['student_id'])]})
    client.get("/logout")

    login(client, "em_stu@test.com", "Studpass1!")
    _enroll_face(client)
    r = client.get(f"/student/tests/{test_id}/start")
    assert b"Flat Q" in r.data
    assert b"data-section-duration" not in r.data


# --- Auto-save answers ---------------------------------------------------------

def test_autosave_stores_in_progress_answers(client, app, em_admin_and_student):
    _create_test(client, "EMA1")
    test_id = _get_test_id(app, "EMA1")
    add_single_question(client, test_id, "Autosave Q", "A", "B", "C", "D", "a")
    client.post(f"/admin/tests/{test_id}/assign", data={"student_ids": [str(em_admin_and_student['student_id'])]})
    client.get("/logout")

    login(client, "em_stu@test.com", "Studpass1!")
    _enroll_face(client)
    client.get(f"/student/tests/{test_id}/start")
    with app.app_context():
        from app.models import Question, Attempt
        q_id = Question.query.filter_by(test_id=test_id).first().id
        attempt_id = Attempt.query.filter_by(test_id=test_id).first().id

    r = client.post(f"/student/attempts/{attempt_id}/autosave", data={f"q_{q_id}": "a"})
    assert r.get_json()["saved"] is True

    with app.app_context():
        from app.models import Attempt
        import json as _json
        attempt = Attempt.query.get(attempt_id)
        saved = _json.loads(attempt.autosaved_answers)
        assert saved[str(q_id)] == "a"


def test_resuming_attempt_prefills_autosaved_answer(client, app, em_admin_and_student):
    _create_test(client, "EMA2")
    test_id = _get_test_id(app, "EMA2")
    add_single_question(client, test_id, "Resume Q", "A", "B", "C", "D", "a")
    client.post(f"/admin/tests/{test_id}/assign", data={"student_ids": [str(em_admin_and_student['student_id'])]})
    client.get("/logout")

    login(client, "em_stu@test.com", "Studpass1!")
    _enroll_face(client)
    client.get(f"/student/tests/{test_id}/start")
    with app.app_context():
        from app.models import Question, Attempt
        q_id = Question.query.filter_by(test_id=test_id).first().id
        attempt_id = Attempt.query.filter_by(test_id=test_id).first().id
    client.post(f"/student/attempts/{attempt_id}/autosave", data={f"q_{q_id}": "a"})

    r = client.get(f"/student/tests/{test_id}/start")
    html = r.data.decode()
    idx = html.find(f'name="q_{q_id}" value="a"')
    assert idx != -1
    assert "checked" in html[idx:idx + 60]


def test_autosave_cleared_on_final_submit(client, app, em_admin_and_student):
    _create_test(client, "EMA3")
    test_id = _get_test_id(app, "EMA3")
    add_single_question(client, test_id, "Submit Q", "A", "B", "C", "D", "a")
    client.post(f"/admin/tests/{test_id}/assign", data={"student_ids": [str(em_admin_and_student['student_id'])]})
    client.get("/logout")

    login(client, "em_stu@test.com", "Studpass1!")
    _enroll_face(client)
    client.get(f"/student/tests/{test_id}/start")
    with app.app_context():
        from app.models import Question, Attempt
        q_id = Question.query.filter_by(test_id=test_id).first().id
        attempt_id = Attempt.query.filter_by(test_id=test_id).first().id
    client.post(f"/student/attempts/{attempt_id}/autosave", data={f"q_{q_id}": "a"})
    client.post(f"/student/attempts/{attempt_id}/submit", data={f"q_{q_id}": "a"})

    with app.app_context():
        from app.models import Attempt
        attempt = Attempt.query.get(attempt_id)
        assert attempt.autosaved_answers is None


def test_autosave_blocked_for_another_students_attempt(client, app, em_admin_and_student):
    client.get("/logout")
    register_and_verify(client, app, "Stu2", "em_stu2@test.com", "9000003003", "student", "Studpass1!")
    with app.app_context():
        from app.models import User
        stu2_id = User.query.filter_by(email="em_stu2@test.com").first().id
    client.get("/logout")
    login(client, "em_admin@test.com", "Adminpass1!")

    _create_test(client, "EMA4")
    test_id = _get_test_id(app, "EMA4")
    add_single_question(client, test_id, "Q", "A", "B", "C", "D", "a")
    client.post(
        f"/admin/tests/{test_id}/assign",
        data={"student_ids": [str(em_admin_and_student['student_id']), str(stu2_id)]},
    )
    client.get("/logout")

    login(client, "em_stu@test.com", "Studpass1!")
    _enroll_face(client)
    client.get(f"/student/tests/{test_id}/start")
    with app.app_context():
        from app.models import Attempt, Question
        attempt_id = Attempt.query.filter_by(test_id=test_id).first().id
        q_id = Question.query.filter_by(test_id=test_id).first().id
    client.get("/logout")

    login(client, "em_stu2@test.com", "Studpass1!")
    r = client.post(f"/student/attempts/{attempt_id}/autosave", data={f"q_{q_id}": "a"})
    assert r.status_code == 403
