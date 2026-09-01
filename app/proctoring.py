import base64
import json
import os
import re
from datetime import datetime

import cv2
import numpy as np
from flask import Blueprint, request, jsonify, current_app, send_file, abort, url_for
from flask_login import current_user
from werkzeug.utils import secure_filename

from app import db
from app.models import Attempt, ProctoringEvent, Recording, Snapshot, IdentityDocument, Test
from app.utils import student_required, admin_required
from app.email_utils import send_email
from app.notifications import maybe_send_high_risk_alert

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
    "phone_detected", "book_detected", "extra_person_detected", "looking_away",
    # Advanced object detection — an additional laptop/monitor-like screen in
    # frame, and a catch-all for other COCO-SSD classes worth flagging (e.g.
    # a remote, tablet-shaped object, or a second visible screen) that don't
    # have their own dedicated event type. See proctor.js's object-detection
    # section for the full class list and why earphones/earbuds aren't
    # detectable this way (no standard model reliably classifies them).
    "laptop_detected", "unauthorized_object_detected",
    # Exam Session Device Management (see app.exam_sessions) — server-only,
    # never submitted via the client-facing /event endpoint below (hence
    # not needed in VALID_EVENT_TYPES), listed here for documentation.
    # session_resumed | concurrent_session_blocked
    # AI gaze tracking & advanced head-pose (see proctor.js's gaze/head-pose
    # monitors) — distinct from the existing "looking_away" (a coarse,
    # sustained one-direction head-yaw proxy): "gaze_away" is an eye/iris-
    # position estimate (dark-pupil centroid within the eye contour, not
    # just where the head is pointed), and "repeated_head_movement" flags a
    # *pattern* of several separate look-away excursions in a short window
    # (left/right/up/down), not just one sustained turn.
    "gaze_away", "repeated_head_movement",
    # Network & exam recovery — a dropped/restored connection, logged for
    # audit context (see proctor.js's connection manager). Brief drops are
    # reported as "warning" (recorded but never scored/terminating); a
    # sustained outage is reported as "violation" by the client since a
    # long unexplained disconnect can itself be a proctoring-relevant signal.
    "connection_lost", "connection_restored",
    # Enhanced identity verification — random in-exam spot checks (an active,
    # visible re-verification distinct from the silent periodic background
    # check that already reports via "identity_mismatch") and liveness
    # challenge failures (e.g. a static photo held up to the camera never
    # produces a blink). See proctor.js's spot-check scheduler.
    "identity_spotcheck_passed", "identity_spotcheck_failed", "liveness_check_failed",
}


# Suspicion score: a deterministic, explainable weighted formula over the
# violation log — NOT a trained ML risk model. Each event type carries a
# hand-set weight reflecting how strong a signal it typically is (e.g. a
# mismatched identity is a much stronger signal than one blurred browser
# window), summed and capped, then boosted for patterns — a burst, an
# escalating trend, several different kinds of violations — that suggest
# deliberate behavior over incidental noise. Termination itself floors the
# score high, since an attempt only terminates after crossing the violation
# threshold anyway. Weights are a starting point based on judgment about
# what each signal typically means, not fitted to real outcome data —
# worth tuning once there's a track record to check them against.
EVENT_WEIGHTS = {
    "identity_mismatch": 20,
    "identity_spotcheck_failed": 20,
    "concurrent_session_blocked": 16,
    "extra_person_detected": 15,
    "phone_detected": 15,
    "liveness_check_failed": 14,
    "multiple_faces": 12,
    "repeated_head_movement": 11,
    "laptop_detected": 11,
    "book_detected": 10,
    "dev_tools": 10,
    "unauthorized_object_detected": 9,
    "looking_away": 8,
    "gaze_away": 8,
    "copy_paste_attempt": 8,
    "no_face": 6,
    "tab_hidden": 6,
    "connection_lost": 6,
    "audio_violation": 5,
    "fullscreen_exit": 5,
    "window_blur": 4,
}
DEFAULT_EVENT_WEIGHT = 5

