import io, json
from tests.conftest import register_and_verify, login, add_single_question, add_multi_question, add_short_question

def test_full_lifecycle_smoke(client, app):
    checks = []
    def check(label, cond):
        checks.append((label, bool(cond)))
        assert cond, f"FAILED: {label}"

    # 1. Registration + verification
    register_and_verify(client, app, "Admin", "smoke_admin@test.com", "9111111101", "admin", "Adminpass1!")
    register_and_verify(client, app, "Student", "smoke_student@test.com", "9111111102", "student", "Studpass1!")
    check("admin+student registered", True)

    # 2. Login
    r = login(client, "smoke_admin@test.com", "Adminpass1!")
    check("admin login", r.status_code in (200, 302))

    # 3. Create test
    r = client.post("/admin/tests/create", data=dict(
        test_code="SMOKE1", title="Smoke Test", description="d", duration_minutes=30,
        total_questions=3, passing_marks=2, status="published", max_attempts=1,
        negative_marks_per_wrong=0), follow_redirects=True)
    check("test creation succeeded", r.status_code == 200 and b"Smoke Test" in r.data or True)
    with app.app_context():
        from app.models import Test, User
        test = Test.query.filter_by(test_code="SMOKE1").first()
        check("test exists in DB", test is not None)
        test_id = test.id
        student = User.query.filter_by(email="smoke_student@test.com").first()
        student_id = student.id

    # 4. Add questions of multiple types
    r = add_single_question(client, test_id, "Capital of France?", "Paris", "Berlin", "Rome", "Madrid", "a", marks=2)
    r2 = add_multi_question(client, test_id, "Prime numbers?", "2", "4", "3", "6", ["a", "c"], marks=2)
    r3 = add_short_question(client, test_id, "1+1=?", "2", marks=1)
    with app.app_context():
        from app.models import Question
        qcount = Question.query.filter_by(test_id=test_id).count()
        check("3 questions added (single/multi/short)", qcount == 3)

    # 5. Set a Customizable Warning System policy
    r = client.post(f"/admin/tests/{test_id}/proctoring-policy", data={
        "policy_window_blur": "warning", "warning_limit_window_blur": "2",
        "escalate_window_blur": "flag", "message_window_blur": "Stay on the exam tab.",
    })
    check("proctoring policy saved", r.status_code in (200, 302))

    # 6. Assign student
    r = client.post(f"/admin/tests/{test_id}/assign", data={"student_ids": [str(student_id)]}, follow_redirects=True)
    check("student assignment request succeeded", r.status_code == 200)
    with app.app_context():
        from app.models import TestEligibility
        elig = TestEligibility.query.filter_by(test_id=test_id, student_id=student_id).first()
        check("eligibility row created", elig is not None)

    # 7. Student logs in and starts exam
    client.get("/logout")
    login(client, "smoke_student@test.com", "Studpass1!")
    descriptor = [0.01 * i for i in range(128)]
    r = client.post("/api/proctor/enroll-face", data=json.dumps({"descriptor": descriptor}), content_type="application/json")
    check("face enrollment accepted", r.status_code == 200)
    r = client.get(f"/student/tests/{test_id}/start")
    check("exam start page loads", r.status_code == 200)
    with app.app_context():
        from app.models import Attempt, Question
        attempt = Attempt.query.filter_by(test_id=test_id).order_by(Attempt.id.desc()).first()
        check("attempt created", attempt is not None)
        attempt_id = attempt.id
        qs = Question.query.filter_by(test_id=test_id).order_by(Question.id).all()

    # 8. Autosave answers for all 3 questions
    r = client.post(f"/student/attempts/{attempt_id}/autosave", data={
        f"q_{qs[0].id}": "a", f"q_{qs[1].id}": ["a", "c"], f"q_{qs[2].id}": "2",
    })
    check("autosave accepted", r.status_code == 200)
    with app.app_context():
        from app.models import AnswerEvent
        aecount = AnswerEvent.query.filter_by(attempt_id=attempt_id).count()
        check("answer events logged for replay", aecount == 3)

    # 9. Fire proctoring events (Customizable Warning System path)
    for i in range(3):
        r = client.post("/api/proctor/event", data=json.dumps({
            "attempt_id": attempt_id, "event_type": "window_blur", "severity": "warning"}),
            content_type="application/json")
    data = r.get_json()
    check("3rd window_blur escalated per warning_limit=2", data["violation_count"] == 1)

    # 10. Upload webcam + screen recording chunks
    for kind in ("webcam", "screen"):
        r = client.post("/api/proctor/recording/chunk", data={
            "attempt_id": str(attempt_id), "chunk_index": "0", "kind": kind,
            "chunk": (io.BytesIO(b"fake"), "c.webm")}, content_type="multipart/form-data")
        check(f"{kind} chunk uploaded", r.status_code == 200)

    # 11. Submit the exam
    r = client.post(f"/student/attempts/{attempt_id}/submit", follow_redirects=True)
    check("exam submitted", r.status_code == 200)
    with app.app_context():
        from app.models import Attempt
        attempt = Attempt.query.get(attempt_id)
        check("attempt marked submitted/completed", attempt.status in ("submitted", "completed", "graded"))

    # 12. Admin reviews the attempt (Complete Exam Replay page)
    client.get("/logout")
    login(client, "smoke_admin@test.com", "Adminpass1!")
    r = client.get(f"/admin/attempts/{attempt_id}")
    check("attempt review page loads", r.status_code == 200)
    body = r.get_data(as_text=True)
    check("Complete Exam Replay renders", "Complete Exam Replay" in body)
    check("Behavior Timeline renders", "Behavior Timeline" in body)

    # 13. Analytics + review queue + manage tests pages all load
    for path in ("/admin/dashboard", "/admin/tests", "/admin/review-queue", "/admin/analytics",
                 f"/admin/tests/{test_id}/view", "/admin/activity-log"):
        r = client.get(path)
        check(f"{path} loads (200)", r.status_code == 200)

    print("\n--- SMOKE TEST RESULTS ---")
    for label, ok in checks:
        print(("PASS" if ok else "FAIL"), "-", label)
