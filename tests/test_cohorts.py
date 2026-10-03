from tests.conftest import register_and_verify, login, add_single_question


def test_create_cohort_and_add_students(client, app):
    register_and_verify(client, app, "Admin", "coha1@test.com", "9111199001", "admin", "Adminpass1!")
    register_and_verify(client, app, "Student1", "cohs1@test.com", "9111199002", "student", "Studpass1!")
    register_and_verify(client, app, "Student2", "cohs2@test.com", "9111199003", "student", "Studpass1!")
    login(client, "coha1@test.com", "Adminpass1!")

    r = client.post("/admin/cohorts/create", data={"name": "Section A", "description": "Morning batch"}, follow_redirects=True)
    assert b"created" in r.data
    with app.app_context():
        from app.models import Cohort
        cohort = Cohort.query.filter_by(name="Section A").first()
        assert cohort is not None
        cohort_id = cohort.id

    with app.app_context():
        from app.models import User
        s1 = User.query.filter_by(email="cohs1@test.com").first().id
        s2 = User.query.filter_by(email="cohs2@test.com").first().id

    r2 = client.post(f"/admin/cohorts/{cohort_id}/add-student", data={"student_ids": [str(s1), str(s2)]}, follow_redirects=True)
    assert b"Added 2 student" in r2.data

    with app.app_context():
        from app.models import Cohort
        cohort = Cohort.query.get(cohort_id)
        assert cohort.member_count == 2


def test_remove_student_from_cohort(client, app):
    register_and_verify(client, app, "Admin", "coha2@test.com", "9111199004", "admin", "Adminpass1!")
    register_and_verify(client, app, "Student", "cohs3@test.com", "9111199005", "student", "Studpass1!")
    login(client, "coha2@test.com", "Adminpass1!")
    client.post("/admin/cohorts/create", data={"name": "Section B"})
    with app.app_context():
        from app.models import Cohort, User
        cohort_id = Cohort.query.filter_by(name="Section B").first().id
        student_id = User.query.filter_by(email="cohs3@test.com").first().id
    client.post(f"/admin/cohorts/{cohort_id}/add-student", data={"student_ids": [str(student_id)]})

    r = client.post(f"/admin/cohorts/{cohort_id}/remove-student/{student_id}", follow_redirects=True)
    assert b"removed from cohort" in r.data
    with app.app_context():
        from app.models import Cohort
        assert Cohort.query.get(cohort_id).member_count == 0


def test_assign_cohort_to_test_creates_eligibility_for_all_members(client, app):
    register_and_verify(client, app, "Admin", "coha3@test.com", "9111199006", "admin", "Adminpass1!")
    register_and_verify(client, app, "Student1", "cohs4@test.com", "9111199007", "student", "Studpass1!")
    register_and_verify(client, app, "Student2", "cohs5@test.com", "9111199008", "student", "Studpass1!")
    login(client, "coha3@test.com", "Adminpass1!")

    client.post("/admin/cohorts/create", data={"name": "Section C"})
    with app.app_context():
        from app.models import Cohort, User
        cohort_id = Cohort.query.filter_by(name="Section C").first().id
        s1 = User.query.filter_by(email="cohs4@test.com").first().id
        s2 = User.query.filter_by(email="cohs5@test.com").first().id
    client.post(f"/admin/cohorts/{cohort_id}/add-student", data={"student_ids": [str(s1), str(s2)]})

    client.post("/admin/tests/create", data=dict(
        test_code="COH1", title="Cohort Assign Test", description="d", duration_minutes=20,
        total_questions=1, passing_marks=1, status="published", max_attempts=1, negative_marks_per_wrong=0))
    with app.app_context():
        from app.models import Test
        test_id = Test.query.filter_by(test_code="COH1").first().id
    add_single_question(client, test_id, "2+2=?", "3", "4", "5", "6", "b", marks=1)

    r = client.post(f"/admin/tests/{test_id}/assign-cohort", data={"cohort_id": cohort_id, "notify": "on"}, follow_redirects=True)
    assert b"Assigned 2 student" in r.data

    with app.app_context():
        from app.models import TestEligibility
        count = TestEligibility.query.filter_by(test_id=test_id).count()
        assert count == 2


