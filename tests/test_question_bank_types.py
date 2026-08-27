import io
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


# Tiny 1x1 PNG, valid enough for Pillow/werkzeug to accept as a real image file.
_PNG_BYTES = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000a49444154789c6360000002000155a0f1a50000000049454e44ae426082"
)


@pytest.fixture()
def qt_admin_and_student(client, app):
    register_and_verify(client, app, "Admin", "qt_admin@test.com", "9000005001", "admin", "Adminpass1!")
    register_and_verify(client, app, "Stu", "qt_stu@test.com", "9000005002", "student", "Studpass1!")
    login(client, "qt_admin@test.com", "Adminpass1!")
    with app.app_context():
        from app.models import User
        return {"student_id": User.query.filter_by(email="qt_stu@test.com").first().id}


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


def _assign_and_start(client, app, test_id, student_id, email, password):
    client.post(f"/admin/tests/{test_id}/assign", data={"student_ids": [str(student_id)]})
    client.get("/logout")
    login(client, email, password)
    _enroll_face(client)
    client.get(f"/student/tests/{test_id}/start")
    with app.app_context():
        from app.models import Attempt
        return Attempt.query.filter_by(test_id=test_id, student_id=student_id).first().id


# --- True / False ------------------------------------------------------------

def test_true_false_question_created_and_graded(client, app, qt_admin_and_student):
    _create_test(client, "TF1")
    test_id = _get_test_id(app, "TF1")
    client.post(
        f"/admin/tests/{test_id}/questions/add",
        data=dict(question_type="true_false", question_text="The sky is blue.", marks=2, tf_correct="true"),
    )
    with app.app_context():
        from app.models import Question
        q = Question.query.filter_by(test_id=test_id).first()
        assert q.question_type == "true_false"
        assert q.correct_answer == "true"
        q_id = q.id

    attempt_id = _assign_and_start(
        client, app, test_id, qt_admin_and_student["student_id"], "qt_stu@test.com", "Studpass1!"
    )
    r = client.get(f"/student/tests/{test_id}/start")
    assert b'value="true"' in r.data and b'value="false"' in r.data

    client.post(f"/student/attempts/{attempt_id}/submit", data={f"q_{q_id}": "true"})
    with app.app_context():
        from app.models import Attempt
        assert Attempt.query.get(attempt_id).score == 2.0


def test_true_false_requires_a_selection(client, app, qt_admin_and_student):
    _create_test(client, "TF2")
    test_id = _get_test_id(app, "TF2")
    r = client.post(
        f"/admin/tests/{test_id}/questions/add",
        data=dict(question_type="true_false", question_text="2+2=4", marks=1),
    )
    assert b"Select True or False" in r.data
    with app.app_context():
        from app.models import Question
        assert Question.query.filter_by(test_id=test_id).first() is None


# --- Fill in the blank ---------------------------------------------------------

def test_fill_blank_question_created_and_graded(client, app, qt_admin_and_student):
    _create_test(client, "FB1")
    test_id = _get_test_id(app, "FB1")
    client.post(
        f"/admin/tests/{test_id}/questions/add",
        data=dict(
            question_type="fill_blank", question_text="The capital of France is ____.",
            marks=3, blank_answer="Paris;paris",
        ),
    )
    with app.app_context():
        from app.models import Question
        q = Question.query.filter_by(test_id=test_id).first()
        assert q.question_type == "fill_blank"
        q_id = q.id

    attempt_id = _assign_and_start(
        client, app, test_id, qt_admin_and_student["student_id"], "qt_stu@test.com", "Studpass1!"
    )
    client.post(f"/student/attempts/{attempt_id}/submit", data={f"q_{q_id}": "paris"})
    with app.app_context():
        from app.models import Attempt
        assert Attempt.query.get(attempt_id).score == 3.0


def test_fill_blank_requires_a_blank_marker_in_text(client, app, qt_admin_and_student):
    _create_test(client, "FB2")
    test_id = _get_test_id(app, "FB2")
    r = client.post(
        f"/admin/tests/{test_id}/questions/add",
        data=dict(question_type="fill_blank", question_text="No blank here.", marks=1, blank_answer="x"),
    )
    assert b"need a blank" in r.data