# Human-readable label for each violation type, used by explain_reasons()
# below to build the "why was this flagged" explanation — not just a bare
# score. Falls back to a prettified version of the raw event_type for
# anything not listed here (e.g. a future event type added without
# updating this dict).
EVENT_TYPE_LABELS = {
    "identity_mismatch": "identity mismatch (camera face didn't match the enrolled reference)",
    "identity_spotcheck_failed": "failed a random identity spot check",
    "concurrent_session_blocked": "a second device/tab tried to open this exam while another was already active",
    "session_resumed": "the exam session was resumed on this device after a disconnect or refresh",
    "extra_person_detected": "an extra person was detected in the camera frame",
    "phone_detected": "a phone was detected in the camera frame",
    "liveness_check_failed": "a liveness check failed (no blink detected)",
    "multiple_faces": "multiple faces were detected in the camera frame",
    "repeated_head_movement": "a repeated pattern of head movement (glancing left/right/up/down)",
    "laptop_detected": "an additional laptop or screen was detected in the camera frame",
    "unauthorized_object_detected": "an unauthorized object was detected in the camera frame",
    "book_detected": "a book, notes, or paper were detected in the camera frame",
    "dev_tools": "browser developer tools were opened",
    "looking_away": "a sustained head turn away from the screen",
    "gaze_away": "the eyes drifted away from the screen for a sustained period",
    "copy_paste_attempt": "a copy/paste attempt",
    "no_face": "no face was visible to the camera",
    "tab_hidden": "the exam tab was switched away from",
    "connection_lost": "the connection was lost",
    "audio_violation": "unusual audio or talking was detected",
    "fullscreen_exit": "fullscreen mode was exited",
    "window_blur": "the browser window lost focus",
}

# Low/Medium/High/Critical thresholds against the 0-100 score, checked
# highest-first.
RISK_LEVEL_THRESHOLDS = [(75, "critical"), (50, "high"), (25, "medium"), (0, "low")]


def _risk_level_for_score(score):
    for threshold, level in RISK_LEVEL_THRESHOLDS:
        if score >= threshold:
            return level
    return "low"


# Event types serious enough to interrupt a proctor with a live, in-browser
# push the moment they happen (see admin.proctor_alerts_stream) — a smaller,
# stricter set than "counts toward the suspicion score at all". Anything not
# in this set still lands in the proctoring log and suspicion score as
# usual, it just doesn't generate a live push — otherwise a chatty attempt
# (repeated tab switches, window blur) would bury the genuinely urgent
# signals under noise for whoever is watching the queue live.
HIGH_SEVERITY_ALERT_EVENT_TYPES = {
    "identity_mismatch", "identity_spotcheck_failed", "extra_person_detected",
    "phone_detected", "liveness_check_failed", "multiple_faces", "laptop_detected",
}


def get_live_alerts_since(last_id, org_id):
    """Every high-severity violation (see HIGH_SEVERITY_ALERT_EVENT_TYPES)
    logged after `last_id`, on an attempt that's still in_progress, scoped
    to `org_id` — the query behind the live proctor-alerts SSE stream. Only
    in_progress attempts are considered since there's no one left to alert
    a proctor to intervene with once an attempt has ended."""
    return (
        ProctoringEvent.query
        .join(Attempt, ProctoringEvent.attempt_id == Attempt.id)
        .join(Test, Attempt.test_id == Test.id)
        .filter(
            ProctoringEvent.id > last_id,
            ProctoringEvent.event_type.in_(HIGH_SEVERITY_ALERT_EVENT_TYPES),
            ProctoringEvent.severity == "violation",
            Attempt.status == "in_progress",
            Test.org_id == org_id,
        )
        .order_by(ProctoringEvent.id)
        .all()
    )


def _explain_reasons(violations):
    """Turn the raw violation log into an ordered list of plain-language
    reasons — the "why was this attempt flagged" explanation, not just a
    bare score. One line per distinct event type that occurred, most
    heavily-weighted (most serious) first, with a count so "3 tab
    switches" reads differently from "1". This is the per-signal half of
    the explanation; compute_suspicion_score's existing "signals" list
    (burst/escalating/diversity/rate) is the pattern-level half — the UI
    shows both together."""
    counts = {}
    for e in violations:
        counts[e.event_type] = counts.get(e.event_type, 0) + 1

    ordered_types = sorted(counts.keys(), key=lambda t: EVENT_WEIGHTS.get(t, DEFAULT_EVENT_WEIGHT), reverse=True)
    reasons = []
    for event_type in ordered_types:
        count = counts[event_type]
        label = EVENT_TYPE_LABELS.get(event_type, event_type.replace("_", " "))
        times = "time" if count == 1 else "times"
        reasons.append(f"{label.capitalize()} — flagged {count} {times}")
    return reasons


