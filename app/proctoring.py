import base64
import json
import os
import re
from datetime import datetime

import cv2
import numpy as np
from flask import Blueprint, request, jsonify, current_app, send_file, abort, url_for, Response
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
    # Candidate Technical Pre-Check & Exam Environment Verification (see
    # proctor.js's pre-check/environment-scan wizard, run before the exam
    # itself starts) — all informational/setup-stage, never exam-time
    # violations: precheck_completed carries a JSON summary of the
    # webcam/mic/speaker/browser/network checks; environment_check_clear
    # and environment_check_flagged report the pre-exam room scan's
    # outcome (see EVENT_WEIGHTS/severity below — these deliberately never
    # carry violation weight, since they happen before the exam is even
    # underway and shouldn't count against a student the same way an
    # in-exam detection does).
    "precheck_completed", "environment_check_clear", "environment_check_flagged",
    # Proctoring Quality Score (see app.proctoring.compute_quality_score):
    # a periodic, lightweight technical sample (audio level, resolution) —
    # not a violation signal at all, purely evidence-quality bookkeeping.
    "quality_sample",
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

# AI-Based Behavioral Pattern Analysis: which underlying signal source
# each event type comes from. The point of this categorization is to tell
# apart "the same sensor fired several times" (e.g. three tab_hidden
# events — plausibly a flaky window manager, or one indecisive moment)
# from "several *different* sensors fired together" (e.g. a phone
# appearing on camera at the same moment the tab was switched away and
# audio picked up talking — much harder to explain as anything but
# coordinated, deliberate behavior). See _cluster_incidents/
# detect_behavioral_patterns below for where this is actually used.
SIGNAL_CATEGORIES = {
    "no_face": "face", "multiple_faces": "face", "identity_mismatch": "face",
    "identity_spotcheck_failed": "face", "liveness_check_failed": "face",
    "looking_away": "gaze", "gaze_away": "gaze", "repeated_head_movement": "gaze",
    "audio_violation": "audio",
    "tab_hidden": "window", "fullscreen_exit": "window", "window_blur": "window",
    "copy_paste_attempt": "window", "dev_tools": "window",
    "phone_detected": "object", "book_detected": "object", "laptop_detected": "object",
    "unauthorized_object_detected": "object", "extra_person_detected": "object",
    "connection_lost": "network", "concurrent_session_blocked": "network",
}
SIGNAL_CATEGORY_LABELS = {
    "face": "Face/Identity", "gaze": "Gaze/Attention", "audio": "Audio",
    "window": "Tab/Window", "object": "Object Detection", "network": "Network/Session",
}
# How close together (in seconds) two violations need to land to be
# considered part of the same "incident" for pattern-clustering purposes
# — deliberately more generous than the 60s burst window above, since a
# coordinated attempt (glance at a phone, switch tabs, talk to someone)
# plausibly unfolds over slightly longer than a single minute.
INCIDENT_WINDOW_SECONDS = 90

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
    "precheck_completed": "completed the pre-exam technical check",
    "environment_check_clear": "pre-exam room scan found nothing of concern",
    "environment_check_flagged": "pre-exam room scan flagged something",
    "quality_sample": "periodic technical quality sample",
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


def _cluster_incidents(violations):
    """Group an already-time-ordered list of violation events into
    "incidents" — runs of violations where consecutive events land within
    INCIDENT_WINDOW_SECONDS of each other — and describe each one: which
    signal categories it touches, whether it's "coordinated" (2+ distinct
    categories, not just one sensor repeating), and a plain-language
    narrative. Pure function over an already-fetched list (no DB access)
    so compute_suspicion_score can reuse it for scoring without a second
    query, and detect_behavioral_patterns (used by the UI) can reuse it
    for display."""
    if not violations:
        return []

    groups = [[violations[0]]]
    for e in violations[1:]:
        if (e.created_at - groups[-1][-1].created_at).total_seconds() <= INCIDENT_WINDOW_SECONDS:
            groups[-1].append(e)
        else:
            groups.append([e])

    incidents = []
    for group in groups:
        categories = sorted({SIGNAL_CATEGORIES.get(e.event_type, "other") for e in group})
        coordinated = len(categories) >= 2 and len(group) >= 2
        span_seconds = int((group[-1].created_at - group[0].created_at).total_seconds())
        labels = list(dict.fromkeys(
            EVENT_TYPE_LABELS.get(e.event_type, e.event_type.replace("_", " ")) for e in group
        ))
        if len(categories) >= 2:
            narrative = (
                f"{len(group)} events across {len(categories)} different signal types within "
                f"{span_seconds}s — {'; '.join(labels[:4])}"
            )
        else:
            narrative = f"{len(group)} event(s) within {span_seconds}s — {'; '.join(labels[:4])}"

        incidents.append({
            "start": group[0].created_at, "end": group[-1].created_at,
            "event_count": len(group),
            "category_count": len(categories),
            "categories": [SIGNAL_CATEGORY_LABELS.get(c, c.capitalize()) for c in categories],
            "coordinated": coordinated,
            "narrative": narrative,
        })

    return sorted(incidents, key=lambda i: (i["coordinated"], i["category_count"], i["event_count"]), reverse=True)


