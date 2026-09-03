from tests.conftest import register_and_verify, login

def test_create_test_shows_validation_errors_when_fields_missing(client, app):
    register_and_verify(client, app, "Admin", "adminX@test.com", "9000000099", "admin", "Adminpass1!")
    login(client, "adminX@test.com", "Adminpass1!")

    # Missing duration_minutes (required) — this used to fail with NO
    # visible error anywhere on the page.
    r = client.post("/admin/tests/create", data=dict(
        test_code="ERR1", title="Broken Test", description="d",
        total_questions=1, passing_marks=1, status="published", max_attempts=1,
        negative_marks_per_wrong=0,
        # duration_minutes intentionally omitted
    ))
    assert r.status_code == 200  # re-renders the form, doesn't redirect
    body = r.get_data(as_text=True)
    assert "This field is required" in body or "errors" in body.lower()
    assert "flash-error" in body or 'class="errors"' in body
