from tests.conftest import register_and_verify, login


def test_manage_tests_search_by_title(client, app):
    register_and_verify(client, app, "Admin", "mtsa1@test.com", "9133311001", "admin", "Adminpass1!")
    login(client, "mtsa1@test.com", "Adminpass1!")
    client.post("/admin/tests/create", data=dict(
        test_code="MTS1", title="Biology Midterm", description="d", duration_minutes=20,
        total_questions=1, passing_marks=1, status="published", max_attempts=1, negative_marks_per_wrong=0))
    client.post("/admin/tests/create", data=dict(
        test_code="MTS2", title="Chemistry Final", description="d", duration_minutes=20,
        total_questions=1, passing_marks=1, status="published", max_attempts=1, negative_marks_per_wrong=0))

    r = client.get("/admin/tests?q=Biology")
    assert b"Biology Midterm" in r.data
    assert b"Chemistry Final" not in r.data


def test_manage_tests_search_by_code(client, app):
    register_and_verify(client, app, "Admin", "mtsa2@test.com", "9133311002", "admin", "Adminpass1!")
    login(client, "mtsa2@test.com", "Adminpass1!")
    client.post("/admin/tests/create", data=dict(
        test_code="UNIQUECODE9", title="Something Generic", description="d", duration_minutes=20,
        total_questions=1, passing_marks=1, status="published", max_attempts=1, negative_marks_per_wrong=0))

    r = client.get("/admin/tests?q=UNIQUECODE9")
    assert b"Something Generic" in r.data


def test_manage_tests_search_combined_with_mine_filter(client, app):
    register_and_verify(client, app, "Admin", "mtsa3@test.com", "9133311003", "admin", "Adminpass1!")
    login(client, "mtsa3@test.com", "Adminpass1!")
    client.post("/admin/tests/create", data=dict(
        test_code="MTS3", title="Physics Quiz", description="d", duration_minutes=20,
        total_questions=1, passing_marks=1, status="published", max_attempts=1, negative_marks_per_wrong=0))

    r = client.get("/admin/tests?q=Physics&mine=1")
    assert r.status_code == 200
    assert b"Physics Quiz" in r.data
