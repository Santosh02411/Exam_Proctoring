import io

import pytest

from tests._review_helpers import make_world, other_org_admin, start_attempt, terminate_by_violations, get_attempt


def _terminated(app, w, n=1):
    attempt_id = start_attempt(app, w, n)
    terminate_by_violations(w, attempt_id, n)
    assert get_attempt(app, attempt_id)["status"] == "terminated"
    return attempt_id


def _review(client, attempt_id, decision, notes="", follow=False):
    return client.post(f"/admin/attempts/{attempt_id}/review", data={"decision": decision, "notes": notes},
                       follow_redirects=follow)


def _review_row(app, attempt_id):
    from app.models import AttemptReview
    with app.app_context():
        r = AttemptReview.query.filter_by(attempt_id=attempt_id).first()
        return None if r is None else {"decision": r.decision, "notes": r.notes, "reviewer": r.reviewer.name}


def test_cleared_needs_no_notes_and_is_recorded(client, app):
    w = make_world(app, "rv1")
    a1 = _terminated(app, w)
    r = _review(w["proctor"], a1, "cleared", follow=True)
    assert b"Review saved" in r.data
    assert _review_row(app, a1) == {"decision": "cleared", "notes": None, "reviewer": "Proctor"}


@pytest.mark.parametrize("decision", ["confirmed", "escalated"])
def test_confirmed_and_escalated_require_written_reasoning(client, app, decision):
    w = make_world(app, f"rv2{decision[:3]}")
    a1 = _terminated(app, w)
    r = _review(w["proctor"], a1, decision, notes="   ", follow=True)
    assert b"write down your reasoning" in r.data
    assert _review_row(app, a1) is None
    _review(w["proctor"], a1, decision, notes="Second device visible in the recording at 12:04.")
    assert _review_row(app, a1)["decision"] == decision


def test_invalid_decision_is_rejected(client, app):
    w = make_world(app, "rv3")
    a1 = _terminated(app, w)
    _review(w["proctor"], a1, "banned-for-life", notes="x")
    assert _review_row(app, a1) is None


def test_re_review_updates_in_place_and_is_logged(client, app):
    w = make_world(app, "rv4")
    a1 = _terminated(app, w)
    _review(w["proctor"], a1, "escalated", notes="Unclear — needs the admin's call.")
    _review(w["admin"], a1, "cleared", notes="Reviewed the footage; the second person was a passer-by.")
    from app.models import AttemptReview, AdminActivityLog
    with app.app_context():
        assert AttemptReview.query.filter_by(attempt_id=a1).count() == 1
        row = _review_row(app, a1)
        assert row["decision"] == "cleared" and row["reviewer"] == "Admin"
        actions = [l.action for l in AdminActivityLog.query.order_by(AdminActivityLog.id).all()]
        assert "reviewed_attempt_escalated" in actions and "reviewed_attempt_cleared" in actions
        assert any("escalated → cleared" in l.description for l in AdminActivityLog.query.all())


def test_cannot_review_in_progress_attempt(client, app):
    w = make_world(app, "rv5")
    a1 = start_attempt(app, w, 1)
    r = _review(w["proctor"], a1, "cleared", follow=True)
    assert b"still in progress" in r.data
    assert _review_row(app, a1) is None


def test_attempt_page_shows_form_then_decision(client, app):
    w = make_world(app, "rv6")
    a1 = _terminated(app, w)
    page = w["proctor"].get(f"/admin/attempts/{a1}").data
    assert b"Record a decision" in page and b"report.pdf" in page
    _review(w["proctor"], a1, "confirmed", notes="Clear evidence of a second screen.")
    page = w["proctor"].get(f"/admin/attempts/{a1}").data
    assert b"Confirmed" in page and b"Clear evidence of a second screen." in page and b"Update decision" in page


def test_review_queue_filters_by_review_status(client, app):
    w = make_world(app, "rv7")
    a1 = _terminated(app, w, 1)
    a2 = _terminated(app, w, 2)
    _review(w["proctor"], a1, "cleared")

    def ids_shown(query):
        html = w["proctor"].get(f"/admin/review-queue{query}").data.decode()
        return {a: f"/admin/attempts/{a}" in html for a in (a1, a2)}

    assert ids_shown("") == {a1: True, a2: True}
    assert ids_shown("?review=pending") == {a1: False, a2: True}
    assert ids_shown("?review=cleared") == {a1: True, a2: False}
    assert ids_shown("?review=confirmed") == {a1: False, a2: False}
    assert ids_shown("?review=nonsense") == {a1: True, a2: True}  # unknown filter -> everything
    html = w["proctor"].get("/admin/review-queue").data.decode()
    assert "Cleared" in html and "Not reviewed" in html