def test_fill_blank_requires_an_accepted_answer(client, app, qt_admin_and_student):
    _create_test(client, "FB3")
    test_id = _get_test_id(app, "FB3")
    r = client.post(
        f"/admin/tests/{test_id}/questions/add",
        data=dict(question_type="fill_blank", question_text="Water is made of ____.", marks=1),
    )
    assert b"Enter at least one accepted answer" in r.data


# --- Descriptive / coding (manual grading) -------------------------------------

def test_descriptive_question_not_auto_graded_and_shows_pending(client, app, qt_admin_and_student):
    _create_test(client, "DES1")
    test_id = _get_test_id(app, "DES1")
    client.post(
        f"/admin/tests/{test_id}/questions/add",
        data=dict(question_type="descriptive", question_text="Explain gravity.", marks=10, model_answer="mass attraction"),
    )
    with app.app_context():
        from app.models import Question
        q = Question.query.filter_by(test_id=test_id).first()
        assert q.needs_manual_grading is True
        q_id = q.id

    attempt_id = _assign_and_start(
        client, app, test_id, qt_admin_and_student["student_id"], "qt_stu@test.com", "Studpass1!"
    )
    client.post(f"/student/attempts/{attempt_id}/submit", data={f"q_{q_id}": "Objects with mass attract each other."})
    with app.app_context():
        from app.models import Attempt
        attempt = Attempt.query.get(attempt_id)
        assert attempt.score == 0.0  # pending, not auto-graded

    r = client.get(f"/student/attempts/{attempt_id}/result")
    assert b"still being reviewed" in r.data


def test_coding_question_with_starter_code_shown_and_manually_graded(client, app, qt_admin_and_student):
    _create_test(client, "COD1")
    test_id = _get_test_id(app, "COD1")
    client.post(
        f"/admin/tests/{test_id}/questions/add",
        data=dict(
            question_type="coding", question_text="Reverse a string.", marks=10,
            code_language="python", starter_code="def reverse(s):\n    pass",
        ),
    )
    with app.app_context():
        from app.models import Question
        q = Question.query.filter_by(test_id=test_id).first()
        q_id = q.id
        assert q.starter_code == "def reverse(s):\n    pass"
        assert q.code_language == "python"

    attempt_id = _assign_and_start(
        client, app, test_id, qt_admin_and_student["student_id"], "qt_stu@test.com", "Studpass1!"
    )
    r = client.get(f"/student/tests/{test_id}/start")
    assert b"def reverse(s):" in r.data

    client.post(f"/student/attempts/{attempt_id}/submit", data={f"q_{q_id}": "def reverse(s):\n    return s[::-1]"})
    client.get("/logout")

    login(client, "qt_admin@test.com", "Adminpass1!")
    r = client.get(f"/admin/attempts/{attempt_id}")
    assert b"Pending review" in r.data
    assert b"return s[::-1]" in r.data

    with app.app_context():
        from app.models import Answer
        answer_id = Answer.query.filter_by(attempt_id=attempt_id, question_id=q_id).first().id

    r = client.post(f"/admin/attempts/{attempt_id}/grade", data={f"score_{answer_id}": "9"}, follow_redirects=True)
    assert b"Grades saved" in r.data
    with app.app_context():
        from app.models import Attempt, Answer
        assert Attempt.query.get(attempt_id).score == 9.0
        graded = Answer.query.get(answer_id)
        assert graded.manual_score == 9.0
        assert graded.graded_by_id is not None


def test_grading_score_is_clamped_to_question_marks(client, app, qt_admin_and_student):
    _create_test(client, "COD2")
    test_id = _get_test_id(app, "COD2")
    client.post(
        f"/admin/tests/{test_id}/questions/add",
        data=dict(question_type="descriptive", question_text="Explain X.", marks=5),
    )
    with app.app_context():
        from app.models import Question
        q_id = Question.query.filter_by(test_id=test_id).first().id
    attempt_id = _assign_and_start(
        client, app, test_id, qt_admin_and_student["student_id"], "qt_stu@test.com", "Studpass1!"
    )
    client.post(f"/student/attempts/{attempt_id}/submit", data={f"q_{q_id}": "An answer."})
    client.get("/logout")
    login(client, "qt_admin@test.com", "Adminpass1!")
    with app.app_context():
        from app.models import Answer
        answer_id = Answer.query.filter_by(attempt_id=attempt_id, question_id=q_id).first().id

    client.post(f"/admin/attempts/{attempt_id}/grade", data={f"score_{answer_id}": "999"})
    with app.app_context():
        from app.models import Attempt
        assert Attempt.query.get(attempt_id).score == 5.0  # clamped to max marks


