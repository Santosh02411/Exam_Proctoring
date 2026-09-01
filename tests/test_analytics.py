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
def analytics_setup(client, app):
    register_and_verify(client, app, "Admin", "adminan@test.com", "9000000100", "admin", "Adminpass1!")
    register_and_verify(client, app, "Alice", "alicean@test.com", "9000000101", "student", "Studpass1!")
    register_and_verify(client, app, "Bob", "bobanl@test.com", "9000000102", "student", "Studpass1!")

    for email in ("alicean@test.com", "bobanl@test.com"):
        login(client, email, "Studpass1!")
        _enroll_face(client)
        client.get("/logout")

    login(client, "adminan@test.com", "Adminpass1!")
    return {}


def _create_test(client, app, code="AN1", **overrides):
    data = dict(test_code=code, title=f"Test {code}", description="d", duration_minutes=30,
                total_questions=2, passing_marks=1, status="published", max_attempts=1,
                negative_marks_per_wrong=0)
    data.update(overrides)
    client.post("/admin/tests/create", data=data)
    with app.app_context():
        from app.models import Test
        return Test.query.filter_by(test_code=code).first().id


def _set_category(app, test_id, question_text, category):
    with app.app_context():
        from app import db
        from app.models import Question
        q = Question.query.filter_by(test_id=test_id, question_text=question_text).first()
        q.category = category
        db.session.commit()


def _assign_all(client, app, test_id, emails):
    with app.app_context():
        from app.models import User
        ids = [str(User.query.filter_by(email=e).first().id) for e in emails]
    client.post(f"/admin/tests/{test_id}/assign", data={"student_ids": ids})


def _take_and_submit(client, app, test_id, email, answers):
    """answers: dict of question_text -> submitted letter/text (None = skip)."""
    client.get("/logout")
    login(client, email, "Studpass1!")
    client.get(f"/student/tests/{test_id}/start")
    with app.app_context():
        from app.models import Question, Attempt, User
        student = User.query.filter_by(email=email).first()
        attempt = Attempt.query.filter_by(test_id=test_id, student_id=student.id).first()
        qmap = {q.question_text: q.id for q in Question.query.filter_by(test_id=test_id).all()}
    form = {}
    for text, value in answers.items():
        if value is not None:
            form[f"q_{qmap[text]}"] = value
    client.post(f"/student/attempts/{attempt.id}/submit", data=form)
    client.get("/logout")
    login(client, "adminan@test.com", "Adminpass1!")
    return attempt.id


# ---------- question-wise success rate / skipped / difficulty ----------

def test_question_stats_success_and_skip_rates(client, app, analytics_setup):
    test_id = _create_test(client, app, code="AN1")
    add_single_question(client, test_id, "Easy Q", "1", "2", "3", "4", "a", marks=1)
    add_single_question(client, test_id, "Hard Q", "1", "2", "3", "4", "a", marks=1)
    _assign_all(client, app, test_id, ["alicean@test.com", "bobanl@test.com"])

    _take_and_submit(client, app, test_id, "alicean@test.com", {"Easy Q": "a", "Hard Q": "b"})
    _take_and_submit(client, app, test_id, "bobanl@test.com", {"Easy Q": "a", "Hard Q": None})

    with app.app_context():
        from app.models import Test
        from app import analytics
        test = Test.query.get(test_id)
        rows = {r["question_text"]: r for r in analytics.question_stats(test)}

        easy = rows["Easy Q"]
        assert easy["total_attempts"] == 2
        assert easy["answered_count"] == 2
        assert easy["correct_count"] == 2
        assert easy["success_rate"] == 100.0
        assert easy["skip_rate"] == 0.0

        hard = rows["Hard Q"]
        assert hard["answered_count"] == 1  # Bob skipped it
        assert hard["skipped_count"] == 1
        assert hard["skip_rate"] == 50.0
        assert hard["correct_count"] == 0  # Alice answered wrong
        assert hard["success_rate"] == 0.0


