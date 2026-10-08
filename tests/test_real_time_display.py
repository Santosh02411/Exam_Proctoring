"""Real (viewer-local) time rendering — see the local_time() macro in
_components.html and applyLocalTimes() in base.html. Server-rendered pages
must always show an explicit, honest UTC fallback (correct even with no JS),
tagged so the client can upgrade it to the viewer's real local time."""
from datetime import datetime, timedelta

from tests._review_helpers import make_world, start_attempt, terminate_by_violations
from tests.conftest import register_and_verify, login, add_single_question


def _has_localtime_tag(html, iso_prefix, fmt=None):
    needle = f'class="localtime" data-utc="{iso_prefix}'
    if needle not in html:
        return False
    if fmt is not None:
        return f'data-fmt="{fmt}"' in html
    return True


def test_base_template_defines_apply_local_times_once(client, app):
    w = make_world(app, "rt1")
    html = w["admin"].get("/admin/dashboard").data.decode()
    assert html.count("function applyLocalTimes(") == 1
    assert "localtime" in html  # the helper is actually wired up


def test_student_calendar_shows_tagged_utc_time_for_a_scheduled_test(client, app):
    start = datetime.utcnow().replace(second=0, microsecond=0) + timedelta(days=1)
    w = make_world(app, "rt2")
    w["admin"].post(
        f"/admin/tests/{w['test_id']}/edit",
        data=dict(test_code="TRT2", title="Exam rt2", description="d", duration_minutes=30, total_questions=1,
                   passing_marks=1, status="published", max_attempts=1, negative_marks_per_wrong=0,
                   start_time=start.strftime("%Y-%m-%dT%H:%M")),
    )
    html = w["students"][1]["client"].get("/student/calendar").data.decode()
    assert _has_localtime_tag(html, start.strftime("%Y-%m-%dT%H:%M"), fmt="time")
    assert f'{start.strftime("%H:%M")} UTC' in html  # honest no-JS fallback text


def test_student_dashboard_shows_opens_closes_window(client, app):
    start = datetime.utcnow().replace(second=0, microsecond=0) + timedelta(days=1)
    end = start + timedelta(days=5)
    w = make_world(app, "rt3")
    w["admin"].post(
        f"/admin/tests/{w['test_id']}/edit",
        data=dict(test_code="TRT3", title="Exam rt3", description="d", duration_minutes=30, total_questions=1,
                   passing_marks=1, status="published", max_attempts=1, negative_marks_per_wrong=0,
                   start_time=start.strftime("%Y-%m-%dT%H:%M"), end_time=end.strftime("%Y-%m-%dT%H:%M")),
    )
    html = w["students"][1]["client"].get("/student/dashboard").data.decode()
    assert "Opens" in html and "Closes" in html
    assert _has_localtime_tag(html, start.strftime("%Y-%m-%dT%H:%M"))
    assert _has_localtime_tag(html, end.strftime("%Y-%m-%dT%H:%M"))


def test_review_queue_started_column_is_real_time_tagged(client, app):
    w = make_world(app, "rt4")
    a1 = start_attempt(app, w)
    terminate_by_violations(w, a1)
    html = w["admin"].get("/admin/review-queue").data.decode()
    assert 'class="localtime"' in html
    assert 'data-fmt="datetime"' in html or 'time class="localtime"' in html


def test_attempt_detail_started_and_ended_are_real_time_tagged(client, app):
    w = make_world(app, "rt5")
    a1 = start_attempt(app, w)
    terminate_by_violations(w, a1)
    html = w["admin"].get(f"/admin/attempts/{a1}").data.decode()
    assert html.count('class="localtime"') >= 2  # Started + Ended


def test_appeal_submitted_timestamp_is_real_time_tagged_for_student_and_admin(client, app):
    w = make_world(app, "rt6")
    a1 = start_attempt(app, w)
    terminate_by_violations(w, a1)
    w["students"][1]["client"].post(f"/student/attempts/{a1}/appeal", data={"reason": "x" * 30})

    student_html = w["students"][1]["client"].get(f"/student/attempts/{a1}/appeal").data.decode()
    assert 'class="localtime"' in student_html

    admin_html = w["admin"].get("/admin/appeals").data.decode()
    assert 'class="localtime"' in admin_html


def test_notification_history_when_column_is_real_time_tagged(client, app):
    w = make_world(app, "rt7")
    # make_world's own setup (sign-ins etc.) may already have logged some
    # notifications, so check growth rather than assuming an empty start —
    # and assign a brand-new test (the shared one is already assigned, so
    # re-posting the same assignment is a no-op that sends nothing new).
    before = w["admin"].get("/admin/notifications").data.decode().count('class="localtime"')

    w["admin"].post("/admin/tests/create", data=dict(
        test_code="TRT7FRESH", title="Fresh exam rt7", description="d", duration_minutes=10,
        total_questions=1, passing_marks=1, status="published", max_attempts=1, negative_marks_per_wrong=0))
    from app.models import Test
    with app.app_context():
        fresh_id = Test.query.filter_by(test_code="TRT7FRESH").first().id
    w["admin"].post(
        f"/admin/tests/{fresh_id}/assign",
        data={"student_ids": [str(w["students"][1]["id"])], "notify": "on"},
    )
    html = w["admin"].get("/admin/notifications").data.decode()
    assert html.count('class="localtime"') > before