def detect_behavioral_patterns(attempt):
    """AI-Based Behavioral Pattern Analysis, for display: this attempt's
    violations clustered into incidents (see _cluster_incidents), worst
    (most coordinated, most signal categories, most events) first — the
    "Behavioral Pattern Analysis" section on the attempt review page. This
    is the same clustering compute_suspicion_score uses internally for its
    coordination bonus, just returned in full for a human to read rather
    than reduced to a number."""
    violations = (
        ProctoringEvent.query.filter_by(attempt_id=attempt.id, severity="violation")
        .order_by(ProctoringEvent.created_at).all()
    )
    return _cluster_incidents(violations)


def compute_quality_score(attempt):
    """Proctoring Quality Score: how *reliable* the recorded evidence for
    this attempt is, as distinct from compute_suspicion_score's "how
    suspicious is this attempt" — a poorly-lit, silent-mic, choppy-network
    attempt can still be perfectly honest, but it's evidence an admin
    should trust less (or ask the student to retake with better setup),
    which is exactly the kind of thing this score exists to surface
    rather than silently fold into the suspicion number.

    Combines five dimensions, each scored 0-100 and averaged (missing
    dimensions — e.g. no quality_sample events were ever reported — are
    left out of the average rather than penalized, so a short/aborted
    attempt doesn't automatically read as "poor quality"):
      - lighting: from Attempt.avg_brightness/min_brightness (see
        check_snapshot, which samples this on every periodic frame it
        already decodes for face-count checking — no extra client work).
      - face_visibility: how much of the attempt had no_face/
        multiple_faces trouble, from the existing violation log.
      - audio: average mic input level from quality_sample events.
      - network: how many connection_lost events occurred, from the
        existing violation log.
      - resolution: the webcam capture resolution reported in
        quality_sample/precheck_completed events.
    """
    dimensions = {}

    # Lighting
    if attempt.avg_brightness is not None:
        avg_b = attempt.avg_brightness
        if 90 <= avg_b <= 170:
            lighting_score = 100
        elif 60 <= avg_b < 90 or 170 < avg_b <= 200:
            lighting_score = 70
        else:
            lighting_score = 35
        if attempt.min_brightness is not None and attempt.min_brightness < 40:
            lighting_score = min(lighting_score, 50)  # at least one period was quite dark, even if the average looks fine
        dimensions["lighting"] = round(lighting_score, 1)

    events = ProctoringEvent.query.filter_by(attempt_id=attempt.id).order_by(ProctoringEvent.created_at).all()
    end_time = attempt.submitted_at or datetime.utcnow()
    duration_minutes = max((end_time - attempt.started_at).total_seconds() / 60, 1) if attempt.started_at else 1

    # Face visibility
    face_issues = sum(1 for e in events if e.event_type in ("no_face", "multiple_faces"))
    face_rate = face_issues / duration_minutes
    dimensions["face_visibility"] = round(max(100 - min(face_rate * 15, 100), 0), 1)

    # Audio, from periodic quality_sample events
    audio_levels = []
    resolutions = []
    for e in events:
        if e.event_type != "quality_sample" or not e.details:
            continue
        try:
            sample = json.loads(e.details)
        except (TypeError, ValueError):
            continue
        if isinstance(sample.get("audio_rms"), (int, float)):
            audio_levels.append(sample["audio_rms"])
        if sample.get("resolution"):
            resolutions.append(sample["resolution"])

    if audio_levels:
        avg_rms = sum(audio_levels) / len(audio_levels)
        if 0.015 <= avg_rms <= 0.35:
            audio_score = 100
        elif avg_rms < 0.015:
            audio_score = 40  # mic picking up next to nothing — hard to verify audio evidence either way
        else:
            audio_score = 55  # frequently loud/near-clipping
        dimensions["audio"] = round(audio_score, 1)

    # Network stability
    lost_count = sum(1 for e in events if e.event_type == "connection_lost")
    dimensions["network"] = round(max(100 - min(lost_count * 15, 100), 0), 1)

    # Resolution (informational-leaning, light penalty only for genuinely low capture)
    if resolutions:
        last_res = resolutions[-1]
        try:
            w, h = (int(v) for v in str(last_res).lower().split("x"))
            dimensions["resolution"] = 100 if w * h >= 640 * 480 else (70 if w * h >= 320 * 240 else 40)
        except (ValueError, AttributeError):
            pass

    if not dimensions:
        return {"score": None, "level": None, "dimensions": {}, "resolution": None}

    overall = round(sum(dimensions.values()) / len(dimensions))
    if overall >= 85:
        level = "excellent"
    elif overall >= 65:
        level = "good"
    elif overall >= 45:
        level = "fair"
    else:
        level = "poor"

    return {
        "score": overall, "level": level, "dimensions": dimensions,
        "resolution": resolutions[-1] if resolutions else None,
    }


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

    # AI-Based Behavioral Pattern Analysis: does any single incident (a
    # cluster of violations close together in time — see _cluster_incidents)
    # combine multiple *different* signal sources, not just one sensor
    # repeating? This is a materially different (and stronger) signal than
    # the plain distinct_types bonus below, which only checks "how many
    # kinds of violation happened anywhere in the whole attempt" with no
    # regard for timing — three unrelated, far-apart incidents each of a
    # single type would satisfy that, but wouldn't satisfy this.
    incidents = _cluster_incidents(violations)
    worst_incident = incidents[0] if incidents else None
    coordinated_categories = worst_incident["category_count"] if worst_incident and worst_incident["coordinated"] else 0

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
    if coordinated_categories >= 2:
        bonus += 8
    if coordinated_categories >= 3:
        bonus += 7

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
    if coordinated_categories >= 2:
        cats = ", ".join(worst_incident["categories"])
        signals.append(f"a coordinated pattern combining {coordinated_categories} signal types ({cats}) within {INCIDENT_WINDOW_SECONDS}s")
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
    """Merge this attempt's proctoring events, flagged snapshots, answer
    changes, and recording-chunk uploads into one chronological "Behavior
    Timeline" powering Complete Exam Replay — every entry carries
    offset_seconds (seconds since attempt.started_at) so the UI can render
    one combined feed and jump to the right moment (in both the webcam and
    screen recordings, and the matching answer) when an entry is clicked.

    Recordings are uploaded in short MediaRecorder chunks as they complete
    (see the "video/audio recording" section below), so a chunk's
    Recording.created_at is a good proxy for when that segment of footage
    *ends* — we don't separately store each chunk's actual duration. From
    that, each recording is given an approximate [start, end) playback
    window: it starts where the previous chunk of the *same kind* (or the
    attempt itself) left off, and ends at its own created_at. Webcam and
    screen segments are tracked independently (see `segments`, keyed by
    kind) since the two tracks are recorded and uploaded on entirely
    separate MediaRecorder instances and can drift slightly out of step
    with each other. That's what jump-to-timestamp uses to pick the right
    <video> element for each track and seek within it — an approximation
    based on upload timing, not a frame-accurate cut, but within a few
    seconds in practice since chunks upload promptly."""
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

    # Complete Exam Replay: answer changes, so the replay's timeline and
    # per-question panel can show which question was being worked on as
    # the recording plays. Deliberately no answer *content* here — just
    # "this question changed" — the actual value lives on Answer.
    for ae in sorted(attempt.answer_events, key=lambda a: a.created_at):
        q = ae.question
        preview = (q.question_text or "").strip().replace("\n", " ")
        if len(preview) > 70:
            preview = preview[:70].rstrip() + "…"
        verb = "Answered" if ae.action == "first_answered" else "Changed answer to"
        items.append({
            "type": "answer",
            "id": ae.id,
            "time": ae.created_at,
            "offset": offset(ae.created_at),
            "severity": None,
            "label": f"{verb}: {preview}",
            "detail": None,
            "anchor": f"answer-q-{q.id}",
        })

    # Webcam and screen recordings are separate MediaRecorder streams (see
    # RECORDING_KINDS) uploaded on their own independent chunk cadence, so
    # each kind gets its own running [start, end) window rather than
    # sharing one — otherwise a gap or stall in one track would misalign
    # the other's segment boundaries.
    recordings_by_kind = {}
    for rec in attempt.recordings:
        recordings_by_kind.setdefault(rec.kind, []).append(rec)

    segments = {}
    for kind, recordings in recordings_by_kind.items():
        recordings.sort(key=lambda r: r.chunk_index)
        kind_segments = []
        prev_end = 0.0
        for rec in recordings:
            end = offset(rec.created_at)
            end = max(end, prev_end)  # chunks should upload in order, but never let a clock skew invert a segment
            kind_segments.append({
                "recording_id": rec.id, "chunk_index": rec.chunk_index,
                "start": prev_end, "end": end, "kind": kind,
                "url": url_for("proctoring.serve_recording", recording_id=rec.id),
                "anchor": f"rec-{rec.id}",
            })
            items.append({
                "type": "recording",
                "id": rec.id,
                "time": rec.created_at,
                "offset": end,
                "severity": None,
                "label": f"{kind.capitalize()} segment {rec.chunk_index + 1} saved",
                "detail": f"{(rec.file_size / 1024):.1f} KB",
                "anchor": f"rec-{rec.id}",
            })
            prev_end = end
        segments[kind] = kind_segments

    items.sort(key=lambda it: it["offset"])
    # `segments` (flattened, for the legacy single-track view) kept
    # alongside `segments_by_kind` (for the dual-track replay view) so
    # existing callers/templates that only know about one recording track
    # keep working unchanged.
    flat_segments = [seg for kind_segments in segments.values() for seg in kind_segments]
    return {"entries": items, "segments": flat_segments, "segments_by_kind": segments}


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