def test_regrading_is_idempotent(client, app, qt_admin_and_student):
    _create_test(client, "COD3")
    test_id = _get_test_id(app, "COD3")
    client.post(
        f"/admin/tests/{test_id}/questions/add",
        data=dict(question_type="descriptive", question_text="Explain Y.", marks=10),
    )
    with app.app_context():
        from app.models import Question
        q_id = Question.query.filter_by(test_id=test_id).first().id
    attempt_id = _assign_and_start(
        client, app, test_id, qt_admin_and_student["student_id"], "qt_stu@test.com", "Studpass1!"
    )
    client.post(f"/student/attempts/{attempt_id}/submit", data={f"q_{q_id}": "An answer."})
    client.get("/logout")
    login(client, "qt_admin@test.com", "Adminpass1!")
    with app.app_context():
        from app.models import Answer
        answer_id = Answer.query.filter_by(attempt_id=attempt_id, question_id=q_id).first().id

    client.post(f"/admin/attempts/{attempt_id}/grade", data={f"score_{answer_id}": "6"})
    client.post(f"/admin/attempts/{attempt_id}/grade", data={f"score_{answer_id}": "6"})
    client.post(f"/admin/attempts/{attempt_id}/grade", data={f"score_{answer_id}": "4"})
    with app.app_context():
        from app.models import Attempt
        assert Attempt.query.get(attempt_id).score == 4.0  # re-grading replaces, never accumulates


def test_negative_marking_not_applied_to_manually_graded_questions(client, app, qt_admin_and_student):
    _create_test(client, "COD4", negative_marks_per_wrong=1)
    test_id = _get_test_id(app, "COD4")
    client.post(
        f"/admin/tests/{test_id}/questions/add",
        data=dict(question_type="descriptive", question_text="Explain Z.", marks=5),
    )
    with app.app_context():
        from app.models import Question
        q_id = Question.query.filter_by(test_id=test_id).first().id
    attempt_id = _assign_and_start(
        client, app, test_id, qt_admin_and_student["student_id"], "qt_stu@test.com", "Studpass1!"
    )
    client.post(f"/student/attempts/{attempt_id}/submit", data={f"q_{q_id}": "An answer."})
    with app.app_context():
        from app.models import Attempt
        # No negative marking applied just for being ungraded/pending.
        assert Attempt.query.get(attempt_id).score == 0.0


# --- Media attachments ---------------------------------------------------------

def test_question_with_uploaded_image_is_served_and_shown_to_student(client, app, qt_admin_and_student):
    _create_test(client, "MED1")
    test_id = _get_test_id(app, "MED1")
    client.post(
        f"/admin/tests/{test_id}/questions/add",
        data={
            "question_type": "single", "question_text": "What shape?",
            "option_a": "Circle", "option_b": "Square", "option_c": "Triangle", "option_d": "Star",
            "correct_radio": "a", "marks": "1",
            "media_file": (io.BytesIO(_PNG_BYTES), "shape.png"),
        },
        content_type="multipart/form-data",
    )
    with app.app_context():
        from app.models import Question
        q = Question.query.filter_by(test_id=test_id).first()
        assert q.media_type == "image"
        assert q.media_url.startswith("/static/uploads/questions/")
        media_url = q.media_url

    r = client.get(media_url)
    assert r.status_code == 200
    assert r.content_type == "image/png"

    _assign_and_start(client, app, test_id, qt_admin_and_student["student_id"], "qt_stu@test.com", "Studpass1!")
    r = client.get(f"/student/tests/{test_id}/start")
    assert media_url.encode() in r.data


def test_question_with_external_media_url(client, app, qt_admin_and_student):
    _create_test(client, "MED2")
    test_id = _get_test_id(app, "MED2")
    client.post(
        f"/admin/tests/{test_id}/questions/add",
        data=dict(
            question_type="short", question_text="Describe this diagram.", short_answer_text="a triangle",
            marks=1, media_type="image", media_url="https://example.com/diagram.png",
        ),
    )
    with app.app_context():
        from app.models import Question
        q = Question.query.filter_by(test_id=test_id).first()
        assert q.media_type == "image"
        assert q.media_url == "https://example.com/diagram.png"


