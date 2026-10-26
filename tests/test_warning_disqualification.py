"""Warning-based disqualification: a student may accumulate up to a limit of
warnings (any type combined) before the *next* one ends their attempt — see
Test.max_warnings and app.proctoring._record_violation. Independent of the
older per-event-type Customizable Warning System and of the plain
violation-count/MAX_VIOLATIONS_BEFORE_TERMINATION mechanism, both of which
keep working unchanged."""
import json
import os
import sqlite3

import pytest

from tests._review_helpers import make_world, start_attempt, get_attempt
from tests.conftest import TestConfig


def _warn(world, attempt_id, event_type="tab_hidden", n=1, details=""):
    return world["students"][n]["client"].post(
        "/api/proctor/event",
        data=json.dumps({"attempt_id": attempt_id, "event_type": event_type, "severity": "warning", "details": details}),
        content_type="application/json",
    ).get_json()


def _set_max_warnings(app, test_id, value):
    from app import db
    from app.models import Test
    with app.app_context():
        Test.query.get(test_id).max_warnings = value
        db.session.commit()


def test_platform_default_is_two_warnings_then_disqualified(client, app):
    w = make_world(app, "wd1")
    a1 = start_attempt(app, w)

    r1 = _warn(w, a1, details="Looked away")
    assert r1["terminated"] is False
    assert "1 warning" in r1["message"] and "left before you are disqualified" in r1["message"]

    r2 = _warn(w, a1, event_type="no_face", details="Face not visible")
    assert r2["terminated"] is False
    assert "Final warning" in r2["message"]

    r3 = _warn(w, a1, event_type="audio_violation", details="Voices detected")
    assert r3["terminated"] is True
    assert "Disqualified" in r3["message"] and "2 warnings" in r3["message"]

    state = get_attempt(app, a1)
    assert state["status"] == "terminated"
    assert "Disqualified after using all 2 warnings" in state["reason"]


def test_total_warning_count_persists_and_stops_at_the_limit(client, app):
    w = make_world(app, "wd2")
    a1 = start_attempt(app, w)
    from app.models import Attempt
    with app.app_context():
        assert Attempt.query.get(a1).total_warning_count == 0
    _warn(w, a1)
    with app.app_context():
        assert Attempt.query.get(a1).total_warning_count == 1
    _warn(w, a1, event_type="no_face")
    with app.app_context():
        assert Attempt.query.get(a1).total_warning_count == 2
    _warn(w, a1, event_type="audio_violation")  # disqualifying occurrence
    with app.app_context():
        # the disqualifying occurrence itself is not counted as a 3rd warning
        assert Attempt.query.get(a1).total_warning_count == 2


def test_per_test_override_lowers_the_limit(client, app):
    w = make_world(app, "wd3")
    _set_max_warnings(app, w["test_id"], 1)
    a1 = start_attempt(app, w)

    r1 = _warn(w, a1)
    assert r1["terminated"] is False and "Final warning" in r1["message"]
    r2 = _warn(w, a1, event_type="no_face")
    assert r2["terminated"] is True
    assert get_attempt(app, a1)["status"] == "terminated"


def test_per_test_zero_disables_warning_disqualification(client, app):
    w = make_world(app, "wd4")
    _set_max_warnings(app, w["test_id"], 0)
    a1 = start_attempt(app, w)

    for i in range(6):
        r = _warn(w, a1, details=f"warning #{i}")
        assert r["terminated"] is False
    assert get_attempt(app, a1)["status"] == "in_progress"
    from app.models import Attempt
    with app.app_context():
        # disabled means the counter isn't even bumped for this test
        assert Attempt.query.get(a1).total_warning_count == 0


def test_disqualification_is_independent_of_real_violation_count(client, app):
    """A real violation-severity event still counts toward
    MAX_VIOLATIONS_BEFORE_TERMINATION on its own, unaffected by warnings."""
    w = make_world(app, "wd5")
    a1 = start_attempt(app, w)
    _warn(w, a1)  # 1 of 2 warnings used — does not touch violation_count
    r = w["students"][1]["client"].post(
        "/api/proctor/event",
        data=json.dumps({"attempt_id": a1, "event_type": "tab_hidden", "severity": "violation"}),
        content_type="application/json",
    ).get_json()
    assert r["terminated"] is False
    assert get_attempt(app, a1)["violations"] == 1