# Configurable Proctoring Policies: the actions an admin can assign to an
# event type for a specific test (see Test.proctoring_policy / get_policy
# below), and what each one means for _record_violation:
#   ignore    — still logged (severity "info"), but never counted toward
#               violation_count, the suspicion score, or termination. Use
#               for a signal this test doesn't consider meaningful at all
#               (e.g. an org running a low-stakes practice test might
#               ignore window_blur entirely).
#   warning   — logged as severity "warning": visible in the log and to
#               the student's running violation feed, but — like the
#               existing warning-severity events (window_blur, audio_
#               violation by default) — doesn't count toward
#               violation_count/suspicion score/termination either. See
#               "Customizable Warning System" below for the optional
#               warning_limit that turns this into "warn the first N
#               times, then escalate."
#   flag      — logged as severity "violation": counts toward
#               violation_count and the suspicion score, and toward
#               MAX_VIOLATIONS_BEFORE_TERMINATION same as today. This is
#               what "violation"-severity events already do by default —
#               explicitly choosing it is how an admin makes a normally-
#               softer event type (e.g. window_blur) count for real.
#   terminate — logged as severity "violation" AND ends the attempt
#               immediately on the very first occurrence, regardless of
#               MAX_VIOLATIONS_BEFORE_TERMINATION. For the handful of
#               signals a given exam considers zero-tolerance (e.g. an
#               org might decide any phone_detected ends a high-stakes
#               certification exam on the spot).
POLICY_ACTIONS = ("ignore", "warning", "flag", "terminate")

