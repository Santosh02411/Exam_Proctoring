import base64
import json
import os
from datetime import datetime

import cv2
import numpy as np
from flask import Blueprint, request, jsonify, current_app, send_file, abort, url_for
from flask_login import current_user

from app import db
from app.models import Attempt, ProctoringEvent, Recording, Snapshot
from app.utils import student_required, admin_required
from app.email_utils import send_email

bp = Blueprint("proctoring", __name__, url_prefix="/api/proctor")

# Haar cascade shipped with opencv-python-headless — used for a lightweight,
# dependency-free server-side second opinion on top of the browser's
# face-api.js detector.
_CASCADE_PATH = cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
_face_cascade = cv2.CascadeClassifier(_CASCADE_PATH)

VALID_EVENT_TYPES = {
    "no_face", "multiple_faces", "tab_hidden", "fullscreen_exit",
    "copy_paste_attempt", "dev_tools", "window_blur",
    "identity_mismatch", "audio_violation",
}


def _get_owned_attempt(attempt_id):
    attempt = Attempt.query.get(attempt_id)
    if not attempt or attempt.student_id != current_user.id:
        return None
    return attempt


def _notify_termination(attempt):
    """Email both the student and the test's owning admin the moment an
    attempt is auto-terminated for proctoring violations — previously the
    only automated notification in the app was the assignment email, so a
    terminated student had no way to find out short of trying to log back
    in, and the admin had no prompt to go review it."""
    test = attempt.test
    student = attempt.student
    admin = test.creator

    send_email(
        student.email,
        f"Your attempt on '{test.title}' was terminated",
        f"Hi {student.name},\n\nYour attempt on '{test.title}' was automatically ended during "
        f"the exam after {attempt.violation_count} proctoring violation(s) were flagged.\n\n"
        f"Reason: {attempt.termination_reason}\n\n"
        f"If you believe this was a mistake, contact your test administrator.",
    )

    if admin and admin.email:
        send_email(
            admin.email,
            f"Attempt terminated — {student.name} on '{test.title}'",
            f"{student.name} ({student.email})'s attempt on '{test.title}' was automatically "
            f"terminated for proctoring violations.\n\n"
            f"Violations flagged: {attempt.violation_count}\nReason: {attempt.termination_reason}\n\n"
            f"Review the events, snapshots, and recording here:\n"
            f"{url_for('admin.view_attempt', attempt_id=attempt.id, _external=True)}",
        )


def _record_violation(attempt, event_type, severity, details=""):
    event = ProctoringEvent(attempt_id=attempt.id, event_type=event_type, severity=severity, details=details)
    db.session.add(event)

    terminated = False
    if severity == "violation":
        attempt.violation_count += 1
        max_v = current_app.config["MAX_VIOLATIONS_BEFORE_TERMINATION"]
        if attempt.violation_count >= max_v and attempt.status == "in_progress":
            attempt.status = "terminated"
            attempt.termination_reason = f"Exceeded {max_v} proctoring violations."
            attempt.submitted_at = datetime.utcnow()
            terminated = True

    db.session.commit()
    if terminated:
        _notify_termination(attempt)
    return terminated


@bp.route("/event", methods=["POST"])
@student_required
def log_event():
    data = request.get_json(silent=True) or {}
    attempt_id = data.get("attempt_id")
    event_type = data.get("event_type")
    severity = data.get("severity", "violation")
    details = str(data.get("details", ""))[:500]

    if event_type not in VALID_EVENT_TYPES:
        return jsonify({"ok": False, "error": "invalid event_type"}), 400

    attempt = _get_owned_attempt(attempt_id)
    if not attempt:
        return jsonify({"ok": False, "error": "attempt not found"}), 404

    if attempt.status != "in_progress":
        return jsonify({"ok": True, "terminated": attempt.status == "terminated", "violation_count": attempt.violation_count})

    terminated = _record_violation(attempt, event_type, severity, details)
    return jsonify({"ok": True, "terminated": terminated, "violation_count": attempt.violation_count})


@bp.route("/snapshot", methods=["POST"])
@student_required
def check_snapshot():
    """Server-side second opinion: decode a base64 JPEG frame sent periodically
    from the browser and run a Haar-cascade face count with OpenCV, independent
    of the client-side face-api.js model. Flags mismatches as proctoring events.
    """
    data = request.get_json(silent=True) or {}
    attempt_id = data.get("attempt_id")
    image_b64 = data.get("image", "")

    attempt = _get_owned_attempt(attempt_id)
    if not attempt:
        return jsonify({"ok": False, "error": "attempt not found"}), 404

    if attempt.status != "in_progress":
        return jsonify({"ok": True, "faces_detected": None})

    try:
        if "," in image_b64:
            image_b64 = image_b64.split(",", 1)[1]
        img_bytes = base64.b64decode(image_b64)
        arr = np.frombuffer(img_bytes, dtype=np.uint8)
        frame = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        if frame is None:
            return jsonify({"ok": False, "error": "could not decode image"}), 400

        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        faces = _face_cascade.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5, minSize=(60, 60))
        count = len(faces)
    except Exception as exc:  # pragma: no cover - defensive
        return jsonify({"ok": False, "error": str(exc)}), 400

    terminated = False
    if count == 0:
        terminated = _record_violation(attempt, "no_face", "warning", "Server check: no face in frame")
        _save_flagged_snapshot(attempt, frame, count)
    elif count > 1:
        terminated = _record_violation(attempt, "multiple_faces", "violation", f"Server check: {count} faces in frame")
        _save_flagged_snapshot(attempt, frame, count)

    return jsonify({"ok": True, "faces_detected": count, "terminated": terminated})