def test_most_skipped_questions_sorted_by_skip_rate(client, app, analytics_setup):
    test_id = _create_test(client, app, code="AN2")
    add_single_question(client, test_id, "Q Always Answered", "1", "2", "3", "4", "a", marks=1)
    add_single_question(client, test_id, "Q Often Skipped", "1", "2", "3", "4", "a", marks=1)
    _assign_all(client, app, test_id, ["alicean@test.com", "bobanl@test.com"])

    _take_and_submit(client, app, test_id, "alicean@test.com", {"Q Always Answered": "a", "Q Often Skipped": None})
    _take_and_submit(client, app, test_id, "bobanl@test.com", {"Q Always Answered": "a", "Q Often Skipped": None})

    with app.app_context():
        from app.models import Test
        from app import analytics
        test = Test.query.get(test_id)
        skipped = analytics.most_skipped_questions(test)
        assert skipped[0]["question_text"] == "Q Often Skipped"
        assert skipped[0]["skip_rate"] == 100.0


def test_question_difficulty_buckets_by_observed_success_rate(client, app, analytics_setup):
    test_id = _create_test(client, app, code="AN3")
    add_single_question(client, test_id, "Should Be Easy", "1", "2", "3", "4", "a", marks=1)
    add_single_question(client, test_id, "Should Be Hard", "1", "2", "3", "4", "a", marks=1)
    _assign_all(client, app, test_id, ["alicean@test.com", "bobanl@test.com"])

    _take_and_submit(client, app, test_id, "alicean@test.com", {"Should Be Easy": "a", "Should Be Hard": "b"})
    _take_and_submit(client, app, test_id, "bobanl@test.com", {"Should Be Easy": "a", "Should Be Hard": "c"})

    with app.app_context():
        from app.models import Test
        from app import analytics
        test = Test.query.get(test_id)
        rows = {r["question_text"]: r for r in analytics.question_difficulty(test)}
        assert rows["Should Be Easy"]["observed_difficulty"] == "easy"
        assert rows["Should Be Hard"]["observed_difficulty"] == "hard"
        # Both were authored at the default difficulty (medium) — that mismatches
        # the observed bucket for both, so label_matches_observed should be False.
        assert rows["Should Be Easy"]["label_matches_observed"] is False


def test_manually_graded_question_excluded_from_difficulty_but_has_avg_score(client, app, analytics_setup):
    test_id = _create_test(client, app, code="AN4")
    with app.app_context():
        from app import db
        from app.models import Question
        q = Question(test_id=test_id, question_type="descriptive", question_text="Essay Q",
                     correct_answer="(reference)", marks=10)
        db.session.add(q)
        db.session.commit()
        qid = q.id
    _assign_all(client, app, test_id, ["alicean@test.com"])
    attempt_id = _take_and_submit(client, app, test_id, "alicean@test.com", {"Essay Q": "my essay"})

    with app.app_context():
        from app.models import Answer
        ans = Answer.query.filter_by(attempt_id=attempt_id, question_id=qid).first()
        ans.manual_score = 7.0
        from app import db
        db.session.commit()

    with app.app_context():
        from app.models import Test
        from app import analytics
        test = Test.query.get(test_id)
        difficulty_rows = analytics.question_difficulty(test)
        assert all(r["question_text"] != "Essay Q" for r in difficulty_rows)

        stats = {r["question_text"]: r for r in analytics.question_stats(test)}
        assert stats["Essay Q"]["avg_score_pct"] == 70.0
        assert stats["Essay Q"]["success_rate"] is None


# ---------- performance by topic ----------

def test_performance_by_topic_aggregates_across_categories(client, app, analytics_setup):
    test_id = _create_test(client, app, code="AN5")
    add_single_question(client, test_id, "Math Q1", "1", "2", "3", "4", "a", marks=2)
    add_single_question(client, test_id, "Science Q1", "1", "2", "3", "4", "a", marks=2)
    _set_category(app, test_id, "Math Q1", "Math")
    _set_category(app, test_id, "Science Q1", "Science")
    _assign_all(client, app, test_id, ["alicean@test.com"])

    _take_and_submit(client, app, test_id, "alicean@test.com", {"Math Q1": "a", "Science Q1": "b"})

    with app.app_context():
        from app.models import Test
        from app import analytics
        test = Test.query.get(test_id)
        topics = {r["topic"]: r for r in analytics.performance_by_topic(test)}
        assert topics["Math"]["avg_pct"] == 100.0
        assert topics["Science"]["avg_pct"] == 0.0


