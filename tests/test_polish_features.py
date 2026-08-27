import io
import json

import pytest

from tests.conftest import register_and_verify, login, add_single_question, get_outbox


# --- Termination notification --------------------------------------------

def test_termination_emails_student_and_admin(client, app):
    register_and_verify(client, app, "Admin", "admin3@test.com", "9000000030", "admin", "Adminpass1!")
    register_and_verify(client, app, "Student", "student3@test.com", "9000000031", "student", "Studpass1!")

    login(client, "admin3@test.com", "Adminpass1!")
    client.post(
        "/admin/tests/create",
        data=dict(test_code="TERM1", title="Termination Notice Test", description="d", duration_minutes=20,
                   total_questions=1, passing_marks=1, status="published", max_attempts=1,
                   negative_marks_per_wrong=0),
    )
    with app.app_context():
        from app.models import Test, User
        test = Test.query.filter_by(test_code="TERM1").first()
        test_id, test_title = test.id, test.title
        student = User.query.filter_by(email="student3@test.com").first()
    add_single_question(client, test_id, "Q", "1", "2", "3", "4", "a", marks=1)
    client.post(f"/admin/tests/{test_id}/assign", data={"student_ids": [str(student.id)]})
    client.get("/logout")

    login(client, "student3@test.com", "Studpass1!")
    descriptor = [0.01 * i for i in range(128)]
    client.post("/api/proctor/enroll-face", data=json.dumps({"descriptor": descriptor}),
                content_type="application/json")
    client.get(f"/student/tests/{test_id}/start")
    with app.app_context():
        from app.models import Attempt
        attempt_id = Attempt.query.filter_by(test_id=test_id, student_id=student.id).first().id

    for _ in range(5):
        r = client.post(
            "/api/proctor/event",
            data=json.dumps({"attempt_id": attempt_id, "event_type": "tab_hidden", "severity": "violation"}),
            content_type="application/json",
        )
    assert r.get_json()["terminated"] is True

    outbox = get_outbox(app)
    # Student gets told their own attempt ended.
    assert "student3@test.com" in outbox
    assert "was terminated" in outbox
    assert test_title in outbox
    # The test's owning admin gets a heads-up to go review it.
    assert "admin3@test.com" in outbox
    assert "Attempt terminated" in outbox

    with app.app_context():
        from app.models import Attempt
        attempt = Attempt.query.get(attempt_id)
        assert attempt.status == "terminated"
        assert attempt.termination_reason


# --- Question bank / reuse across tests -----------------------------------

@pytest.fixture()
def bank_admin(client, app):
    register_and_verify(client, app, "Admin", "adminbank@test.com", "9000000032", "admin", "Adminpass1!")
    login(client, "adminbank@test.com", "Adminpass1!")
    return "adminbank@test.com"


def _create_test(client, app, code):
    client.post(
        "/admin/tests/create",
        data=dict(test_code=code, title=f"{code} Title", description="d", duration_minutes=20,
                   total_questions=1, passing_marks=1, status="draft", max_attempts=1,
                   negative_marks_per_wrong=0),
    )
    with app.app_context():
        from app.models import Test
        return Test.query.filter_by(test_code=code).first().id


def test_add_bank_item_directly(client, app, bank_admin):
    r = client.post(
        "/admin/bank/add",
        data=dict(
            question_type="single", question_text="What is 2+2?",
            option_a="3", option_b="4", option_c="5", option_d="6",
            correct_radio="b", marks=1, category="Math",
        ),
        follow_redirects=True,
    )
    assert b"Added to the question bank" in r.data
    with app.app_context():
        from app.models import QuestionBankItem
        item = QuestionBankItem.query.filter_by(question_text="What is 2+2?").first()
        assert item is not None
        assert item.category == "Math"
        assert item.correct_answer == "b"