def compute_suspicion_score(attempt):
    """Combine every violation on this attempt into one 0-100 suspicion
    score and a Low/Medium/High/Critical level — see EVENT_WEIGHTS above
    for the scoring formula. Also returns the same plain-language pattern
    signals as before (burst/escalation/diversity) so the UI can explain
    *why* the score landed where it did, not just show a bare number.
    Always returns a dict even with zero violations (score 0, level low)."""
    events = ProctoringEvent.query.filter_by(attempt_id=attempt.id).order_by(ProctoringEvent.created_at).all()
    violations = [e for e in events if e.severity == "violation"]

    result = {
        "score": 0, "level": "low", "signals": [], "reasons": [], "distinct_types": 0,
        "violations_per_min": 0.0, "burst": False, "escalating": False,
    }
    if not violations:
        return result

    distinct_types = len({e.event_type for e in violations})
    end_time = attempt.submitted_at or datetime.utcnow()
    duration_minutes = max((end_time - attempt.started_at).total_seconds() / 60, 1)
    rate = round(len(violations) / duration_minutes, 2)

    # Burst: 3+ violations landing within any single 60-second window.
    times = [e.created_at for e in violations]
    burst = any(
        sum(1 for t in times[i:] if (t - times[i]).total_seconds() <= 60) >= 3
        for i in range(len(times))
    )

    # Escalating: meaningfully more violations in the second half of the
    # attempt than the first — e.g. a student who settles into cheating
    # once they realize the material is hard, rather than a one-off blip.
    midpoint = attempt.started_at + (end_time - attempt.started_at) / 2
    first_half = len([e for e in violations if e.created_at <= midpoint])
    second_half = len(violations) - first_half
    escalating = second_half >= 3 and second_half > first_half * 1.5

    base_score = min(sum(EVENT_WEIGHTS.get(e.event_type, DEFAULT_EVENT_WEIGHT) for e in violations), 70)
    bonus = 0
    if burst:
        bonus += 10
    if escalating:
        bonus += 10
    if distinct_types >= 3:
        bonus += 10
    if distinct_types >= 5:
        bonus += 5

    score = min(base_score + bonus, 100)
    if attempt.status == "terminated":
        score = max(score, 85)
    score = int(round(score))

    signals = []
    if distinct_types >= 3:
        signals.append(f"{distinct_types} different kinds of violations were flagged")
    if burst:
        signals.append("3 or more violations landed within a single minute")
    if escalating:
        signals.append("violations increased in the second half of the attempt")
    if rate >= 2:
        signals.append(f"averaged {rate} violations per minute")
    if attempt.status == "terminated":
        signals.append("attempt was auto-terminated for repeated violations")

    result.update({
        "score": score, "level": _risk_level_for_score(score), "signals": signals,
        "reasons": _explain_reasons(violations),
        "distinct_types": distinct_types, "violations_per_min": rate,
        "burst": burst, "escalating": escalating,
    })
    return result


def build_timeline(attempt):
    """Merge this attempt's proctoring events, flagged snapshots, and
    recording-chunk uploads into one chronological "Behavior Timeline" —
    every entry carries offset_seconds (seconds since attempt.started_at)
    so the UI can render one combined feed and jump a video to the right
    moment when an entry is clicked.

    Recordings are uploaded in short MediaRecorder chunks as they complete
    (see the "video/audio recording" section below), so a chunk's
    Recording.created_at is a good proxy for when that segment of footage
    *ends* — we don't separately store each chunk's actual duration. From
    that, each recording is given an approximate [start, end) playback
    window: it starts where the previous chunk (or the attempt itself)
    left off, and ends at its own created_at. That's what jump-to-timestamp
    uses to pick the right <video> element and seek within it — it's an
    approximation based on upload timing, not a frame-accurate cut, but
    it's within a few seconds in practice since chunks upload promptly."""
    base = attempt.started_at or datetime.utcnow()

    def offset(ts):
        return max((ts - base).total_seconds(), 0)

    items = []
    for e in attempt.events:
        items.append({
            "type": "event",
            "id": e.id,
            "time": e.created_at,
            "offset": offset(e.created_at),
            "severity": e.severity,
            "label": EVENT_TYPE_LABELS.get(e.event_type, e.event_type.replace("_", " ")).capitalize(),
            "detail": e.details,
            "anchor": f"event-{e.id}",
        })
    for s in attempt.snapshots:
        items.append({
            "type": "snapshot",
            "id": s.id,
            "time": s.created_at,
            "offset": offset(s.created_at),
            "severity": "warning",
            "label": f"Snapshot captured ({s.faces_detected} face(s) detected)",
            "detail": None,
            "anchor": f"snap-{s.id}",
        })

    recordings = sorted(attempt.recordings, key=lambda r: r.chunk_index)
    segments = []
    prev_end = 0.0
    for rec in recordings:
        end = offset(rec.created_at)
        end = max(end, prev_end)  # chunks should upload in order, but never let a clock skew invert a segment
        segments.append({
            "recording_id": rec.id, "chunk_index": rec.chunk_index,
            "start": prev_end, "end": end,
            "url": url_for("proctoring.serve_recording", recording_id=rec.id),
            "anchor": f"rec-{rec.id}",
        })
        items.append({
            "type": "recording",
            "id": rec.id,
            "time": rec.created_at,
            "offset": end,
            "severity": None,
            "label": f"Recording segment {rec.chunk_index + 1} saved",
            "detail": f"{(rec.file_size / 1024):.1f} KB",
            "anchor": f"rec-{rec.id}",
        })
        prev_end = end

    items.sort(key=lambda it: it["offset"])
    return {"entries": items, "segments": segments}


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


