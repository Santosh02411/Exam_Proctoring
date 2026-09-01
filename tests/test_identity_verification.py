import io
import json

import pytest

from tests.conftest import register_and_verify, login, add_single_question


def _make_id_image_bytes():
    from PIL import Image, ImageDraw, ImageFont

    img = Image.new("RGB", (600, 300), "white")
    d = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 28)
    except Exception:
        font = ImageFont.load_default()
    d.text((20, 20), "Name: Jordan A Rivera", fill="black", font=font)
    d.text((20, 70), "ID Number: A7788990", fill="black", font=font)
    d.text((20, 120), "DOB: 03/14/2001", fill="black", font=font)

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return buf


@pytest.fixture()
def student_ready(client, app):
    register_and_verify(client, app, "Admin", "adminiv@test.com", "9000000070", "admin", "Adminpass1!")
    register_and_verify(client, app, "Student", "studentiv@test.com", "9000000071", "student", "Studpass1!")
    login(client, "studentiv@test.com", "Studpass1!")
    return {}


def _upload_id(client):
    img_bytes = _make_id_image_bytes()
    return client.post(
        "/api/proctor/id-document/upload",
        data={"id_image": (img_bytes, "id.png"), "doc_type": "government_id"},
        content_type="multipart/form-data",
    )


# ---------- upload + OCR ----------

def test_upload_id_document_extracts_ocr_fields(client, app, student_ready):
    r = _upload_id(client)
    assert r.status_code == 200
    data = r.get_json()
    assert data["ok"] is True
    assert data["doc_id"]
    assert data["ocr"]["name"] and "Rivera" in data["ocr"]["name"]
    assert data["ocr"]["id_number"] == "A7788990"
    assert "2001" in (data["ocr"]["dob"] or "")

    with app.app_context():
        from app.models import IdentityDocument, User
        student = User.query.filter_by(email="studentiv@test.com").first()
        doc = IdentityDocument.query.filter_by(user_id=student.id).first()
        assert doc is not None
        assert doc.review_status == "pending"
        assert doc.ocr_raw_text


def test_upload_id_document_rejects_bad_extension(client, app, student_ready):
    r = client.post(
        "/api/proctor/id-document/upload",
        data={"id_image": (io.BytesIO(b"not an image"), "id.txt"), "doc_type": "government_id"},
        content_type="multipart/form-data",
    )
    assert r.status_code == 400
    assert r.get_json()["ok"] is False


def test_reupload_replaces_document_and_resets_match(client, app, student_ready):
    r1 = _upload_id(client)
    doc_id_1 = r1.get_json()["doc_id"]

    with app.app_context():
        from app import db
        from app.models import IdentityDocument
        doc = db.session.get(IdentityDocument, doc_id_1)
        doc.face_match_passed = True
        doc.review_status = "approved"
        db.session.commit()

    r2 = _upload_id(client)
    doc_id_2 = r2.get_json()["doc_id"]
    assert doc_id_2 == doc_id_1  # same row, upserted

    with app.app_context():
        from app.models import IdentityDocument
        doc = IdentityDocument.query.get(doc_id_1)
        assert doc.face_match_passed is None
        assert doc.review_status == "pending"


# ---------- face match + liveness (confirm-match) ----------

def _descriptor(base):
    return [base + 0.001 * i for i in range(128)]


def test_confirm_match_enrolls_on_match_and_liveness(client, app, student_ready):
    doc_id = _upload_id(client).get_json()["doc_id"]
    desc = _descriptor(0.1)

    r = client.post(
        "/api/proctor/id-document/confirm-match",
        data=json.dumps({
            "doc_id": doc_id, "id_descriptor": desc, "live_descriptor": desc,
            "liveness_passed": True, "blink_count": 2,
        }),
        content_type="application/json",
    )
    assert r.status_code == 200
    data = r.get_json()
    assert data["ok"] is True
    assert data["face_match_passed"] is True
    assert data["enrolled"] is True
    assert data["distance"] == 0.0

    with app.app_context():
        from app.models import User
        student = User.query.filter_by(email="studentiv@test.com").first()
        assert student.face_descriptor is not None
        assert json.loads(student.face_descriptor) == desc


