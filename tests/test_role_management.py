import pytest

from tests.conftest import register_and_verify, login, add_single_question


# --- Registration with new roles ------------------------------------------

def test_can_register_as_examiner_and_proctor(client, app):
    register_and_verify(client, app, "Eddy", "eddy_exam@test.com", "9000002001", "examiner", "Examiner1!")
    register_and_verify(client, app, "Priya", "priya_proc@test.com", "9000002002", "proctor", "Proctor1!")
    with app.app_context():
        from app.models import User
        e = User.query.filter_by(email="eddy_exam@test.com").first()
        p = User.query.filter_by(email="priya_proc@test.com").first()
        assert e.role == "examiner"
        assert p.role == "proctor"
        assert e.user_id.startswith("EXA")
        assert p.user_id.startswith("PRO")


# --- Landing page / index() redirect by role -------------------------------

def test_index_redirects_by_role(client, app):
    register_and_verify(client, app, "Eddy2", "eddy2@test.com", "9000002003", "examiner", "Examiner1!")
    register_and_verify(client, app, "Priya2", "priya2@test.com", "9000002004", "proctor", "Proctor1!")
    register_and_verify(client, app, "Stu2", "stu2rbac@test.com", "9000002005", "student", "Studpass1!")

    login(client, "eddy2@test.com", "Examiner1!")
    r = client.get("/", follow_redirects=False)
    assert r.headers["Location"].endswith("/admin/dashboard")
    client.get("/logout")

    login(client, "priya2@test.com", "Proctor1!")
    r = client.get("/", follow_redirects=False)
    assert r.headers["Location"].endswith("/admin/review-queue")
    client.get("/logout")

    login(client, "stu2rbac@test.com", "Studpass1!")
    r = client.get("/", follow_redirects=False)
    assert r.headers["Location"].endswith("/student/dashboard")


# --- RBAC across roles ------------------------------------------------------

@pytest.fixture()
def rbac_users(client, app):
    register_and_verify(client, app, "Admin", "admin_rbac@test.com", "9000002006", "admin", "Adminpass1!")
    register_and_verify(client, app, "Examiner", "examiner_rbac@test.com", "9000002007", "examiner", "Examiner1!")
    register_and_verify(client, app, "Proctor", "proctor_rbac@test.com", "9000002008", "proctor", "Proctor1!")
    register_and_verify(client, app, "Student", "student_rbac@test.com", "9000002009", "student", "Studpass1!")
    return {
        "admin": ("admin_rbac@test.com", "Adminpass1!"),
        "examiner": ("examiner_rbac@test.com", "Examiner1!"),
        "proctor": ("proctor_rbac@test.com", "Proctor1!"),
        "student": ("student_rbac@test.com", "Studpass1!"),
    }


def test_examiner_can_manage_tests_and_bank_but_not_users(client, app, rbac_users):
    email, pw = rbac_users["examiner"]
    login(client, email, pw)

    r = client.get("/admin/dashboard")
    assert r.status_code == 200
    r = client.post(
        "/admin/tests/create",
        data=dict(test_code="RBAC1", title="RBAC Test", description="d", duration_minutes=20,
                   total_questions=1, passing_marks=1, status="draft", max_attempts=1,
                   negative_marks_per_wrong=0),
        follow_redirects=True,
    )
    assert b"Test created" in r.data or b"Now add some questions" in r.data or r.status_code == 200

    r = client.get("/admin/bank")
    assert r.status_code == 200
    r = client.post(
        "/admin/bank/add",
        data=dict(question_type="short", question_text="Examiner bank Q?", short_answer_text="A", marks=1),
        follow_redirects=True,
    )
    assert b"Added to the question bank" in r.data

    # But not user management or the audit log.
    assert client.get("/admin/users").status_code == 403
    assert client.get("/admin/activity-log").status_code == 403


def test_proctor_can_review_but_not_author_content_or_manage_users(client, app, rbac_users):
    admin_email, admin_pw = rbac_users["admin"]
    student_email, student_pw = rbac_users["student"]
    login(client, admin_email, admin_pw)
    client.post(
        "/admin/tests/create",
        data=dict(test_code="RBAC2", title="RBAC Test 2", description="d", duration_minutes=20,
                   total_questions=1, passing_marks=1, status="published", max_attempts=1,
                   negative_marks_per_wrong=0),
    )
    with app.app_context():
        from app.models import Test, User
        test = Test.query.filter_by(test_code="RBAC2").first()
        test_id = test.id
        student = User.query.filter_by(email=student_email).first()
        student_id = student.id
    add_single_question(client, test_id, "Q?", "1", "2", "3", "4", "a", marks=1)
    client.post(f"/admin/tests/{test_id}/assign", data={"student_ids": [str(student_id)]})
    client.get("/logout")

    proctor_email, proctor_pw = rbac_users["proctor"]
    login(client, proctor_email, proctor_pw)

    # Can reach the review queue and results — that's their whole job.
    assert client.get("/admin/review-queue").status_code == 200
    assert client.get(f"/admin/tests/{test_id}/results").status_code == 200

    # Cannot author tests/questions/bank, and cannot manage users.
    assert client.get("/admin/tests/create").status_code == 403
    assert client.get(f"/admin/tests/{test_id}/questions/add").status_code == 403
    assert client.get("/admin/bank").status_code == 403
    assert client.get("/admin/users").status_code == 403
    assert client.get("/admin/activity-log").status_code == 403


