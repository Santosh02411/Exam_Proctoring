import json

from tests.conftest import register_and_verify, login


def _start_page(client, app, **config):
    app.config.update(config)
    register_and_verify(client, app, "Admin", "admin@test.com", "9000000010", "admin", "Adminpass1!")
    register_and_verify(client, app, "Student", "student@test.com", "9000000011", "student", "Studpass1!")
    login(client, "admin@test.com", "Adminpass1!")
    client.post("/admin/tests/create", data=dict(
        test_code="T1", title="Sample Test", description="d", duration_minutes=30,
        total_questions=1, passing_marks=1, status="published", max_attempts=1,
        negative_marks_per_wrong=0, allow_review="y"), follow_redirects=True)
    with app.app_context():
        from app.models import Test, User
        test = Test.query.filter_by(test_code="T1").first()
        student = User.query.filter_by(email="student@test.com").first()
        test_id, student_id = test.id, student.id
    client.post(f"/admin/tests/{test_id}/assign", data={"student_ids": [str(student_id)]})
    client.get("/logout")
    login(client, "student@test.com", "Studpass1!")
    client.post("/api/proctor/enroll-face", data=json.dumps({"descriptor": [0.01 * i for i in range(128)]}),
                content_type="application/json")
    return client.get(f"/student/tests/{test_id}/start", follow_redirects=True)


def test_precheck_has_share_screen_button_and_requires_it_by_default(client, app):
    r = _start_page(client, app)
    html = r.data.decode()
    assert 'id="pcShareScreen"' in html
    assert "requireScreenShare: true" in html
    assert "requireFullscreen: true" in html
    assert 'id="screenShareLostOverlay"' in html
    assert 'id="fullscreenLostOverlay"' in html


def test_screen_share_requirement_can_be_turned_off(client, app):
    r = _start_page(client, app, REQUIRE_SCREEN_SHARE=False)
    assert "requireScreenShare: false" in r.data.decode()


def test_face_api_is_served_locally_and_not_deferred(client, app):
    """proctor.js calls faceapi.* as soon as it runs, so the library must be a
    plain synchronous <script> (a deferred/CDN copy was always undefined by
    then, which showed "Face model: Failed to load")."""
    import re
    r = _start_page(client, app)
    html = r.data.decode()
    tag = re.search(r'<script[^>]*face-api[^>]*>', html).group(0)
    assert "defer" not in tag and "cdn.jsdelivr" not in tag
    assert client.get("/static/vendor/face-api.min.js").status_code == 200
    # ...and it comes before proctor.js
    assert html.index("face-api.min.js") < html.index("js/proctor.js")


def test_sidebar_has_light_theme_styles():
    import pathlib
    css = pathlib.Path("app/static/css/style.css").read_text()
    assert '[data-theme="light"] .sidebar' in css