def test_disqualifying_event_is_logged_as_a_violation_not_a_warning(client, app):
    w = make_world(app, "wd6")
    a1 = start_attempt(app, w)
    _warn(w, a1)
    _warn(w, a1, event_type="no_face")
    _warn(w, a1, event_type="audio_violation")  # disqualifies
    from app.models import ProctoringEvent
    with app.app_context():
        last = ProctoringEvent.query.filter_by(attempt_id=a1, event_type="audio_violation").order_by(ProctoringEvent.id.desc()).first()
        assert last.severity == "violation"
    assert get_attempt(app, a1)["violations"] == 1  # the disqualifying occurrence counts as one real violation


def test_result_page_and_report_show_disqualification(client, app):
    w = make_world(app, "wd7")
    a1 = start_attempt(app, w)
    _warn(w, a1)
    _warn(w, a1, event_type="no_face")
    _warn(w, a1, event_type="audio_violation")
    result_html = w["students"][1]["client"].get(f"/student/attempts/{a1}/result").data.decode()
    assert "Disqualified after using all 2 warnings" in result_html

    pdf = w["admin"].get(f"/admin/attempts/{a1}/report.pdf")
    assert pdf.status_code == 200
    pypdf = pytest.importorskip("pypdf")
    import io
    text = "\n".join(p.extract_text() for p in pypdf.PdfReader(io.BytesIO(pdf.data)).pages)
    assert "Warnings used" in text and "Disqualified after using all 2 warnings" in text


def test_attempt_detail_page_shows_warning_tally(client, app):
    w = make_world(app, "wd8")
    a1 = start_attempt(app, w)
    _warn(w, a1)
    html = w["admin"].get(f"/admin/attempts/{a1}").data.decode()
    assert "Warnings" in html and "1 / 2" in html


def test_admin_can_configure_max_warnings_via_test_form(client, app):
    w = make_world(app, "wd9")
    r = w["admin"].post(
        f"/admin/tests/{w['test_id']}/edit",
        data=dict(test_code=f"TWD9", title="Exam wd9", description="d", duration_minutes=30, total_questions=1,
                   passing_marks=1, status="published", max_attempts=1, negative_marks_per_wrong=0,
                   max_warnings="3"),
        follow_redirects=True,
    )
    assert r.status_code == 200
    from app.models import Test
    with app.app_context():
        assert Test.query.get(w["test_id"]).max_warnings == 3

    # Leaving it blank clears the override back to "use platform default".
    w["admin"].post(
        f"/admin/tests/{w['test_id']}/edit",
        data=dict(test_code="TWD9", title="Exam wd9", description="d", duration_minutes=30, total_questions=1,
                   passing_marks=1, status="published", max_attempts=1, negative_marks_per_wrong=0, max_warnings=""),
    )
    with app.app_context():
        assert Test.query.get(w["test_id"]).max_warnings is None


def test_student_facing_message_flows_through_to_termination_alert(client, app):
    """The exam page's endExam()/submitExam() now receives the server's
    specific message instead of a generic one — see proctor.js."""
    with open("app/static/js/proctor.js") as f:
        js = f.read()
    assert "endExam('terminated', data.message)" in js
    assert "submitExam(message || 'Your attempt has been terminated" in js


def test_migration_adds_columns_to_a_pre_existing_database(tmp_path):
    """db.create_all() only creates whole missing tables; app.__init__'s
    _ensure_columns must patch columns added to tables that already
    existed on a previously-deployed database, or upgrading would crash
    every query that touches Test.max_warnings / Attempt.total_warning_count."""
    from app import create_app, db

    db_path = str(tmp_path / "old.db")

    class OldConfig(TestConfig):
        SQLALCHEMY_DATABASE_URI = f"sqlite:///{db_path}"

    create_app(config_object=OldConfig())  # builds a fresh DB with the columns

    # Simulate a database from before these columns existed by dropping them.
    con = sqlite3.connect(db_path)
    cur = con.cursor()
    for table, column in (("tests", "max_warnings"), ("attempts", "total_warning_count")):
        cols = [r[1] for r in cur.execute(f"PRAGMA table_info({table})") if r[1] != column]
        cur.execute(f"CREATE TABLE {table}_old AS SELECT {', '.join(cols)} FROM {table}")
        cur.execute(f"DROP TABLE {table}")
        cur.execute(f"ALTER TABLE {table}_old RENAME TO {table}")
    con.commit()
    con.close()

    con = sqlite3.connect(db_path)
    cols = [r[1] for r in con.execute("PRAGMA table_info(tests)")]
    con.close()
    assert "max_warnings" not in cols  # confirm the simulated "old" DB really lacks it

    app2 = create_app(config_object=OldConfig())
    with app2.app_context():
        from app.models import Test, Attempt
        Test.query.count()      # would raise OperationalError if the column were still missing
        Attempt.query.count()