# Customizable Warning System: what a "warning" action can escalate to
# once its warning_limit is exceeded (see _resolve_policy_action below).
# Deliberately a subset of POLICY_ACTIONS — escalating a warning into
# another warning would be a no-op, and "ignore" doesn't make sense as an
# escalation outcome.
ESCALATE_ACTIONS = ("flag", "terminate")

# The event types exposed in the admin policy editor — deliberately just
# the ones with a hand-set entry in EVENT_WEIGHTS (i.e. the ones that
# normally carry real scoring weight), since those are the signals a
# policy override actually changes something meaningful about. A handful
# of event types exist purely for audit/context (e.g. session_resumed,
# identity_spotcheck_passed, connection_restored) and are intentionally
# left out — they were never going to affect scoring either way.
POLICY_CONFIGURABLE_EVENT_TYPES = list(EVENT_WEIGHTS.keys())

# Default per-event-type entry when a test's policy has nothing configured
# for that event type at all — every key defaults to "no override, use
# whatever the caller/normal severity was," matching pre-this-feature
# behavior exactly.
_DEFAULT_POLICY_ENTRY = {
    "action": "default", "warning_limit": None, "escalate_action": None,
    "message": None, "grace_period_seconds": None,
}


def get_policy(test):
    """This test's event_type -> policy-entry overrides, parsed from
    Test.proctoring_policy. Each entry is a dict with keys "action"
    (POLICY_ACTIONS, or "default" for no override), "warning_limit" (see
    Customizable Warning System below), "escalate_action", "message" (a
    custom student-facing message for this event type), and
    "grace_period_seconds". Any key missing from a stored entry — or the
    whole event type missing — falls back to _DEFAULT_POLICY_ENTRY, so a
    test that has never touched its policy (or only set some of these
    fields) behaves exactly as before for anything it didn't configure.

    Backward compatible with the pre-Customizable-Warning-System format,
    where a stored entry was just a bare action string (e.g.
    '"phone_detected": "terminate"') rather than an object — that's
    normalized into {"action": "terminate", ...defaults} here so callers
    never need to care which format is on disk."""
    if not test.proctoring_policy:
        return {}
    try:
        data = json.loads(test.proctoring_policy)
    except (TypeError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}

    policy = {}
    for event_type, raw in data.items():
        if isinstance(raw, str):
            if raw not in POLICY_ACTIONS:
                continue
            entry = dict(_DEFAULT_POLICY_ENTRY, action=raw)
        elif isinstance(raw, dict):
            entry = dict(_DEFAULT_POLICY_ENTRY)
            action = raw.get("action", "default")
            if action == "default" or action in POLICY_ACTIONS:
                entry["action"] = action
            warning_limit = raw.get("warning_limit")
            if isinstance(warning_limit, int) and warning_limit > 0:
                entry["warning_limit"] = warning_limit
            escalate_action = raw.get("escalate_action")
            if escalate_action in ESCALATE_ACTIONS:
                entry["escalate_action"] = escalate_action
            message = raw.get("message")
            if isinstance(message, str) and message.strip():
                entry["message"] = message.strip()[:300]
            grace = raw.get("grace_period_seconds")
            if isinstance(grace, int) and grace > 0:
                entry["grace_period_seconds"] = grace
        else:
            continue
        policy[event_type] = entry
    return policy