def test_confirm_match_does_not_enroll_on_mismatch(client, app, student_ready):
    doc_id = _upload_id(client).get_json()["doc_id"]
    id_desc = _descriptor(0.1)
    live_desc = _descriptor(5.0)  # far away — clearly a different "person"

    r = client.post(
        "/api/proctor/id-document/confirm-match",
        data=json.dumps({
            "doc_id": doc_id, "id_descriptor": id_desc, "live_descriptor": live_desc,
            "liveness_passed": True, "blink_count": 1,
        }),
        content_type="application/json",
    )
    data = r.get_json()
    assert data["face_match_passed"] is False
    assert data["enrolled"] is False

    with app.app_context():
        from app.models import User
        student = User.query.filter_by(email="studentiv@test.com").first()
        assert student.face_descriptor is None


def test_confirm_match_does_not_enroll_when_liveness_fails(client, app, student_ready):
    doc_id = _upload_id(client).get_json()["doc_id"]
    desc = _descriptor(0.1)

    r = client.post(
        "/api/proctor/id-document/confirm-match",
        data=json.dumps({
            "doc_id": doc_id, "id_descriptor": desc, "live_descriptor": desc,
            "liveness_passed": False, "blink_count": 0,
        }),
        content_type="application/json",
    )
    data = r.get_json()
    assert data["face_match_passed"] is True  # descriptors matched...
    assert data["liveness_passed"] is False
    assert data["enrolled"] is False  # ...but liveness gates enrollment regardless

    with app.app_context():
        from app.models import User
        student = User.query.filter_by(email="studentiv@test.com").first()
        assert student.face_descriptor is None


def test_confirm_match_rejects_malformed_descriptor(client, app, student_ready):
    doc_id = _upload_id(client).get_json()["doc_id"]
    r = client.post(
        "/api/proctor/id-document/confirm-match",
        data=json.dumps({
            "doc_id": doc_id, "id_descriptor": [0.1, 0.2], "live_descriptor": _descriptor(0.1),
            "liveness_passed": True, "blink_count": 1,
        }),
        content_type="application/json",
    )
    assert r.status_code == 400


def test_confirm_match_rejects_other_students_document(client, app, student_ready):
    doc_id = _upload_id(client).get_json()["doc_id"]
    client.get("/logout")

    register_and_verify(client, app, "Other", "otheriv@test.com", "9000000072", "student", "Studpass1!")
    login(client, "otheriv@test.com", "Studpass1!")
    desc = _descriptor(0.1)
    r = client.post(
        "/api/proctor/id-document/confirm-match",
        data=json.dumps({
            "doc_id": doc_id, "id_descriptor": desc, "live_descriptor": desc,
            "liveness_passed": True, "blink_count": 1,
        }),
        content_type="application/json",
    )
    assert r.status_code == 404


# ---------- serving the ID image ----------

def test_id_document_image_visible_to_owner_and_admin_not_others(client, app, student_ready):
    doc_id = _upload_id(client).get_json()["doc_id"]

    r_owner = client.get(f"/api/proctor/id-document/image/{doc_id}")
    assert r_owner.status_code == 200

    client.get("/logout")
    register_and_verify(client, app, "Other2", "otheriv2@test.com", "9000000073", "student", "Studpass1!")
    login(client, "otheriv2@test.com", "Studpass1!")
    r_other = client.get(f"/api/proctor/id-document/image/{doc_id}")
    assert r_other.status_code == 403

    client.get("/logout")
    login(client, "adminiv@test.com", "Adminpass1!")
    r_admin = client.get(f"/api/proctor/id-document/image/{doc_id}")
    assert r_admin.status_code == 200


# ---------- admin review queue ----------

def test_admin_can_approve_and_reject_id_documents(client, app, student_ready):
    doc_id = _upload_id(client).get_json()["doc_id"]
    desc = _descriptor(0.1)
    client.post(
        "/api/proctor/id-document/confirm-match",
        data=json.dumps({
            "doc_id": doc_id, "id_descriptor": desc, "live_descriptor": desc,
            "liveness_passed": True, "blink_count": 1,
        }),
        content_type="application/json",
    )
    client.get("/logout")

    login(client, "adminiv@test.com", "Adminpass1!")
    r = client.get("/admin/id-verification")
    assert r.status_code == 200
    assert b"Jordan A Rivera" in r.data or b"Rivera" in r.data

    r = client.post(f"/admin/id-verification/{doc_id}/review", data={"decision": "rejected", "notes": "blurry photo"})
    assert r.status_code == 302

    with app.app_context():
        from app.models import IdentityDocument, User
        doc = IdentityDocument.query.get(doc_id)
        assert doc.review_status == "rejected"
        assert doc.review_notes == "blurry photo"
        # Rejecting the document revokes the enrollment it fed.
        student = User.query.filter_by(email="studentiv@test.com").first()
        assert student.face_descriptor is None