def test_rejects_unsupported_media_file_type(client, app, qt_admin_and_student):
    _create_test(client, "MED3")
    test_id = _get_test_id(app, "MED3")
    r = client.post(
        f"/admin/tests/{test_id}/questions/add",
        data={
            "question_type": "short", "question_text": "Q?", "short_answer_text": "a",
            "marks": "1", "media_file": (io.BytesIO(b"not an exe really"), "virus.exe"),
        },
        content_type="multipart/form-data",
    )
    with app.app_context():
        from app.models import Question
        # Rejected at the form validator (FileAllowed) — no question created with that upload.
        q = Question.query.filter_by(test_id=test_id).first()
        assert q is None or q.media_url is None


# --- Question bank reuse of new types --------------------------------------------

def test_bank_reuse_carries_over_coding_fields(client, app, qt_admin_and_student):
    client.post(
        "/admin/bank/add",
        data=dict(
            question_type="coding", question_text="Reverse a list", marks=5,
            code_language="python", starter_code="def rev(lst): pass", category="Algorithms",
        ),
    )
    with app.app_context():
        from app.models import QuestionBankItem
        item_id = QuestionBankItem.query.filter_by(question_text="Reverse a list").first().id

    _create_test(client, "BANKCOD")
    test_id = _get_test_id(app, "BANKCOD")
    client.post(f"/admin/tests/{test_id}/questions/from-bank", data={"item_ids": [str(item_id)]})
    with app.app_context():
        from app.models import Question
        q = Question.query.filter_by(test_id=test_id).first()
        assert q.question_type == "coding"
        assert q.starter_code == "def rev(lst): pass"
        assert q.code_language == "python"


def test_bank_reuse_carries_over_true_false(client, app, qt_admin_and_student):
    client.post("/admin/bank/add", data=dict(question_type="true_false", question_text="Water boils at 100C.", marks=1, tf_correct="true"))
    with app.app_context():
        from app.models import QuestionBankItem
        item_id = QuestionBankItem.query.filter_by(question_text="Water boils at 100C.").first().id

    _create_test(client, "BANKTF")
    test_id = _get_test_id(app, "BANKTF")
    client.post(f"/admin/tests/{test_id}/questions/from-bank", data={"item_ids": [str(item_id)]})
    with app.app_context():
        from app.models import Question
        q = Question.query.filter_by(test_id=test_id).first()
        assert q.question_type == "true_false"
        assert q.correct_answer == "true"


# --- CSV import of new types --------------------------------------------------

def test_csv_import_supports_true_false_and_fill_blank(client, app, qt_admin_and_student):
    _create_test(client, "CSVQT")
    test_id = _get_test_id(app, "CSVQT")
    csv_content = (
        "question_text,question_type,correct_answer\n"
        "The earth is flat.,true_false,false\n"
        "The capital of Japan is ____.,fill_blank,Tokyo;tokyo\n"
    )
    client.post(
        f"/admin/tests/{test_id}/questions/import",
        data={"csv_file": (io.BytesIO(csv_content.encode()), "q.csv")},
        content_type="multipart/form-data",
    )
    with app.app_context():
        from app.models import Question
        qs = {q.question_type: q for q in Question.query.filter_by(test_id=test_id).all()}
        assert qs["true_false"].correct_answer == "false"
        assert qs["fill_blank"].correct_answer == "Tokyo;tokyo"


def test_csv_import_skips_fill_blank_row_without_blank_marker(client, app, qt_admin_and_student):
    _create_test(client, "CSVQT2")
    test_id = _get_test_id(app, "CSVQT2")
    csv_content = (
        "question_text,question_type,correct_answer\n"
        "No blank marker here,fill_blank,answer\n"
    )
    r = client.post(
        f"/admin/tests/{test_id}/questions/import",
        data={"csv_file": (io.BytesIO(csv_content.encode()), "q.csv")},
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    with app.app_context():
        from app.models import Question
        assert Question.query.filter_by(test_id=test_id).count() == 0