def test_uncategorized_questions_grouped_together(client, app, analytics_setup):
    test_id = _create_test(client, app, code="AN6")
    add_single_question(client, test_id, "No Category Q", "1", "2", "3", "4", "a", marks=1)
    _assign_all(client, app, test_id, ["alicean@test.com"])
    _take_and_submit(client, app, test_id, "alicean@test.com", {"No Category Q": "a"})

    with app.app_context():
        from app.models import Test
        from app import analytics
        test = Test.query.get(test_id)
        topics = analytics.performance_by_topic(test)
        assert any(r["topic"] == "Uncategorized" for r in topics)


# ---------- exam comparison ----------

def test_exam_comparison_across_tests(client, app, analytics_setup):
    test1 = _create_test(client, app, code="AN7")
    add_single_question(client, test1, "Q", "1", "2", "3", "4", "a", marks=1)
    _assign_all(client, app, test1, ["alicean@test.com", "bobanl@test.com"])
    _take_and_submit(client, app, test1, "alicean@test.com", {"Q": "a"})
    _take_and_submit(client, app, test1, "bobanl@test.com", {"Q": "b"})

    test2 = _create_test(client, app, code="AN8")
    add_single_question(client, test2, "Q", "1", "2", "3", "4", "a", marks=1)
    _assign_all(client, app, test2, ["alicean@test.com"])
    _take_and_submit(client, app, test2, "alicean@test.com", {"Q": "a"})

    with app.app_context():
        from app.models import Test
        from app import analytics
        tests = [Test.query.get(test1), Test.query.get(test2)]
        rows = {r["test_id"]: r for r in analytics.exam_comparison(tests)}
        assert rows[test1]["attempts"] == 2
        assert rows[test1]["avg_score_pct"] == 50.0
        assert rows[test1]["pass_rate"] == 50.0
        assert rows[test2]["attempts"] == 1
        assert rows[test2]["avg_score_pct"] == 100.0


# ---------- student performance trend / topic ----------

def test_student_performance_trend_across_tests(client, app, analytics_setup):
    test1 = _create_test(client, app, code="AN9")
    add_single_question(client, test1, "Q", "1", "2", "3", "4", "a", marks=1)
    _assign_all(client, app, test1, ["alicean@test.com"])
    _take_and_submit(client, app, test1, "alicean@test.com", {"Q": "a"})

    test2 = _create_test(client, app, code="AN10")
    add_single_question(client, test2, "Q", "1", "2", "3", "4", "a", marks=1)
    _assign_all(client, app, test2, ["alicean@test.com"])
    _take_and_submit(client, app, test2, "alicean@test.com", {"Q": "b"})

    with app.app_context():
        from app.models import User
        from app import analytics
        alice = User.query.filter_by(email="alicean@test.com").first()
        trend = analytics.student_performance_trend(alice)
        assert len(trend) == 2
        assert trend[0]["score_pct"] == 100.0
        assert trend[1]["score_pct"] == 0.0


def test_student_topic_performance_across_all_tests(client, app, analytics_setup):
    test1 = _create_test(client, app, code="AN11")
    add_single_question(client, test1, "Math Q", "1", "2", "3", "4", "a", marks=1)
    _set_category(app, test1, "Math Q", "Math")
    _assign_all(client, app, test1, ["alicean@test.com"])
    _take_and_submit(client, app, test1, "alicean@test.com", {"Math Q": "a"})

    test2 = _create_test(client, app, code="AN12")
    add_single_question(client, test2, "Math Q2", "1", "2", "3", "4", "a", marks=1)
    _set_category(app, test2, "Math Q2", "Math")
    _assign_all(client, app, test2, ["alicean@test.com"])
    _take_and_submit(client, app, test2, "alicean@test.com", {"Math Q2": "b"})

    with app.app_context():
        from app.models import User
        from app import analytics
        alice = User.query.filter_by(email="alicean@test.com").first()
        topics = {r["topic"]: r for r in analytics.student_topic_performance(alice)}
        assert topics["Math"]["questions"] == 2
        assert topics["Math"]["avg_pct"] == 50.0