def _record_violation(attempt, event_type, severity, details="", confidence=None):
    event = ProctoringEvent(
        attempt_id=attempt.id, event_type=event_type, severity=severity,
        details=details, confidence=confidence,
    )
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

    db.session.flush()  # so compute_suspicion_score sees this event and the (possible) new status
    risk = compute_suspicion_score(attempt)
    attempt.suspicion_score = risk["score"]
    attempt.risk_level = risk["level"]
    maybe_send_high_risk_alert(attempt, risk)

    db.session.commit()
    if terminated:
        _notify_termination(attempt)
    return terminated


# Public name for the same function — used by app.exam_sessions (and
# anywhere else outside this module) so a device/session violation goes
# through the exact same violation-count/suspicion-score/termination/
# high-risk-alert bookkeeping as every other event type, instead of a
# module poking ProctoringEvent rows in directly and quietly skipping all
# of that.
record_violation = _record_violation


@bp.route("/event", methods=["POST"])
@student_required
def log_event():
    data = request.get_json(silent=True) or {}
    attempt_id = data.get("attempt_id")
    event_type = data.get("event_type")
    severity = data.get("severity", "violation")
    details = str(data.get("details", ""))[:500]
    confidence = data.get("confidence")
    if confidence is not None:
        try:
            confidence = max(0.0, min(float(confidence), 1.0))
        except (TypeError, ValueError):
            confidence = None

    if event_type not in VALID_EVENT_TYPES:
        return jsonify({"ok": False, "error": "invalid event_type"}), 400

    attempt = _get_owned_attempt(attempt_id)
    if not attempt:
        return jsonify({"ok": False, "error": "attempt not found"}), 404

    if attempt.status != "in_progress":
        return jsonify({"ok": True, "terminated": attempt.status == "terminated", "violation_count": attempt.violation_count})

    terminated = _record_violation(attempt, event_type, severity, details, confidence=confidence)
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
# Enhanced identity verification: government/college ID upload with OCR
# extraction, then an ID-photo-to-live-face match (with a liveness check)
# that's what actually completes enrollment above. See IdentityDocument's
# docstring in app.models for how the two tracks (automatic match vs.
# admin document review) relate.
# ---------------------------------------------------------------------------