def _resolve_policy_action(attempt, event_type, entry):
    """Turn a policy entry into the concrete action to apply *this
    occurrence* — resolving the Customizable Warning System's warning
    limit against how many warnings this event type has already used up
    on this attempt. Returns (action, warnings_used, warnings_remaining)
    where action is one of POLICY_ACTIONS or None (no override — caller's
    own severity stands), warnings_used/remaining are None unless the
    policy actually has a warning_limit configured for this event type
    (so the UI can tell "this event type isn't warning-limited" from "0
    remaining")."""
    action = entry["action"]
    if action != "warning" or not entry["warning_limit"]:
        return (None if action == "default" else action), None, None

    counts = json.loads(attempt.warning_counts) if attempt.warning_counts else {}
    used = counts.get(event_type, 0) + 1  # this occurrence
    counts[event_type] = used
    attempt.warning_counts = json.dumps(counts)

    limit = entry["warning_limit"]
    if used <= limit:
        return "warning", used, max(limit - used, 0)
    # Limit exceeded on this occurrence — escalate. No configured
    # escalate_action defaults to "flag", the least disruptive escalation.
    return entry["escalate_action"] or "flag", used, 0


def _policy_message(entry, event_type, resolved_action, warnings_remaining):
    """The student-facing message for this occurrence — the admin's
    custom per-event-type message if one is configured, otherwise a
    sensible generic fallback that still reflects the resolved action
    (plain warning vs. "this just used up your last warning")."""
    label = EVENT_TYPE_LABELS.get(event_type, event_type.replace("_", " "))
    if entry["message"]:
        base = entry["message"]
    elif resolved_action == "terminate":
        base = f"This exam ends immediately on {label}."
    else:
        base = f"Warning: {label}."
    if warnings_remaining is not None:
        if warnings_remaining > 0:
            base += f" ({warnings_remaining} warning{'s' if warnings_remaining != 1 else ''} left before this counts as a violation.)"
        elif resolved_action == "warning":
            base += " (Last warning before this counts as a violation.)"
    return base


