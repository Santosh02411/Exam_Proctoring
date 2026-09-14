from datetime import datetime

from tests.conftest import register_and_verify, login


def _make_test_with_attempts(app, admin_email, test_code, n_students=10):
    """Builds a test with one 'good' question (top scorers get it right,
    bottom scorers get it wrong — should discriminate well) and one 'bad'
    question (inverted — bottom scorers get it right more often, a red
    flag), then n_students finished attempts with a range of total scores."""
    with app.app_context():
        from app import db
        from app.models import Test, Question, Attempt, Answer, User, Organization

        admin = User.query.filter_by(email=admin_email).first()
        test = Test(
            test_code=test_code, title="Item Analysis Test", duration_minutes=30,
            total_questions=2, passing_marks=1, status="published",
            created_by=admin.id, org_id=admin.org_id, max_attempts=1,
        )
        db.session.add(test)
        db.session.flush()

        good_q = Question(
            test_id=test.id, question_type="single", question_text="Good discriminator",
            option_a="1", option_b="2", option_c="3", option_d="4",
            correct_answer="b", marks=1,
        )
        bad_q = Question(
            test_id=test.id, question_type="single", question_text="Bad discriminator (inverted)",
            option_a="1", option_b="2", option_c="3", option_d="4",
            correct_answer="b", marks=1,
        )
        db.session.add_all([good_q, bad_q])
        db.session.flush()

        for i in range(n_students):
            student = User(
                user_id=f"ITEM{i:03d}", name=f"Student {i}", email=f"item{i}_{test_code}@test.com",
                phone="9100000000", role="student", org_id=admin.org_id, status="active",
                email_verified=True,
            )
            student.set_password("x")
            db.session.add(student)
            db.session.flush()

            # Score decreases as i increases — student 0 is the strongest.
            is_strong = i < n_students // 2
            attempt = Attempt(
                test_id=test.id, student_id=student.id, status="submitted",
                started_at=datetime.utcnow(), submitted_at=datetime.utcnow(),
                score=float(n_students - i),  # 10, 9, 8, ... descending
                attempt_token=f"item-analysis-token-{test_code}-{i}",
            )
            db.session.add(attempt)
            db.session.flush()

            # Good question: strong students get it right, weak ones don't.
            db.session.add(Answer(
                attempt_id=attempt.id, question_id=good_q.id,
                selected_option="b" if is_strong else "a",
            ))
            # Bad question: INVERTED — weak students get it right instead.
            db.session.add(Answer(
                attempt_id=attempt.id, question_id=bad_q.id,
                selected_option="a" if is_strong else "b",
            ))
        db.session.commit()
        return test.id, good_q.id, bad_q.id


def test_discrimination_index_flags_good_and_bad_questions(client, app):
    register_and_verify(client, app, "Admin", "itemadmin@test.com", "9133300001", "admin", "Adminpass1!")
    test_id, good_q_id, bad_q_id = _make_test_with_attempts(app, "itemadmin@test.com", "ITEM1")

    with app.app_context():
        from app import analytics
        from app.models import Test
        test = Test.query.get(test_id)
        rows = analytics.question_discrimination(test)
        by_id = {r["question_id"]: r for r in rows}

        good = by_id[good_q_id]
        bad = by_id[bad_q_id]

        assert good["discrimination_index"] > 0.5
        assert good["quality"] == "excellent"

        assert bad["discrimination_index"] < 0
        assert bad["quality"] == "negative"


def test_discrimination_index_returns_empty_for_too_few_attempts(client, app):
    register_and_verify(client, app, "Admin", "itemadmin2@test.com", "9133300002", "admin", "Adminpass1!")
    test_id, _, _ = _make_test_with_attempts(app, "itemadmin2@test.com", "ITEM2", n_students=2)
    with app.app_context():
        from app import analytics
        from app.models import Test
        test = Test.query.get(test_id)
        assert analytics.question_discrimination(test) == []


def test_analytics_page_renders_discrimination_section(client, app):
    register_and_verify(client, app, "Admin", "itemadmin3@test.com", "9133300003", "admin", "Adminpass1!")
    test_id, _, _ = _make_test_with_attempts(app, "itemadmin3@test.com", "ITEM3")
    login(client, "itemadmin3@test.com", "Adminpass1!")
    r = client.get(f"/admin/tests/{test_id}/analytics")
    assert r.status_code == 200
    assert b"Question Discrimination Index" in r.data
    assert b"Excellent" in r.data or b"Good" in r.data
    assert b"Negative" in r.data
