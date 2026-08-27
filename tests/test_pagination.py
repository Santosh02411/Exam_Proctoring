import pytest

from tests.conftest import register_and_verify, login


@pytest.fixture()
def admin_with_many_students(client, app):
    register_and_verify(client, app, "Admin", "adminp@test.com", "9000000060", "admin", "Adminpass1!")
    for i in range(25):
        register_and_verify(
            client, app, f"Student {i:02d}", f"student{i:02d}@test.com", f"90000001{i:02d}", "student", "Studpass1!"
        )
    login(client, "adminp@test.com", "Adminpass1!")
    client.post(
        "/admin/tests/create",
        data=dict(test_code="PAGE1", title="Pagination Test", description="d", duration_minutes=20,
                   total_questions=1, passing_marks=1, status="published", max_attempts=1,
                   negative_marks_per_wrong=0),
    )
    with app.app_context():
        from app.models import Test
        test = Test.query.filter_by(test_code="PAGE1").first()
    return {"test_id": test.id}


def test_assign_students_page_is_paginated(client, app, admin_with_many_students):
    test_id = admin_with_many_students["test_id"]
    r = client.get(f"/admin/tests/{test_id}/assign")
    assert r.status_code == 200
    # Default PER_PAGE is 15; with 25 students there should be a "Page 1 of 2" indicator.
    assert b"Page 1 of 2" in r.data


def test_assign_students_search_filters_list(client, app, admin_with_many_students):
    test_id = admin_with_many_students["test_id"]
    r = client.get(f"/admin/tests/{test_id}/assign?q=student00")
    assert r.status_code == 200
    assert b"student00@test.com" in r.data
    assert b"student01@test.com" not in r.data


def test_assigned_students_excluded_from_picker(client, app, admin_with_many_students):
    test_id = admin_with_many_students["test_id"]
    with app.app_context():
        from app.models import User
        student0 = User.query.filter_by(email="student00@test.com").first()

    client.post(f"/admin/tests/{test_id}/assign", data={"student_ids": [str(student0.id)]})

    r = client.get(f"/admin/tests/{test_id}/assign?q=student00")
    # Once assigned, student00 should no longer appear in the "add students" picker
    # (it now only shows in "Currently assigned").
    assert b"No unassigned students match" in r.data


def test_second_page_shows_remaining_students(client, app, admin_with_many_students):
    test_id = admin_with_many_students["test_id"]
    r = client.get(f"/admin/tests/{test_id}/assign?page=2")
    assert r.status_code == 200
    assert b"Page 2 of 2" in r.data