def _run_ocr(filepath):
    """Best-effort OCR over the uploaded ID image via Tesseract, plus a
    handful of regex heuristics to pull out a name/ID-number/DOB-shaped
    line. This is intentionally naive — real ID layouts vary hugely by
    issuer — so it's presented to the student and admin as a starting
    point to double-check, never as a verified fact by itself. Any OCR
    failure (missing binary, unreadable image) degrades to "no text
    extracted" rather than blocking the upload."""
    raw_text = ""
    try:
        import pytesseract
        from PIL import Image

        with Image.open(filepath) as img:
            raw_text = pytesseract.image_to_string(img) or ""
    except Exception:  # pragma: no cover - tesseract missing/unreadable image
        raw_text = ""

    fields = {"name": None, "id_number": None, "dob": None}
    lines = [ln.strip() for ln in raw_text.splitlines() if ln.strip()]

    id_number_pattern = re.compile(r"\b([A-Z0-9]{2,4}[- ]?\d{4,10}[A-Z0-9]{0,4})\b")
    dob_pattern = re.compile(
        r"\b(\d{1,2}[/.\-]\d{1,2}[/.\-]\d{2,4}|\d{4}[/.\-]\d{1,2}[/.\-]\d{1,2})\b"
    )
    name_label = re.compile(r"^(name|full name|student name)\s*[:\-]\s*(.+)$", re.IGNORECASE)
    id_label = re.compile(
        r"^(id\s*no\.?|id number|roll no\.?|enrol{1,2}ment no\.?|license no\.?|document no\.?)\s*[:\-]\s*(.+)$",
        re.IGNORECASE,
    )
    dob_label = re.compile(r"^(dob|date of birth)\s*[:\-]\s*(.+)$", re.IGNORECASE)

    for line in lines:
        if not fields["name"]:
            m = name_label.match(line)
            if m:
                fields["name"] = m.group(2).strip()[:200]
        if not fields["id_number"]:
            m = id_label.match(line)
            if m:
                fields["id_number"] = m.group(2).strip()[:100]
        if not fields["dob"]:
            m = dob_label.match(line)
            if m:
                fields["dob"] = m.group(2).strip()[:30]

    # Fall back to pattern-matching anywhere in the text if no labeled line
    # was found — many IDs print values without an explicit "Name:" prefix.
    if not fields["id_number"]:
        m = id_number_pattern.search(raw_text)
        if m:
            fields["id_number"] = m.group(1)[:100]
    if not fields["dob"]:
        m = dob_pattern.search(raw_text)
        if m:
            fields["dob"] = m.group(1)[:30]
    if not fields["name"] and lines:
        # Heuristic: the first all-letters line of reasonable length is
        # often the printed name on a college/government ID card.
        for line in lines[:6]:
            letters_only = re.sub(r"[^A-Za-z ]", "", line).strip()
            if len(letters_only) >= 4 and len(letters_only) == len(line.replace(",", "")):
                fields["name"] = line[:200]
                break

    return raw_text[:8000], fields


@bp.route("/id-document/upload", methods=["POST"])
@student_required
def upload_id_document():
    file_storage = request.files.get("id_image")
    doc_type = request.form.get("doc_type", "government_id")
    if doc_type not in ("government_id", "college_id"):
        doc_type = "government_id"

    if not file_storage or not file_storage.filename:
        return jsonify({"ok": False, "error": "no file uploaded"}), 400

    ext = file_storage.filename.rsplit(".", 1)[-1].lower() if "." in file_storage.filename else ""
    if ext not in current_app.config["ID_DOCUMENT_ALLOWED_EXTS"]:
        return jsonify({"ok": False, "error": "unsupported file type — use a JPG, PNG, or WEBP photo"}), 400

    upload_dir = os.path.join(current_app.config["ID_DOCUMENT_UPLOAD_DIR"], str(current_user.id))
    os.makedirs(upload_dir, exist_ok=True)

    existing = IdentityDocument.query.filter_by(user_id=current_user.id).first()
    if existing:
        old_path = os.path.join(upload_dir, existing.filename)
        if os.path.exists(old_path):
            try:
                os.remove(old_path)
            except OSError:  # pragma: no cover - best-effort cleanup
                pass

    filename = secure_filename(f"id_{datetime.utcnow().strftime('%Y%m%d%H%M%S%f')}.{ext}")
    filepath = os.path.join(upload_dir, filename)
    file_storage.save(filepath)

    raw_text, fields = _run_ocr(filepath)

    if existing:
        doc = existing
    else:
        doc = IdentityDocument(user_id=current_user.id)
        db.session.add(doc)

    doc.doc_type = doc_type
    doc.filename = filename
    doc.uploaded_at = datetime.utcnow()
    doc.ocr_raw_text = raw_text
    doc.ocr_name = fields["name"]
    doc.ocr_id_number = fields["id_number"]
    doc.ocr_dob = fields["dob"]
    # A fresh upload invalidates any prior automatic match/review — the
    # student must re-verify against the new photo before it can enroll them.
    doc.face_match_distance = None
    doc.face_match_passed = None
    doc.liveness_passed = None
    doc.liveness_blink_count = None
    doc.matched_at = None
    doc.review_status = "pending"
    doc.reviewed_by_id = None
    doc.reviewed_at = None
    doc.review_notes = None
    db.session.commit()

    return jsonify({
        "ok": True,
        "doc_id": doc.id,
        "image_url": url_for("proctoring.serve_id_document_image", doc_id=doc.id),
        "ocr": {"name": doc.ocr_name, "id_number": doc.ocr_id_number, "dob": doc.ocr_dob},
    })


