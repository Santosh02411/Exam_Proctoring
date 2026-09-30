from datetime import datetime

from tests.conftest import register_and_verify, login


def test_admin_calendar_shows_scheduled_test(client, app):
    register_and_verify(client, app, "Admin", "cala1@test.com", "9166655001", "admin", "Adminpass1!")
    login(client, "cala1@test.com", "Adminpass1!")
    start = datetime(2026, 6, 15, 10, 0)
    client.post("/admin/tests/create", data=dict(
        test_code="CAL1", title="Calendar Test", description="d", duration_minutes=20,
        total_questions=1, passing_marks=1, status="published", max_attempts=1,
        negative_marks_per_wrong=0, start_time=start.strftime("%Y-%m-%dT%H:%M")))

    r = client.get("/admin/calendar?year=2026&month=6")
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert "Calendar Test" in body
    assert "10:00" in body


def test_admin_calendar_does_not_show_test_in_other_month(client, app):
    register_and_verify(client, app, "Admin", "cala2@test.com", "9166655002", "admin", "Adminpass1!")
    login(client, "cala2@test.com", "Adminpass1!")
    start = datetime(2026, 6, 15, 10, 0)
    client.post("/admin/tests/create", data=dict(
        test_code="CAL2", title="June Only Test", description="d", duration_minutes=20,
        total_questions=1, passing_marks=1, status="published", max_attempts=1,
        negative_marks_per_wrong=0, start_time=start.strftime("%Y-%m-%dT%H:%M")))

    r = client.get("/admin/calendar?year=2026&month=7")
    assert b"June Only Test" not in r.data


def test_admin_calendar_mine_only_filter(client, app):
    register_and_verify(client, app, "Admin", "cala3@test.com", "9166655003", "admin", "Adminpass1!")
    register_and_verify(client, app, "Admin2", "cala3b@test.com", "9166655004", "admin", "Adminpass1!")
    start = datetime(2026, 8, 10, 9, 0)

    login(client, "cala3@test.com", "Adminpass1!")
    client.post("/admin/tests/create", data=dict(
        test_code="CAL3A", title="Mine Test", description="d", duration_minutes=20,
        total_questions=1, passing_marks=1, status="published", max_attempts=1,
        negative_marks_per_wrong=0, start_time=start.strftime("%Y-%m-%dT%H:%M")))

    r = client.get("/admin/calendar?year=2026&month=8&mine=1")
    assert b"Mine Test" in r.data


def test_test_without_start_time_not_on_calendar(client, app):
    register_and_verify(client, app, "Admin", "cala4@test.com", "9166655005", "admin", "Adminpass1!")
    login(client, "cala4@test.com", "Adminpass1!")
    client.post("/admin/tests/create", data=dict(
        test_code="CAL4", title="No Schedule Test", description="d", duration_minutes=20,
        total_questions=1, passing_marks=1, status="published", max_attempts=1,
        negative_marks_per_wrong=0))
    r = client.get("/admin/calendar")
    assert b"No Schedule Test" not in r.data


def test_student_calendar_shows_assigned_test(client, app):
    register_and_verify(client, app, "Admin", "cala5@test.com", "9166655006", "admin", "Adminpass1!")
    register_and_verify(client, app, "Student", "cals5@test.com", "9166655007", "student", "Studpass1!")
    login(client, "cala5@test.com", "Adminpass1!")
    start = datetime(2026, 9, 20, 14, 30)
    client.post("/admin/tests/create", data=dict(
        test_code="CAL5", title="Student Cal Test", description="d", duration_minutes=20,
        total_questions=1, passing_marks=1, status="published", max_attempts=1,
        negative_marks_per_wrong=0, start_time=start.strftime("%Y-%m-%dT%H:%M")))
    with app.app_context():
        from app.models import Test, User
        test_id = Test.query.filter_by(test_code="CAL5").first().id
        student_id = User.query.filter_by(email="cals5@test.com").first().id
    client.post(f"/admin/tests/{test_id}/assign", data={"student_ids": [str(student_id)]})

    client.get("/logout")
    login(client, "cals5@test.com", "Studpass1!")
    r = client.get("/student/calendar?year=2026&month=9")
    assert r.status_code == 200
    assert b"Student Cal Test" in r.data
    assert b"14:30" in r.data


def test_student_calendar_does_not_show_unassigned_test(client, app):
    register_and_verify(client, app, "Admin", "cala6@test.com", "9166655008", "admin", "Adminpass1!")
    register_and_verify(client, app, "Student", "cals6@test.com", "9166655009", "student", "Studpass1!")
    login(client, "cala6@test.com", "Adminpass1!")
    start = datetime(2026, 10, 5, 11, 0)
    client.post("/admin/tests/create", data=dict(
        test_code="CAL6", title="Not Assigned Test", description="d", duration_minutes=20,
        total_questions=1, passing_marks=1, status="published", max_attempts=1,
        negative_marks_per_wrong=0, start_time=start.strftime("%Y-%m-%dT%H:%M")))
    # deliberately not assigned to the student

    client.get("/logout")
    login(client, "cals6@test.com", "Studpass1!")
    r = client.get("/student/calendar?year=2026&month=10")
    assert b"Not Assigned Test" not in r.data