def _record_violation(attempt, event_type, severity, details="", confidence=None):
    entry = get_policy(attempt.test).get(event_type, _DEFAULT_POLICY_ENTRY)

    # Customizable Warning System: a grace period suppresses this event
    # type entirely (logged as "info", never counted, no student-facing
    # message) for the configured number of seconds from the start of the
    # attempt — e.g. an admin might give students 30s of tab_hidden grace
    # while they're still getting the exam window arranged.
    if entry["grace_period_seconds"] and attempt.started_at:
        elapsed = (datetime.utcnow() - attempt.started_at).total_seconds()
        if elapsed < entry["grace_period_seconds"]:
            event = ProctoringEvent(
                attempt_id=attempt.id, event_type=event_type, severity="info",
                details=(details + " (within grace period)").strip(), confidence=confidence,
            )
            db.session.add(event)
            db.session.commit()
            return False

    resolved_action, warnings_used, warnings_remaining = _resolve_policy_action(attempt, event_type, entry)

    force_terminate = False
    message = None
    if resolved_action == "ignore":
        severity = "info"
    elif resolved_action == "warning":
        severity = "warning"
        message = _policy_message(entry, event_type, resolved_action, warnings_remaining)
    elif resolved_action == "flag":
        severity = "violation"
        if warnings_used is not None:  # this was an escalation from warning -> flag
            message = _policy_message(entry, event_type, resolved_action, warnings_remaining)
    elif resolved_action == "terminate":
        severity = "violation"
        force_terminate = True
        message = _policy_message(entry, event_type, resolved_action, warnings_remaining)
    # resolved_action is None (no override for this event type) — keep
    # whatever severity the caller passed in, same as before this feature.

    event = ProctoringEvent(
        attempt_id=attempt.id, event_type=event_type, severity=severity,
        details=details, confidence=confidence,
    )
    db.session.add(event)

    terminated = False
    if severity == "violation":
        attempt.violation_count += 1
        max_v = current_app.config["MAX_VIOLATIONS_BEFORE_TERMINATION"]
        if attempt.status == "in_progress" and (force_terminate or attempt.violation_count >= max_v):
            attempt.status = "terminated"
            attempt.termination_reason = (
                f"This exam is configured to terminate immediately on "
                f"{EVENT_TYPE_LABELS.get(event_type, event_type.replace('_', ' '))}."
                if force_terminate else f"Exceeded {max_v} proctoring violations."
            )
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
    attempt._last_event_message = message  # read by log_event() below; not persisted
    return terminated


# Public name for the same function — used by app.exam_sessions (and
# anywhere else outside this module) so a device/session violation goes
# through the exact same violation-count/suspicion-score/termination/
# high-risk-alert bookkeeping as every other event type, instead of a
# module poking ProctoringEvent rows in directly and quietly skipping all
# of that.
record_violation = _record_violation


# ---------------------------------------------------------------------------
# Candidate Technical Pre-Check: a self-hosted download-speed test blob.
# Timing a request against a public CDN would be a more realistic
# real-world speed test, but this app's outbound network access is
# allow-listed to specific domains (see the sandbox's network config) and
# a public CDN isn't among them — self-hosting also means the test works
# the same in any deployment, with no dependency on a third party's
# availability. A fixed size is generated once at import time and reused
# for every request (not regenerated per-request — there's no reason to
# spend CPU on that) with cache-busting headers so the browser can't just
# serve a cached copy and report a fake instant "download".
# ---------------------------------------------------------------------------
SPEEDTEST_BLOB_SIZE = 1_500_000  # ~1.5MB — enough to get a stable timing over a few hundred ms even on a fast link
_speedtest_blob = os.urandom(SPEEDTEST_BLOB_SIZE)