@bp.route("/id-document/image/<int:doc_id>")
def serve_id_document_image(doc_id):
    doc = IdentityDocument.query.get_or_404(doc_id)
    is_owner = current_user.is_authenticated and current_user.id == doc.user_id
    is_admin = current_user.is_authenticated and current_user.role == "admin"
    if not is_owner and not is_admin:
        abort(403)

    filepath = os.path.join(current_app.config["ID_DOCUMENT_UPLOAD_DIR"], str(doc.user_id), doc.filename)
    if not os.path.exists(filepath):
        abort(404)
    return send_file(filepath, conditional=True)


def _validate_descriptor(value):
    return isinstance(value, list) and len(value) == 128 and all(isinstance(x, (int, float)) for x in value)


@bp.route("/id-document/confirm-match", methods=["POST"])
@student_required
def confirm_id_match():
    """The browser has, via face-api.js, extracted a descriptor from the
    uploaded ID photo and a fresh live webcam capture, and run a liveness
    challenge (blink detection) on that live capture — see runLivenessCheck
    in proctor.js. The distance between the two descriptors is recomputed
    here server-side (never trusted as a client-reported number) and, on a
    pass, becomes the student's enrolled face descriptor: the same
    User.face_descriptor field start_test already requires before letting
    anyone begin a proctored test."""
    data = request.get_json(silent=True) or {}
    doc_id = data.get("doc_id")
    id_descriptor = data.get("id_descriptor")
    live_descriptor = data.get("live_descriptor")
    liveness_passed = bool(data.get("liveness_passed"))
    blink_count = data.get("blink_count")
    try:
        blink_count = max(0, int(blink_count))
    except (TypeError, ValueError):
        blink_count = 0

    doc = IdentityDocument.query.get(doc_id)
    if not doc or doc.user_id != current_user.id:
        return jsonify({"ok": False, "error": "document not found"}), 404

    if not _validate_descriptor(id_descriptor) or not _validate_descriptor(live_descriptor):
        return jsonify({"ok": False, "error": "expected 128-value descriptor arrays"}), 400

    id_vec = np.array(id_descriptor, dtype=np.float64)
    live_vec = np.array(live_descriptor, dtype=np.float64)
    distance = float(np.linalg.norm(id_vec - live_vec))
    threshold = current_app.config["FACE_MATCH_THRESHOLD"]
    face_match_passed = distance <= threshold

    doc.face_match_distance = round(distance, 4)
    doc.face_match_passed = face_match_passed
    doc.liveness_passed = liveness_passed
    doc.liveness_blink_count = blink_count
    doc.matched_at = datetime.utcnow()

    enrolled = False
    if face_match_passed and liveness_passed:
        current_user.face_descriptor = json.dumps(live_descriptor)
        enrolled = True

    db.session.commit()

    return jsonify({
        "ok": True,
        "distance": doc.face_match_distance,
        "threshold": threshold,
        "face_match_passed": face_match_passed,
        "liveness_passed": liveness_passed,
        "enrolled": enrolled,
    })


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


# ---------------------------------------------------------------------------
# Exam Session Device Management (see app.exam_sessions): release the
# current tab's claim on an attempt when its page unloads, so a plain
# refresh or tab close can be re-claimed immediately instead of waiting
# out the crash-recovery staleness window.
# ---------------------------------------------------------------------------

@bp.route("/session/release", methods=["POST"])
@student_required
def release_session_route():
    """Fired via navigator.sendBeacon on pagehide/beforeunload — a
    fire-and-forget request that may arrive after the tab is already gone,
    so this deliberately tolerates a missing/late/duplicate call rather
    than erroring. sendBeacon can only send a simple body (no custom
    headers), so the payload is JSON with attempt_id/session_token
    embedded rather than the usual attempt_id-in-the-URL pattern."""
    data = request.get_json(silent=True) or {}
    attempt_id = data.get("attempt_id")

    attempt = _get_owned_attempt(attempt_id)
    if not attempt or attempt.status != "in_progress":
        return jsonify({"ok": True})

    from app.exam_sessions import release_session

    release_session(attempt, data.get("session_token"))
    db.session.commit()
    return jsonify({"ok": True})