def test_assign_cohort_skips_already_assigned_students(client, app):
    register_and_verify(client, app, "Admin", "coha4@test.com", "9111199009", "admin", "Adminpass1!")
    register_and_verify(client, app, "Student", "cohs6@test.com", "9111199010", "student", "Studpass1!")
    login(client, "coha4@test.com", "Adminpass1!")

    client.post("/admin/cohorts/create", data={"name": "Section D"})
    with app.app_context():
        from app.models import Cohort, User
        cohort_id = Cohort.query.filter_by(name="Section D").first().id
        student_id = User.query.filter_by(email="cohs6@test.com").first().id
    client.post(f"/admin/cohorts/{cohort_id}/add-student", data={"student_ids": [str(student_id)]})

    client.post("/admin/tests/create", data=dict(
        test_code="COH2", title="Cohort Skip Test", description="d", duration_minutes=20,
        total_questions=1, passing_marks=1, status="published", max_attempts=1, negative_marks_per_wrong=0))
    with app.app_context():
        from app.models import Test
        test_id = Test.query.filter_by(test_code="COH2").first().id
    client.post(f"/admin/tests/{test_id}/assign", data={"student_ids": [str(student_id)], "notify": "on"})

    r = client.post(f"/admin/tests/{test_id}/assign-cohort", data={"cohort_id": cohort_id}, follow_redirects=True)
    assert b"Assigned 0 student" in r.data


def test_delete_cohort_does_not_affect_existing_assignments(client, app):
    register_and_verify(client, app, "Admin", "coha5@test.com", "9111199011", "admin", "Adminpass1!")
    register_and_verify(client, app, "Student", "cohs7@test.com", "9111199012", "student", "Studpass1!")
    login(client, "coha5@test.com", "Adminpass1!")

    client.post("/admin/cohorts/create", data={"name": "Section E"})
    with app.app_context():
        from app.models import Cohort, User
        cohort_id = Cohort.query.filter_by(name="Section E").first().id
        student_id = User.query.filter_by(email="cohs7@test.com").first().id
    client.post(f"/admin/cohorts/{cohort_id}/add-student", data={"student_ids": [str(student_id)]})

    client.post("/admin/tests/create", data=dict(
        test_code="COH3", title="Cohort Delete Test", description="d", duration_minutes=20,
        total_questions=1, passing_marks=1, status="published", max_attempts=1, negative_marks_per_wrong=0))
    with app.app_context():
        from app.models import Test
        test_id = Test.query.filter_by(test_code="COH3").first().id
    client.post(f"/admin/tests/{test_id}/assign-cohort", data={"cohort_id": cohort_id})

    client.post(f"/admin/cohorts/{cohort_id}/delete")
    with app.app_context():
        from app.models import Cohort, TestEligibility, User
        assert Cohort.query.get(cohort_id) is None
        student_id = User.query.filter_by(email="cohs7@test.com").first().id
        assert TestEligibility.query.filter_by(test_id=test_id, student_id=student_id).first() is not None


def test_cohort_isolated_to_own_org(client, app):
    register_and_verify(client, app, "Admin1", "coha6@test.com", "9111199013", "admin", "Adminpass1!")
    login(client, "coha6@test.com", "Adminpass1!")
    client.post("/admin/cohorts/create", data={"name": "Org1 Section"})
    with app.app_context():
        from app.models import Cohort
        cohort_id = Cohort.query.filter_by(name="Org1 Section").first().id

    client.get("/logout")
    register_and_verify(client, app, "Admin2", "coha7@test.com", "9111199014", "admin", "Adminpass1!")
    with app.app_context():
        from app.models import User, Organization
        from app import db
        other_org = Organization(name="Cohort Other Org", slug="cohort-other-org", status="active")
        db.session.add(other_org)
        db.session.flush()
        admin2 = User.query.filter_by(email="coha7@test.com").first()
        admin2.org_id = other_org.id
        db.session.commit()

    login(client, "coha7@test.com", "Adminpass1!")
    r = client.get(f"/admin/cohorts/{cohort_id}")
    assert r.status_code == 404