def test_student_still_blocked_from_all_admin_routes(client, app, rbac_users):
    email, pw = rbac_users["student"]
    login(client, email, pw)
    assert client.get("/admin/dashboard").status_code == 403
    assert client.get("/admin/review-queue").status_code == 403
    assert client.get("/admin/bank").status_code == 403
    assert client.get("/admin/users").status_code == 403


def test_admin_still_has_full_access(client, app, rbac_users):
    email, pw = rbac_users["admin"]
    login(client, email, pw)
    assert client.get("/admin/dashboard").status_code == 200
    assert client.get("/admin/review-queue").status_code == 200
    assert client.get("/admin/bank").status_code == 200
    assert client.get("/admin/users").status_code == 200
    assert client.get("/admin/activity-log").status_code == 200


def test_manage_users_role_filter_covers_new_roles(client, app, rbac_users):
    admin_email, admin_pw = rbac_users["admin"]
    login(client, admin_email, admin_pw)
    r = client.get("/admin/users?role=examiner")
    assert r.status_code == 200
    assert b"examiner_rbac@test.com" in r.data
    assert b"proctor_rbac@test.com" not in r.data


# --- Profile management -----------------------------------------------------

def test_view_profile_shows_current_details(client, app):
    register_and_verify(client, app, "Nadia", "nadia_prof@test.com", "9000002010", "student", "Studpass1!")
    login(client, "nadia_prof@test.com", "Studpass1!")
    r = client.get("/profile/")
    assert r.status_code == 200
    assert b"nadia_prof@test.com" in r.data
    assert b"9000002010" in r.data


def test_update_profile_name_and_phone(client, app):
    register_and_verify(client, app, "Nadia2", "nadia2_prof@test.com", "9000002011", "student", "Studpass1!")
    login(client, "nadia2_prof@test.com", "Studpass1!")
    r = client.post(
        "/profile/",
        data={"form_name": "profile", "name": "Nadia Updated", "phone": "9000002099"},
        follow_redirects=True,
    )
    assert b"Profile updated" in r.data
    with app.app_context():
        from app.models import User
        u = User.query.filter_by(email="nadia2_prof@test.com").first()
        assert u.name == "Nadia Updated"
        assert u.phone == "9000002099"


def test_change_password_success_and_relogin(client, app):
    register_and_verify(client, app, "Omar", "omar_prof@test.com", "9000002012", "student", "Studpass1!")
    login(client, "omar_prof@test.com", "Studpass1!")
    r = client.post(
        "/profile/",
        data={
            "form_name": "password", "current_password": "Studpass1!",
            "new_password": "Newerpass1!", "confirm_new_password": "Newerpass1!",
        },
        follow_redirects=True,
    )
    assert b"Password changed successfully" in r.data
    client.get("/logout")
    r = login(client, "omar_prof@test.com", "Newerpass1!")
    assert b"My Tests" in r.data


def test_change_password_rejects_wrong_current_password(client, app):
    register_and_verify(client, app, "Rita", "rita_prof@test.com", "9000002013", "student", "Studpass1!")
    login(client, "rita_prof@test.com", "Studpass1!")
    r = client.post(
        "/profile/",
        data={
            "form_name": "password", "current_password": "WrongOne1!",
            "new_password": "Newerpass1!", "confirm_new_password": "Newerpass1!",
        },
        follow_redirects=True,
    )
    assert b"Current password is incorrect" in r.data
    client.get("/logout")
    r = login(client, "rita_prof@test.com", "Studpass1!")
    assert b"My Tests" in r.data


def test_change_password_rejects_mismatched_confirmation(client, app):
    register_and_verify(client, app, "Sam3", "sam3_prof@test.com", "9000002014", "student", "Studpass1!")
    login(client, "sam3_prof@test.com", "Studpass1!")
    r = client.post(
        "/profile/",
        data={
            "form_name": "password", "current_password": "Studpass1!",
            "new_password": "Newerpass1!", "confirm_new_password": "Different1!",
        },
        follow_redirects=True,
    )
    assert b"do not match" in r.data.lower()


def test_change_password_rejects_weak_new_password(client, app):
    register_and_verify(client, app, "Tia", "tia_prof@test.com", "9000002015", "student", "Studpass1!")
    login(client, "tia_prof@test.com", "Studpass1!")
    r = client.post(
        "/profile/",
        data={
            "form_name": "password", "current_password": "Studpass1!",
            "new_password": "weakpass", "confirm_new_password": "weakpass",
        },
        follow_redirects=True,
    )
    assert b"must also include" in r.data.lower()


def test_all_roles_can_reach_their_own_profile(client, app, rbac_users):
    for role_key in ("admin", "examiner", "proctor", "student"):
        email, pw = rbac_users[role_key]
        login(client, email, pw)
        r = client.get("/profile/")
        assert r.status_code == 200
        client.get("/logout")
