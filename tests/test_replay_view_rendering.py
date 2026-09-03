import io
import json

from tests.conftest import register_and_verify, login, add_single_question


def test_view_attempt_and_policy_pages_render(client, app):
    register_and_verify(client, app, "Admin", "a5@test.com", "9000000050", "admin", "Adminpass1!")
    register_and_verify(client, app, "Student", "s5@test.com", "9000000051", "student", "Studpass1!")
    login(client, "a5@test.com", "Adminpass1!")
    client.post("/admin/tests/create", data=dict(
        test_code="V1", title="View Test", description="d", duration_minutes=20,
        total_questions=1, passing_marks=1, status="published", max_attempts=1,
        negative_marks_per_wrong=0))
    with app.app_context():
        from app.models import Test, User
        test = Test.query.filter_by(test_code="V1").first()
        student = User.query.filter_by(email="s5@test.com").first()
        test_id, student_id = test.id, student.id
    add_single_question(client, test_id, "Q1", "1", "2", "3", "4", "a", marks=1)
    client.post(f"/admin/tests/{test_id}/assign", data={"student_ids": [str(student_id)]})
    client.get("/logout")
    login(client, "s5@test.com", "Studpass1!")
    descriptor = [0.01 * i for i in range(128)]
    client.post("/api/proctor/enroll-face", data=json.dumps({"descriptor": descriptor}),
                content_type="application/json")
    client.get(f"/student/tests/{test_id}/start")
    with app.app_context():
        from app.models import Attempt, Question
        attempt = Attempt.query.filter_by(test_id=test_id).order_by(Attempt.id.desc()).first()
        attempt_id = attempt.id
        q1 = Question.query.filter_by(test_id=test_id).first()
        q1_id = q1.id
    client.post(f"/student/attempts/{attempt_id}/autosave", data={f"q_{q1_id}": "a"})
    for kind in ("webcam", "screen"):
        client.post("/api/proctor/recording/chunk", data={
            "attempt_id": str(attempt_id), "chunk_index": "0", "kind": kind,
            "chunk": (io.BytesIO(b"fake"), "c.webm")}, content_type="multipart/form-data")
    client.post("/api/proctor/event", data=json.dumps({
        "attempt_id": attempt_id, "event_type": "window_blur", "severity": "warning"}),
        content_type="application/json")
    client.get("/logout")
    login(client, "a5@test.com", "Adminpass1!")

    r = client.get(f"/admin/attempts/{attempt_id}")
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert "Complete Exam Replay" in body
    assert "replayVideo-webcam" in body
    assert "replayVideo-screen" in body
    assert "REPLAY_SEGMENTS" in body

    r2 = client.get(f"/admin/tests/{test_id}/proctoring-policy")
    assert r2.status_code == 200
    body2 = r2.get_data(as_text=True)
    assert "Warning limit" in body2 and "Grace period" in body2
