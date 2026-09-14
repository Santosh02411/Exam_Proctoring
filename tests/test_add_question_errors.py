from tests.conftest import register_and_verify, login


def test_add_question_page_prefills_marks_to_1(client, app):
    register_and_verify(client, app, "Admin", "adminY@test.com", "9000000098", "admin", "Adminpass1!")
    login(client, "adminY@test.com", "Adminpass1!")
    client.post("/admin/tests/create", data=dict(
        test_code="Q1", title="Question Test", description="d", duration_minutes=20,
        total_questions=1, passing_marks=1, status="published", max_attempts=1,
        negative_marks_per_wrong=0))
    with app.app_context():
        from app.models import Test
        test_id = Test.query.filter_by(test_code="Q1").first().id

    r = client.get(f"/admin/tests/{test_id}/questions/add")
    body = r.get_data(as_text=True)
    # The "Marks" number input should come pre-filled with 1, matching what
    # the page's own "Marks default to 1" tip claims.
    assert 'value="1"' in body


def test_add_question_shows_error_when_marks_missing(client, app):
    register_and_verify(client, app, "Admin", "adminZ@test.com", "9000000097", "admin", "Adminpass1!")
    login(client, "adminZ@test.com", "Adminpass1!")
    client.post("/admin/tests/create", data=dict(
        test_code="Q2", title="Question Test 2", description="d", duration_minutes=20,
        total_questions=1, passing_marks=1, status="published", max_attempts=1,
        negative_marks_per_wrong=0))
    with app.app_context():
        from app.models import Test
        test_id = Test.query.filter_by(test_code="Q2").first().id

    # Submit without "marks" at all — this used to fail completely silently.
    r = client.post(f"/admin/tests/{test_id}/questions/add", data=dict(
        question_type="single", question_text="What is 2+2?",
        option_a="3", option_b="4", option_c="5", option_d="6",
        correct_radio="b",
    ))
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert "flash-error" in body or 'class="errors"' in body

    with app.app_context():
        from app.models import Question
        assert Question.query.filter_by(test_id=test_id).count() == 0


def test_add_bank_item_prefills_marks_and_shows_errors(client, app):
    register_and_verify(client, app, "Admin", "adminW@test.com", "9000000096", "admin", "Adminpass1!")
    login(client, "adminW@test.com", "Adminpass1!")

    r = client.get("/admin/bank/add")
    body = r.get_data(as_text=True)
    assert 'value="1"' in body

    r2 = client.post("/admin/bank/add", data=dict(
        question_type="single", question_text="",  # blank — required
        option_a="3", option_b="4", option_c="5", option_d="6",
        correct_radio="b", marks=1,
    ))
    assert r2.status_code == 200
    body2 = r2.get_data(as_text=True)
    assert "flash-error" in body2 or 'class="errors"' in body2