def _save_flagged_snapshot(attempt, frame, faces_detected):
    """Persist the still frame to disk when the server-side check flags it, so
    the admin can visually confirm the automated no-face/multiple-faces verdict
    instead of trusting the count alone."""
    try:
        attempt_dir = os.path.join(current_app.config["SNAPSHOT_UPLOAD_DIR"], str(attempt.id))
        os.makedirs(attempt_dir, exist_ok=True)
        filename = f"snap_{datetime.utcnow().strftime('%Y%m%d%H%M%S%f')}.jpg"
        filepath = os.path.join(attempt_dir, filename)
        cv2.imwrite(filepath, frame)

        snap = Snapshot(attempt_id=attempt.id, filename=filename, faces_detected=faces_detected)
        db.session.add(snap)
        db.session.commit()
    except Exception:  # pragma: no cover - never let snapshot storage break proctoring
        db.session.rollback()


@bp.route("/snapshots/<int:snapshot_id>/file")
@admin_required
def serve_snapshot(snapshot_id):
    snap = Snapshot.query.get_or_404(snapshot_id)
    attempt = snap.attempt
    if attempt.test.created_by != current_user.id:
        abort(403)

    filepath = os.path.join(current_app.config["SNAPSHOT_UPLOAD_DIR"], str(attempt.id), snap.filename)
    if not os.path.exists(filepath):
        abort(404)

    return send_file(filepath, mimetype="image/jpeg", conditional=True)


# ---------------------------------------------------------------------------
# Identity verification: enroll a reference face descriptor (128 floats from
# face-api.js's faceRecognitionNet) on the student's account, so it can be
# compared against the live webcam feed before and during a proctored test.
# ---------------------------------------------------------------------------

@bp.route("/enroll-face", methods=["POST"])
@student_required
def enroll_face():
    data = request.get_json(silent=True) or {}
    descriptor = data.get("descriptor")

    if not isinstance(descriptor, list) or len(descriptor) != 128 or not all(
        isinstance(x, (int, float)) for x in descriptor
    ):
        return jsonify({"ok": False, "error": "expected a 128-value descriptor array"}), 400

    current_user.face_descriptor = json.dumps(descriptor)
    db.session.commit()
    return jsonify({"ok": True})


@bp.route("/clear-face-enrollment", methods=["POST"])
@student_required
def clear_face_enrollment():
    current_user.face_descriptor = None
    db.session.commit()
    return jsonify({"ok": True})


# ---------------------------------------------------------------------------
# Video/audio recording: the browser records the exam session in short webm
# chunks (via MediaRecorder) and uploads each one here as it becomes
# available, so a full recording exists even if the browser tab crashes.
# ---------------------------------------------------------------------------

@bp.route("/recording/chunk", methods=["POST"])
@student_required
def upload_recording_chunk():
    attempt_id = request.form.get("attempt_id", type=int)
    chunk_index = request.form.get("chunk_index", type=int, default=0)
    blob = request.files.get("chunk")

    attempt = _get_owned_attempt(attempt_id)
    if not attempt:
        return jsonify({"ok": False, "error": "attempt not found"}), 404
    if not blob:
        return jsonify({"ok": False, "error": "no file uploaded"}), 400

    attempt_dir = os.path.join(current_app.config["RECORDINGS_DIR"], str(attempt.id))
    os.makedirs(attempt_dir, exist_ok=True)

    filename = f"chunk_{chunk_index:05d}.webm"
    filepath = os.path.join(attempt_dir, filename)
    blob.save(filepath)
    size = os.path.getsize(filepath)

    rec = Recording(
        attempt_id=attempt.id,
        chunk_index=chunk_index,
        filename=filename,
        content_type=blob.content_type or "video/webm",
        file_size=size,
    )
    db.session.add(rec)
    db.session.commit()

    return jsonify({"ok": True, "recording_id": rec.id})


@bp.route("/recordings/<int:recording_id>/file")
@admin_required
def serve_recording(recording_id):
    rec = Recording.query.get_or_404(recording_id)
    attempt = rec.attempt
    if attempt.test.created_by != current_user.id:
        abort(403)

    filepath = os.path.join(current_app.config["RECORDINGS_DIR"], str(attempt.id), rec.filename)
    if not os.path.exists(filepath):
        abort(404)

    return send_file(filepath, mimetype=rec.content_type, conditional=True)
