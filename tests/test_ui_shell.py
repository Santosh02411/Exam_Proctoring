"""The app shell (sidebar for staff, top bar for students, split-screen auth),
its live counts, and the security/self-hosting properties of the new UI."""
import re

from tests._review_helpers import (
    make_world, start_attempt, terminate_by_violations, _client_for,
)
from tests.conftest import add_single_question


def _count_for(html, label):
    """The number shown in the sidebar badge next to a nav label, or None."""
    m = re.search(r'<a class="side-link"[^>]*>(?:(?!</a>).)*?<span>' + re.escape(label) + r'</span>\s*<span class="side-count[^"]*">(\d+)</span>', html, re.S)
    return int(m.group(1)) if m else None


def test_admin_sidebar_has_every_section(client, app):
    w = make_world(app, "ui1")
    html = w["admin"].get("/admin/dashboard").data.decode()
    assert 'class="sidebar"' in html and 'class="topnav"' not in html
    for label in ("Dashboard", "Live monitor", "Review queue", "Appeals", "Manage tests", "New test", "Question bank",
                  "Calendar", "Analytics", "Users", "ID verification", "Organization settings", "Log out"):
        assert label in html, label
    for path in ("/admin/live", "/admin/appeals", "/admin/review-queue", "/admin/tests"):
        assert f'href="{path}"' in html


def test_proctor_sidebar_is_limited_to_review_work(client, app):
    w = make_world(app, "ui2")
    html = w["proctor"].get("/admin/review-queue").data.decode()
    for label in ("Live monitor", "Review queue", "Appeals", "Analytics"):
        assert label in html
    for label in ("Manage tests", "New test", "Users", "Organization settings", "Question bank"):
        assert label not in html, label


def test_student_gets_top_bar_not_sidebar(client, app):
    w = make_world(app, "ui3")
    html = w["students"][1]["client"].get("/student/dashboard").data.decode()
    assert 'class="topnav"' in html and 'class="sidebar"' not in html
    assert "My Tests" in html and "Identity Verification" in html
    assert "Live monitor" not in html and "Review queue" not in html


def test_sidebar_counts_track_live_flagged_and_appeals(client, app):
    w = make_world(app, "ui4")
    admin = w["admin"]

    def counts():
        html = admin.get("/admin/dashboard").data.decode()
        return {k: _count_for(html, k) for k in ("Live monitor", "Review queue", "Appeals")}

    assert counts() == {"Live monitor": None, "Review queue": None, "Appeals": None}  # zero shows no badge

    a1 = start_attempt(app, w, 1)
    assert counts()["Live monitor"] == 1

    a2 = start_attempt(app, w, 2)
    terminate_by_violations(w, a2, 2)
    c = counts()
    assert c["Live monitor"] == 1 and c["Review queue"] == 1

    w["students"][2]["client"].post(f"/student/attempts/{a2}/appeal", data={"reason": "The flagged behaviour was a technical fault."})
    assert counts()["Appeals"] == 1

    # A recorded decision takes the attempt out of the "to review" count.
    w["proctor"].post(f"/admin/attempts/{a2}/review", data={"decision": "cleared", "notes": ""})
    assert counts()["Review queue"] is None


def test_pulse_cards_on_dashboard_match_counts(client, app):
    w = make_world(app, "ui5")
    a1 = start_attempt(app, w, 1)
    a2 = start_attempt(app, w, 2)
    terminate_by_violations(w, a2, 2)
    html = w["admin"].get("/admin/dashboard").data.decode()
    assert "Exams in progress" in html and "Flagged attempts to review" in html and "Appeals awaiting a decision" in html
    assert "Highest-risk cases awaiting review" in html and "Student2" in html  # the terminated one is listed


def test_fonts_are_self_hosted(client, app):
    for name in ("newsreader-latin-wght-normal.woff2", "instrument-sans-latin-wght-normal.woff2", "ibm-plex-mono-latin-400-normal.woff2"):
        r = client.get(f"/static/fonts/{name}")
        assert r.status_code == 200 and len(r.data) > 5000, name
    css = client.get("/static/css/style.css").data.decode()
    assert "fonts.googleapis.com" not in css and "fonts.gstatic.com" not in css
    login = client.get("/login").data.decode()
    assert "fonts.googleapis.com" not in login and "fonts.gstatic.com" not in login


def test_login_and_register_use_split_screen(client, app):
    for path in ("/login", "/register"):
        html = client.get(path).data.decode()
        assert 'class="auth-shell"' in html and "Fair exams, with a paper trail." in html
    assert "Quick check:" in client.get("/login").data.decode()


def test_live_alert_bell_never_builds_html_from_alert_data(client, app):
    """The bell used innerHTML with student names/labels — a student could
    register as <img onerror=...> and run script in a proctor's browser."""
    w = make_world(app, "ui6")
    html = w["proctor"].get("/admin/review-queue").data.decode()
    script = html[html.index("proctorAlertBell"):]
    assert "innerHTML" not in script
    assert "textContent" in script


def test_user_supplied_name_is_escaped_in_the_shell(client, app):
    evil = "<img src=x onerror=alert(1)>"
    c = _client_for(app, evil, "evil_ui7@test.com", "proctor")
    html = c.get("/admin/review-queue").data.decode()
    assert evil not in html
    assert "&lt;img src=x onerror=alert(1)&gt;" in html


def test_student_dashboard_puts_the_exam_in_progress_first(client, app):
    w = make_world(app, "ui8")
    admin = w["admin"]
    admin.post("/admin/tests/create", data=dict(
        test_code="SECOND8", title="Second exam ui8", description="d", duration_minutes=30, total_questions=1,
        passing_marks=1, status="published", max_attempts=1, negative_marks_per_wrong=0))
    from app.models import Test
    with app.app_context():
        second_id = Test.query.filter_by(test_code="SECOND8").first().id
    add_single_question(admin, second_id, "Q1", "1", "2", "3", "4", "a", marks=1)
    admin.post(f"/admin/tests/{second_id}/assign", data={"student_ids": [str(w["students"][1]["id"])]})

    s = w["students"][1]["client"]
    before = s.get("/student/dashboard").data.decode()
    assert before.index("Exam ui8") < before.index("Second exam ui8")   # assignment order

    s.get(f"/student/tests/{second_id}/start")                          # begin the second one
    after = s.get("/student/dashboard").data.decode()
    assert after.index("Second exam ui8") < after.index("Exam ui8")     # now it leads
    assert "Resume Test" in after


def test_result_screen_shows_score_and_pass_mark(client, app):
    w = make_world(app, "ui9")
    a1 = start_attempt(app, w, 1)
    c = w["students"][1]["client"]
    c.post(f"/student/attempts/{a1}/submit", data={})
    html = c.get(f"/student/attempts/{a1}/result").data.decode()
    assert "NOT PASSED" in html and "Passing marks required" in html and 'class="score-bar"' in html