# ---------- violation trends ----------

def test_violation_trends_scoped_to_test_attempts(client, app, analytics_setup):
    test_id = _create_test(client, app, code="AN13")
    add_single_question(client, test_id, "Q", "1", "2", "3", "4", "a", marks=1)
    _assign_all(client, app, test_id, ["alicean@test.com"])

    client.get("/logout")
    login(client, "alicean@test.com", "Studpass1!")
    client.get(f"/student/tests/{test_id}/start")
    with app.app_context():
        from app.models import Attempt, User
        student = User.query.filter_by(email="alicean@test.com").first()
        attempt = Attempt.query.filter_by(test_id=test_id, student_id=student.id).first()
    client.post(
        "/api/proctor/event",
        data=json.dumps({"attempt_id": attempt.id, "event_type": "tab_hidden", "severity": "violation"}),
        content_type="application/json",
    )
    client.get("/logout")
    login(client, "adminan@test.com", "Adminpass1!")

    with app.app_context():
        from app.models import Test
        from app import analytics
        test = Test.query.get(test_id)
        trends = analytics.violation_trends(attempts=test.attempts, days=7)
        assert trends["total"] == 1
        assert trends["by_type"][0]["event_type"] == "tab_hidden"
        assert sum(d["count"] for d in trends["by_day"]) == 1


def test_violation_trends_empty_attempts_list_reports_zero(client, app, analytics_setup):
    with app.app_context():
        from app import analytics
        trends = analytics.violation_trends(attempts=[], days=7)
        assert trends["total"] == 0
        assert all(d["count"] == 0 for d in trends["by_day"])


# ---------- time spent per question ----------

def test_time_spent_per_question_recorded_and_aggregated(client, app, analytics_setup):
    test_id = _create_test(client, app, code="AN14")
    add_single_question(client, test_id, "Timed Q", "1", "2", "3", "4", "a", marks=1)
    _assign_all(client, app, test_id, ["alicean@test.com"])

    client.get("/logout")
    login(client, "alicean@test.com", "Studpass1!")
    client.get(f"/student/tests/{test_id}/start")
    with app.app_context():
        from app.models import Question, Attempt, User
        student = User.query.filter_by(email="alicean@test.com").first()
        attempt = Attempt.query.filter_by(test_id=test_id, student_id=student.id).first()
        qid = Question.query.filter_by(test_id=test_id).first().id

    client.post(f"/student/attempts/{attempt.id}/submit", data={
        f"q_{qid}": "a", "question_time_spent": json.dumps({str(qid): 42}),
    })

    with app.app_context():
        from app.models import Answer, Test
        from app import analytics
        ans = Answer.query.filter_by(attempt_id=attempt.id, question_id=qid).first()
        assert ans.time_spent_seconds == 42

        test = Test.query.get(test_id)
        stats = analytics.question_stats(test)
        assert stats[0]["avg_time_seconds"] == 42.0


