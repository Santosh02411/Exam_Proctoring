import json

from tests.conftest import register_and_verify, login, add_single_question


def _setup(client, app, admin_email, student_email, test_code):
    register_and_verify(client, app, "Admin", admin_email, "9177700001", "admin", "Adminpass1!")
    register_and_verify(client, app, "Student", student_email, "9177700002", "student", "Studpass1!")
    login(client, admin_email, "Adminpass1!")
    client.post("/admin/tests/create", data=dict(
        test_code=test_code, title="Watermark Test", description="d", duration_minutes=20,
        total_questions=1, passing_marks=1, status="published", max_attempts=1,
        negative_marks_per_wrong=0))
    with app.app_context():
        from app.models import Test, User
        test_id = Test.query.filter_by(test_code=test_code).first().id
        student_id = User.query.filter_by(email=student_email).first().id
    add_single_question(client, test_id, "2+2=?", "3", "4", "5", "6", "b", marks=1)
    client.post(f"/admin/tests/{test_id}/assign", data={"student_ids": [str(student_id)]})
    client.get("/logout")
    login(client, student_email, "Studpass1!")
    descriptor = [0.01 * i for i in range(128)]
    client.post("/api/proctor/enroll-face", data=json.dumps({"descriptor": descriptor}), content_type="application/json")
    return test_id


def test_watermark_shows_when_enabled(client, app):
    test_id = _setup(client, app, "wma1@test.com", "wms1@test.com", "WM1")
    with app.app_context():
        from app.models import Test
        from app import db
        t = Test.query.get(test_id)
        t.enable_watermark = True
        db.session.commit()

    r = client.get(f"/student/tests/{test_id}/start")
    body = r.get_data(as_text=True)
    assert "examWatermark" in body
    assert "Student" in body  # the student's own name rendered into the tiles
    assert "wms1@test.com" in body


def test_watermark_hidden_when_not_enabled(client, app):
    test_id = _setup(client, app, "wma2@test.com", "wms2@test.com", "WM2")
    r = client.get(f"/student/tests/{test_id}/start")
    body = r.get_data(as_text=True)
    assert "examWatermark" not in body