def test_admin_review_requires_admin_role(client, app, student_ready):
    doc_id = _upload_id(client).get_json()["doc_id"]
    r = client.post(f"/admin/id-verification/{doc_id}/review", data={"decision": "approved"})
    assert r.status_code == 403


# ---------- spot-check / liveness event types ----------

@pytest.fixture()
def attempt_with_events(client, app, student_ready):
    client.get("/logout")  # student_ready leaves the client logged in as the student
    login(client, "adminiv@test.com", "Adminpass1!")
    client.post(
        "/admin/tests/create",
        data=dict(test_code="IVX1", title="ID Verify Test", description="d", duration_minutes=10,
                   total_questions=1, passing_marks=1, status="published", max_attempts=1,
                   negative_marks_per_wrong=0),
    )
    with app.app_context():
        from app.models import Test, User
        test = Test.query.filter_by(test_code="IVX1").first()
        student = User.query.filter_by(email="studentiv@test.com").first()
    add_single_question(client, test.id, "Q1", "1", "2", "3", "4", "a", marks=1)
    client.post(f"/admin/tests/{test.id}/assign", data={"student_ids": [str(student.id)]})
    client.get("/logout")

    login(client, "studentiv@test.com", "Studpass1!")
    doc_id = _upload_id(client).get_json()["doc_id"]
    desc = _descriptor(0.1)
    client.post(
        "/api/proctor/id-document/confirm-match",
        data=json.dumps({
            "doc_id": doc_id, "id_descriptor": desc, "live_descriptor": desc,
            "liveness_passed": True, "blink_count": 1,
        }),
        content_type="application/json",
    )

    client.get(f"/student/tests/{test.id}/start")
    with app.app_context():
        from app.models import Attempt, User
        student = User.query.filter_by(email="studentiv@test.com").first()
        attempt = Attempt.query.filter_by(test_id=test.id, student_id=student.id).first()
    return attempt.id


def test_spotcheck_and_liveness_event_types_accepted(client, app, attempt_with_events):
    attempt_id = attempt_with_events
    for event_type, severity in [
        ("identity_spotcheck_passed", "warning"),
        ("identity_spotcheck_failed", "violation"),
        ("liveness_check_failed", "violation"),
    ]:
        r = client.post(
            "/api/proctor/event",
            data=json.dumps({"attempt_id": attempt_id, "event_type": event_type, "severity": severity}),
            content_type="application/json",
        )
        assert r.status_code == 200
        assert r.get_json()["ok"] is True

    with app.app_context():
        from app.models import ProctoringEvent
        types = {e.event_type for e in ProctoringEvent.query.filter_by(attempt_id=attempt_id).all()}
        assert {"identity_spotcheck_passed", "identity_spotcheck_failed", "liveness_check_failed"} <= types


def test_spotcheck_passed_does_not_count_as_violation(client, app, attempt_with_events):
    attempt_id = attempt_with_events
    r = client.post(
        "/api/proctor/event",
        data=json.dumps({"attempt_id": attempt_id, "event_type": "identity_spotcheck_passed", "severity": "warning"}),
        content_type="application/json",
    )
    assert r.get_json()["violation_count"] == 0


def test_spotcheck_failed_counts_as_violation_with_high_weight(client, app, attempt_with_events):
    attempt_id = attempt_with_events
    r = client.post(
        "/api/proctor/event",
        data=json.dumps({"attempt_id": attempt_id, "event_type": "identity_spotcheck_failed", "severity": "violation"}),
        content_type="application/json",
    )
    data = r.get_json()
    assert data["violation_count"] == 1

    with app.app_context():
        from app.models import Attempt
        attempt = Attempt.query.get(attempt_id)
        # identity_spotcheck_failed is weighted like identity_mismatch (20) — a
        # single one should already push suspicion into a meaningfully nonzero score.
        assert attempt.suspicion_score >= 20