def test_time_spent_merges_additively_across_autosave_and_submit(client, app, analytics_setup):
    test_id = _create_test(client, app, code="AN15")
    add_single_question(client, test_id, "Timed Q2", "1", "2", "3", "4", "a", marks=1)
    _assign_all(client, app, test_id, ["alicean@test.com"])

    client.get("/logout")
    login(client, "alicean@test.com", "Studpass1!")
    client.get(f"/student/tests/{test_id}/start")
    with app.app_context():
        from app.models import Question, Attempt, User
        student = User.query.filter_by(email="alicean@test.com").first()
        attempt = Attempt.query.filter_by(test_id=test_id, student_id=student.id).first()
        qid = Question.query.filter_by(test_id=test_id).first().id

    client.post(f"/student/attempts/{attempt.id}/autosave", data={
        f"q_{qid}": "a", "question_time_spent": json.dumps({str(qid): 10}),
    })
    client.post(f"/student/attempts/{attempt.id}/autosave", data={
        f"q_{qid}": "a", "question_time_spent": json.dumps({str(qid): 15}),
    })
    client.post(f"/student/attempts/{attempt.id}/submit", data={
        f"q_{qid}": "a", "question_time_spent": json.dumps({str(qid): 5}),
    })

    with app.app_context():
        from app.models import Answer
        ans = Answer.query.filter_by(attempt_id=attempt.id, question_id=qid).first()
        assert ans.time_spent_seconds == 30  # 10 + 15 + 5


def test_malformed_time_spent_payload_ignored_gracefully(client, app, analytics_setup):
    test_id = _create_test(client, app, code="AN16")
    add_single_question(client, test_id, "Timed Q3", "1", "2", "3", "4", "a", marks=1)
    _assign_all(client, app, test_id, ["alicean@test.com"])

    client.get("/logout")
    login(client, "alicean@test.com", "Studpass1!")
    client.get(f"/student/tests/{test_id}/start")
    with app.app_context():
        from app.models import Question, Attempt, User
        student = User.query.filter_by(email="alicean@test.com").first()
        attempt = Attempt.query.filter_by(test_id=test_id, student_id=student.id).first()
        qid = Question.query.filter_by(test_id=test_id).first().id

    r = client.post(f"/student/attempts/{attempt.id}/submit", data={
        f"q_{qid}": "a", "question_time_spent": "not valid json",
    })
    assert r.status_code == 200

    with app.app_context():
        from app.models import Answer
        ans = Answer.query.filter_by(attempt_id=attempt.id, question_id=qid).first()
        assert ans.time_spent_seconds is None


# ---------- admin routes ----------

def test_analytics_overview_page_renders(client, app, analytics_setup):
    test_id = _create_test(client, app, code="AN17")
    add_single_question(client, test_id, "Q", "1", "2", "3", "4", "a", marks=1)
    _assign_all(client, app, test_id, ["alicean@test.com"])
    _take_and_submit(client, app, test_id, "alicean@test.com", {"Q": "a"})

    r = client.get("/admin/analytics")
    assert r.status_code == 200
    assert f"Test {'AN17'}".encode() in r.data or b"AN17" in r.data


def test_test_analytics_page_renders(client, app, analytics_setup):
    test_id = _create_test(client, app, code="AN18")
    add_single_question(client, test_id, "Q", "1", "2", "3", "4", "a", marks=1)
    _assign_all(client, app, test_id, ["alicean@test.com"])
    _take_and_submit(client, app, test_id, "alicean@test.com", {"Q": "a"})

    r = client.get(f"/admin/tests/{test_id}/analytics")
    assert r.status_code == 200
    assert b"Question Difficulty Analysis" in r.data
    assert b"Most Frequently Skipped Questions" in r.data


def test_student_analytics_page_renders(client, app, analytics_setup):
    test_id = _create_test(client, app, code="AN19")
    add_single_question(client, test_id, "Q", "1", "2", "3", "4", "a", marks=1)
    _assign_all(client, app, test_id, ["alicean@test.com"])
    _take_and_submit(client, app, test_id, "alicean@test.com", {"Q": "a"})

    with app.app_context():
        from app.models import User
        alice_id = User.query.filter_by(email="alicean@test.com").first().id

    r = client.get(f"/admin/students/{alice_id}/analytics")
    assert r.status_code == 200
    assert b"Performance Trend" in r.data


def test_analytics_routes_require_review_access(client, app, analytics_setup):
    test_id = _create_test(client, app, code="AN20")
    client.get("/logout")
    login(client, "alicean@test.com", "Studpass1!")

    assert client.get("/admin/analytics").status_code == 403
    assert client.get(f"/admin/tests/{test_id}/analytics").status_code == 403