@bp.route("/speedtest-blob")
@student_required
def speedtest_blob():
    return Response(
        _speedtest_blob, mimetype="application/octet-stream",
        headers={"Cache-Control": "no-store, no-cache, must-revalidate", "Content-Length": str(SPEEDTEST_BLOB_SIZE)},
    )


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
    message = getattr(attempt, "_last_event_message", None)
    return jsonify({
        "ok": True, "terminated": terminated, "violation_count": attempt.violation_count,
        "message": message,
    })


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
        brightness = float(gray.mean())
    except Exception as exc:  # pragma: no cover - defensive
        return jsonify({"ok": False, "error": str(exc)}), 400

    # Proctoring Quality Score: update the running lighting mean/min for
    # every frame, not just ones that get flagged — the flagged-only
    # snapshots saved below are a biased sample (already-bad frames), not
    # representative of typical lighting across the exam.
    n = attempt.brightness_sample_count
    attempt.avg_brightness = brightness if n == 0 else (attempt.avg_brightness * n + brightness) / (n + 1)
    attempt.min_brightness = brightness if attempt.min_brightness is None else min(attempt.min_brightness, brightness)
    attempt.brightness_sample_count = n + 1

    terminated = False
    if count == 0:
        terminated = _record_violation(attempt, "no_face", "warning", "Server check: no face in frame")
        _save_flagged_snapshot(attempt, frame, count)
    elif count > 1:
        terminated = _record_violation(attempt, "multiple_faces", "violation", f"Server check: {count} faces in frame")
        _save_flagged_snapshot(attempt, frame, count)
    else:
        # _record_violation (above) commits on the flagged paths; a normal
        # frame still needs its own commit so the brightness running mean
        # isn't lost.
        db.session.commit()

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
#
# Complete Exam Replay: the same endpoint also accepts the student's screen
# share (a second, independent MediaRecorder in proctor.js — see
# startScreenRecording) as its own "kind" of chunk, indexed separately from
# the webcam. The two tracks are stored and served identically; only
# Recording.kind and the on-disk subfolder differ, so a screen-share
# decline/failure just means no "screen" rows ever get created for this
# attempt rather than affecting the webcam recording at all.
# ---------------------------------------------------------------------------
RECORDING_KINDS = ("webcam", "screen")


@bp.route("/recording/chunk", methods=["POST"])
@student_required
def upload_recording_chunk():
    attempt_id = request.form.get("attempt_id", type=int)
    chunk_index = request.form.get("chunk_index", type=int, default=0)
    kind = request.form.get("kind", "webcam")
    if kind not in RECORDING_KINDS:
        kind = "webcam"
    blob = request.files.get("chunk")

    attempt = _get_owned_attempt(attempt_id)
    if not attempt:
        return jsonify({"ok": False, "error": "attempt not found"}), 404
    if not blob:
        return jsonify({"ok": False, "error": "no file uploaded"}), 400

    attempt_dir = os.path.join(current_app.config["RECORDINGS_DIR"], str(attempt.id), kind)
    os.makedirs(attempt_dir, exist_ok=True)

    filename = f"chunk_{chunk_index:05d}.webm"
    filepath = os.path.join(attempt_dir, filename)
    blob.save(filepath)
    size = os.path.getsize(filepath)

    rec = Recording(
        attempt_id=attempt.id,
        kind=kind,
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

    filepath = os.path.join(current_app.config["RECORDINGS_DIR"], str(attempt.id), rec.kind, rec.filename)
    if not os.path.exists(filepath):
        # Pre-existing recordings from before Complete Exam Replay's
        # kind-specific subfolders were saved flat under the attempt dir —
        # fall back so old webcam recordings still play.
        legacy_path = os.path.join(current_app.config["RECORDINGS_DIR"], str(attempt.id), rec.filename)
        if not os.path.exists(legacy_path):
            abort(404)
        filepath = legacy_path

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