def test_appeals_page_shows_reviewer_decision(client, app):
    w = make_world(app, "rv8")
    a1 = _terminated(app, w)
    _review(w["proctor"], a1, "confirmed", notes="Second screen visible.")
    w["students"][1]["client"].post(f"/student/attempts/{a1}/appeal",
                                    data={"reason": "The second screen was switched off, I can prove it."})
    html = w["proctor"].get("/admin/appeals").data.decode()
    assert "Reviewer decision" in html and "Confirmed" in html


def test_review_and_report_are_role_and_org_scoped(client, app):
    w = make_world(app, "rv9")
    a1 = _terminated(app, w)
    student = w["students"][2]["client"]
    assert _review(student, a1, "cleared").status_code == 403
    assert student.get(f"/admin/attempts/{a1}/report.pdf").status_code == 403

    outsider = other_org_admin(app, "rv9")
    assert _review(outsider, a1, "cleared").status_code == 403
    assert outsider.get(f"/admin/attempts/{a1}/report.pdf").status_code == 403
    assert _review_row(app, a1) is None


# ---------------- incident report PDF ----------------

def _pdf_text(data):
    pypdf = pytest.importorskip("pypdf")
    reader = pypdf.PdfReader(io.BytesIO(data))
    return "\n".join(page.extract_text() for page in reader.pages)


def test_incident_report_pdf_contains_the_evidence(client, app):
    w = make_world(app, "pdf1")
    a1 = _terminated(app, w)
    _review(w["proctor"], a1, "confirmed", notes="Second screen visible in the recording.")
    w["students"][1]["client"].post(f"/student/attempts/{a1}/appeal",
                                    data={"reason": "The second screen was switched off, I can prove it."})
    r = w["proctor"].get(f"/admin/attempts/{a1}/report.pdf")
    assert r.status_code == 200
    assert r.mimetype == "application/pdf"
    assert r.data.startswith(b"%PDF")
    assert f"incident_report_attempt{a1}_" in r.headers["Content-Disposition"]

    text = _pdf_text(r.data)
    for expected in ("Proctoring Incident Report", "Student1", "Exam pdf1", "Terminated", "Exceeded 5 proctoring violations",
                     "Confirmed", "Second screen visible in the recording.", "Student appeal",
                     "The second screen was switched off", "tab hidden", "Proctoring event log (5)"):
        assert expected in text, f"missing from PDF: {expected!r}"

    from app.models import AdminActivityLog
    with app.app_context():
        assert AdminActivityLog.query.filter_by(action="exported_attempt_report").count() == 1


def test_incident_report_survives_markup_in_user_text(client, app):
    """Paragraph() parses XML-ish markup — unescaped '<' or '&' in a note or an appeal would crash the export."""
    w = make_world(app, "pdf2")
    a1 = _terminated(app, w)
    nasty_note = "Saw <b>unclosed & <i>weird</font> markup </para> here"
    _review(w["proctor"], a1, "escalated", notes=nasty_note)
    w["students"][1]["client"].post(f"/student/attempts/{a1}/appeal",
                                    data={"reason": "I typed <script>alert(1)</script> & <b>bold</b> by accident."})
    r = w["admin"].get(f"/admin/attempts/{a1}/report.pdf")
    assert r.status_code == 200 and r.data.startswith(b"%PDF")
    text = _pdf_text(r.data)
    assert "<script>alert(1)</script>" in text          # rendered literally, not interpreted
    assert "unclosed & <i>weird</font>" in text


def test_incident_report_for_clean_unreviewed_attempt(client, app):
    w = make_world(app, "pdf3")
    a1 = start_attempt(app, w, 1)
    w["students"][1]["client"].post(f"/student/attempts/{a1}/submit", data={})
    r = w["admin"].get(f"/admin/attempts/{a1}/report.pdf")
    assert r.status_code == 200
    text = _pdf_text(r.data)
    assert "Not yet reviewed." in text and "No proctoring events were recorded." in text
