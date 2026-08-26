import json

import pytest

from tests.conftest import register_and_verify, login


@pytest.fixture()
def attempt_setup(client, app):
    register_and_verify(client, app, "Admin", "admin2@test.com", "9000000020", "admin", "adminpass")
    register_and_verify(client, app, "Student", "student2@test.com", "9000000021", "student", "studpass")

    login(client, "admin2@test.com", "adminpass")
    client.post(
        "/admin/tests/create",
        data=dict(test_code="P1", title="Proctor Test", description="d", duration_minutes=20,
                   total_questions=1, passing_marks=1, status="published", max_attempts=1,
                   negative_marks_per_wrong=0),
    )
    with app.app_context():
        from app.models import Test, User
        test = Test.query.filter_by(test_code="P1").first()
        student = User.query.filter_by(email="student2@test.com").first()
    client.post(
        f"/admin/tests/{test.id}/questions/add",
        data=dict(question_text="Q", option_a="1", option_b="2", option_c="3", option_d="4",
                   correct_answer="a", marks=1),
    )
    client.post(f"/admin/tests/{test.id}/assign", data={"student_ids": [str(student.id)]})
    client.get("/logout")

    login(client, "student2@test.com", "studpass")
    descriptor = [0.01 * i for i in range(128)]
    client.post("/api/proctor/enroll-face", data=json.dumps({"descriptor": descriptor}),
                content_type="application/json")
    client.get(f"/student/tests/{test.id}/start")

    with app.app_context():
        from app.models import Attempt
        attempt = Attempt.query.filter_by(test_id=test.id, student_id=student.id).first()

    return {"test_id": test.id, "attempt_id": attempt.id, "admin_email": "admin2@test.com"}


def test_invalid_event_type_rejected(client, attempt_setup):
    r = client.post(
        "/api/proctor/event",
        data=json.dumps({"attempt_id": attempt_setup["attempt_id"], "event_type": "bogus", "severity": "violation"}),
        content_type="application/json",
    )
    assert r.status_code == 400


def test_violation_events_increment_and_terminate(client, app, attempt_setup):
    attempt_id = attempt_setup["attempt_id"]
    last = None
    for _ in range(5):
        r = client.post(
            "/api/proctor/event",
            data=json.dumps({"attempt_id": attempt_id, "event_type": "tab_hidden", "severity": "violation"}),
            content_type="application/json",
        )
        last = r.get_json()

    assert last["terminated"] is True
    assert last["violation_count"] == 5

    with app.app_context():
        from app.models import Attempt
        attempt = Attempt.query.get(attempt_id)
        assert attempt.status == "terminated"


def test_warning_severity_does_not_increment_violation_count(client, attempt_setup):
    r = client.post(
        "/api/proctor/event",
        data=json.dumps({
            "attempt_id": attempt_setup["attempt_id"], "event_type": "audio_violation", "severity": "warning",
        }),
        content_type="application/json",
    )
    data = r.get_json()
    assert data["violation_count"] == 0


def test_identity_mismatch_event_recorded(client, attempt_setup):
    r = client.post(
        "/api/proctor/event",
        data=json.dumps({
            "attempt_id": attempt_setup["attempt_id"], "event_type": "identity_mismatch",
            "severity": "violation", "details": "distance=0.9",
        }),
        content_type="application/json",
    )
    data = r.get_json()
    assert data["ok"] is True
    assert data["violation_count"] == 1


def test_enroll_face_rejects_bad_descriptor(client, attempt_setup):
    r = client.post(
        "/api/proctor/enroll-face",
        data=json.dumps({"descriptor": [1, 2, 3]}),
        content_type="application/json",
    )
    assert r.status_code == 400


def test_snapshot_check_flags_no_face_and_stores_image(client, app, attempt_setup):
    import cv2
    import numpy as np
    import base64

    img = np.zeros((240, 320, 3), dtype=np.uint8)
    ok, buf = cv2.imencode(".jpg", img)
    b64 = "data:image/jpeg;base64," + base64.b64encode(buf.tobytes()).decode()

    r = client.post(
        "/api/proctor/snapshot",
        data=json.dumps({"attempt_id": attempt_setup["attempt_id"], "image": b64}),
        content_type="application/json",
    )
    data = r.get_json()
    assert data["faces_detected"] == 0

    with app.app_context():
        from app.models import Snapshot
        snaps = Snapshot.query.filter_by(attempt_id=attempt_setup["attempt_id"]).all()
        assert len(snaps) == 1
        assert snaps[0].faces_detected == 0


def test_recording_chunk_upload_and_admin_playback(client, app, attempt_setup):
    import io

    fake_video = io.BytesIO(b"FAKEWEBMDATA")
    r = client.post(
        "/api/proctor/recording/chunk",
        data={"attempt_id": str(attempt_setup["attempt_id"]), "chunk_index": "0",
              "chunk": (fake_video, "chunk_0.webm")},
        content_type="multipart/form-data",
    )
    assert r.status_code == 200
    recording_id = r.get_json()["recording_id"]

    client.get("/logout")
    login(client, attempt_setup["admin_email"], "adminpass")
    r = client.get(f"/api/proctor/recordings/{recording_id}/file")
    assert r.status_code == 200
    assert r.data == b"FAKEWEBMDATA"


def test_student_cannot_access_other_students_recording(client, app, attempt_setup):
    client.get("/logout")
    register_and_verify(client, app, "Other", "other2@test.com", "9000000022", "student", "studpass")
    login(client, "other2@test.com", "studpass")

    r = client.get("/api/proctor/recordings/1/file")
    assert r.status_code in (403, 404)