def test_save_question_to_bank_and_reuse_across_tests(client, app, bank_admin):
    test1_id = _create_test(client, app, "BANK1")
    add_single_question(client, test1_id, "Capital of France?", "Paris", "Rome", "Berlin", "Madrid", "a", marks=2)
    with app.app_context():
        from app.models import Question
        q = Question.query.filter_by(test_id=test1_id).first()
        q_id = q.id

    r = client.post(
        f"/admin/tests/{test1_id}/questions/{q_id}/save-to-bank",
        data={"category": "Geography"},
        follow_redirects=True,
    )
    assert b"Saved to the question bank" in r.data

    with app.app_context():
        from app.models import QuestionBankItem
        item = QuestionBankItem.query.filter_by(question_text="Capital of France?").first()
        assert item is not None
        item_id = item.id

    # Reuse it in a second, unrelated test.
    test2_id = _create_test(client, app, "BANK2")
    r = client.post(
        f"/admin/tests/{test2_id}/questions/from-bank",
        data={"item_ids": [str(item_id)]},
        follow_redirects=True,
    )
    assert b"Added 1 question(s) from the bank" in r.data

    with app.app_context():
        from app.models import Question
        copied = Question.query.filter_by(test_id=test2_id).first()
        assert copied is not None
        assert copied.question_text == "Capital of France?"
        assert copied.bank_item_id == item_id
        assert copied.marks == 2

        # Original test's question is untouched and independent of the copy.
        original = Question.query.filter_by(test_id=test1_id).first()
        assert original.id != copied.id

    # manage_bank page reports it's now used in a test.
    r = client.get("/admin/bank")
    assert b"Capital of France?" in r.data


def test_deleting_bank_item_keeps_existing_copies(client, app, bank_admin):
    test1_id = _create_test(client, app, "BANK3")
    add_single_question(client, test1_id, "2+2=?", "3", "4", "5", "6", "b", marks=1)
    with app.app_context():
        from app.models import Question
        q_id = Question.query.filter_by(test_id=test1_id).first().id
    client.post(f"/admin/tests/{test1_id}/questions/{q_id}/save-to-bank", data={})
    with app.app_context():
        from app.models import QuestionBankItem
        item_id = QuestionBankItem.query.filter_by(question_text="2+2=?").first().id

    client.post(f"/admin/bank/{item_id}/delete", follow_redirects=True)

    with app.app_context():
        from app.models import Question, QuestionBankItem
        assert QuestionBankItem.query.get(item_id) is None
        # The copy in the original test survives, just with the provenance link cleared.
        q = Question.query.get(q_id)
        assert q is not None
        assert q.bank_item_id is None


def test_pick_from_bank_page_marks_already_added_items(client, app, bank_admin):
    test1_id = _create_test(client, app, "BANK4")
    r = client.post(
        "/admin/bank/add",
        data=dict(
            question_type="short", question_text="Capital of Japan?",
            short_answer_text="Tokyo", marks=1, category="Geography",
        ),
        follow_redirects=True,
    )
    with app.app_context():
        from app.models import QuestionBankItem
        item_id = QuestionBankItem.query.filter_by(question_text="Capital of Japan?").first().id

    client.post(f"/admin/tests/{test1_id}/questions/from-bank", data={"item_ids": [str(item_id)]})

    r = client.get(f"/admin/tests/{test1_id}/questions/from-bank")
    assert r.status_code == 200
    assert b"Capital of Japan?" in r.data


# --- Dark mode / accessibility --------------------------------------------

def test_theme_toggle_and_skip_link_present_on_authenticated_pages(client, app):
    register_and_verify(client, app, "Admin", "adminui@test.com", "9000000033", "admin", "Adminpass1!")
    login(client, "adminui@test.com", "Adminpass1!")
    r = client.get("/admin/dashboard")
    html = r.data.decode()
    assert 'id="themeToggle"' in html
    assert 'aria-pressed=' in html
    assert 'class="skip-link"' in html
    assert 'href="#mainContent"' in html
    assert 'id="mainContent"' in html


def test_theme_toggle_present_on_logged_out_pages(client, app):
    r = client.get("/login")
    html = r.data.decode()
    assert 'id="themeToggle"' in html
    assert 'class="skip-link"' in html


def test_dark_theme_css_variables_defined(client, app):
    r = client.get("/static/css/style.css")
    assert r.status_code == 200
    css = r.data.decode()
    assert '[data-theme="dark"]' in css
    assert '--bg' in css and '--text' in css


def test_flash_messages_are_a_live_region(client, app):
    r = client.post("/login", data={"email": "nope@test.com", "password": "x", "captcha_answer": "0"},
                     follow_redirects=True)
    assert b'aria-live="polite"' in r.data
