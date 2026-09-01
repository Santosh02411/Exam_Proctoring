"""LMS/API Integrations — a small REST API, authenticated by a per-
organization API key (see app.models.ApiKey), that an external system —
an institutional LMS like Moodle/Canvas/Google Classroom, or custom
integration middleware — can call to push a roster, list published tests,
and pull results for gradebook sync.

Scope note: this deliberately implements plain REST + API-key auth rather
than a full LTI (Learning Tools Interoperability) launch flow. LTI
Advantage is the "native" way Moodle/Canvas embed an external tool with
single-sign-on, deep linking, and automatic grade passback, but it's a
substantial OAuth2/OIDC-based spec of its own — a real deployment wanting
native LTI would sit a small LTI-launch shim in front of this API
(translating an LTI launch into a login/session here, and an LTI AGS
grade-passback call into a read from GET /api/v1/tests/<code>/results
below) rather than this app implementing the LTI spec directly. What's
here covers the same underlying needs (roster sync, result/grade
retrieval) any such shim — or simpler middleware like Zapier/Make, or a
from-scratch institutional integration — would actually need to call. See
Organization.lms_webhook_url / app.notifications._maybe_send_lms_webhook
for the complementary push side (a result gets POSTed out the moment it's
published, instead of the receiver having to poll).

Every endpoint is scoped to exactly one organization, identified by the
Bearer API key in the Authorization header (see require_api_key) — the
same tenant boundary app.utils.ensure_same_org enforces for the admin UI,
just authenticated by key instead of by login session. Manage keys
themselves at Admin -> Settings -> API Access (see app.admin.api_keys).
"""

import secrets
from datetime import datetime
from functools import wraps

from flask import Blueprint, request, jsonify, g, current_app
from werkzeug.security import generate_password_hash, check_password_hash

from app import db
from app.models import ApiKey, Test, TestEligibility, User, Attempt, gen_user_id
from app.notifications import pending_grading

bp = Blueprint("api_v1", __name__, url_prefix="/api/v1")

API_KEY_PREFIX_LEN = 16


def generate_api_key():
    """Returns (raw_key, prefix, key_hash) — raw_key is shown to the admin
    exactly once at creation and never stored; key_hash (salted, like a
    user password) is what persists. prefix is a non-secret slice of
    raw_key kept in the clear purely to make lookup fast (see
    require_api_key) without having to hash-check every key in the org
    — or, worse, every key system-wide — on every request."""
    raw_key = "epk_" + secrets.token_urlsafe(32)
    prefix = raw_key[:API_KEY_PREFIX_LEN]
    return raw_key, prefix, generate_password_hash(raw_key)


def require_api_key(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        auth_header = request.headers.get("Authorization", "")
        raw_key = auth_header[len("Bearer "):].strip() if auth_header.startswith("Bearer ") else None
        raw_key = raw_key or request.headers.get("X-API-Key")
        if not raw_key:
            return jsonify({"ok": False, "error": "missing API key"}), 401

        candidates = ApiKey.query.filter_by(prefix=raw_key[:API_KEY_PREFIX_LEN], revoked=False).all()
        matched = next((k for k in candidates if check_password_hash(k.key_hash, raw_key)), None)
        if not matched:
            return jsonify({"ok": False, "error": "invalid or revoked API key"}), 401

        matched.last_used_at = datetime.utcnow()
        db.session.commit()
        g.api_org_id = matched.org_id
        g.api_key = matched
        return f(*args, **kwargs)
    return wrapper


def _test_payload(test):
    return {
        "test_code": test.test_code,
        "title": test.title,
        "description": test.description,
        "status": test.status,
        "duration_minutes": test.duration_minutes,
        "total_marks": test.total_marks(),
        "passing_marks": test.passing_marks,
        "start_time": test.start_time.isoformat() if test.start_time else None,
        "end_time": test.end_time.isoformat() if test.end_time else None,
    }


def _attempt_payload(attempt):
    return {
        "attempt_id": attempt.id,
        "test_code": attempt.test.test_code,
        "student_email": attempt.student.email,
        "student_name": attempt.student.name,
        "status": attempt.status,
        "score": attempt.score,
        "total_marks": attempt.test.total_marks(),
        "pending_grading": pending_grading(attempt) if attempt.status != "in_progress" else None,
        "violation_count": attempt.violation_count,
        "risk_level": attempt.risk_level,
        "started_at": attempt.started_at.isoformat() if attempt.started_at else None,
        "submitted_at": attempt.submitted_at.isoformat() if attempt.submitted_at else None,
    }


@bp.route("/tests")
@require_api_key
def list_tests():
    tests = Test.query.filter_by(org_id=g.api_org_id, status="published").order_by(Test.created_at.desc()).all()
    return jsonify({"ok": True, "tests": [_test_payload(t) for t in tests]})


@bp.route("/tests/<string:test_code>")
@require_api_key
def get_test(test_code):
    test = Test.query.filter_by(org_id=g.api_org_id, test_code=test_code).first()
    if not test:
        return jsonify({"ok": False, "error": "test not found"}), 404
    return jsonify({"ok": True, "test": _test_payload(test)})


@bp.route("/tests/<string:test_code>/enroll", methods=["POST"])
@require_api_key
def enroll_students(test_code):
    """Roster sync: create-or-fetch each student by email (scoped to this
    org) and grant them eligibility for the test, the same effect as an
    admin using Assign Students in the UI (see app.admin.assign_students)
    — this is the endpoint an LMS-side enrollment sync would call.
    Newly-created accounts get a random password and are pre-verified
    (email_verified=True), matching the existing CSV bulk-import
    behavior in app.admin.import_users; no credentials are emailed here,
    since an institutional sync typically handles account provisioning/
    SSO on its own side."""
    test = Test.query.filter_by(org_id=g.api_org_id, test_code=test_code).first()
    if not test:
        return jsonify({"ok": False, "error": "test not found"}), 404

    body = request.get_json(silent=True) or {}
    students = body.get("students")
    if not isinstance(students, list) or not students:
        return jsonify({"ok": False, "error": "body must include a non-empty 'students' list"}), 400

    extra_time = int(body.get("extra_time_minutes") or 0)
    extra_attempts = int(body.get("extra_attempts") or 0)

    created, enrolled, skipped = 0, 0, []
    for entry in students:
        email = (entry.get("email") or "").strip().lower() if isinstance(entry, dict) else None
        name = (entry.get("name") or "").strip() if isinstance(entry, dict) else None
        if not email or not name:
            skipped.append({"entry": entry, "reason": "missing name or email"})
            continue

        user = User.query.filter_by(email=email, org_id=g.api_org_id).first()
        if not user:
            user = User(
                user_id=gen_user_id("student"), name=name, email=email, phone="0000000000",
                role="student", status="active", email_verified=True, org_id=g.api_org_id,
            )
            user.set_password(secrets.token_urlsafe(18))
            db.session.add(user)
            db.session.flush()
            created += 1
        elif user.role != "student":
            skipped.append({"entry": entry, "reason": "email belongs to a non-student account"})
            continue

        if not TestEligibility.query.filter_by(test_id=test.id, student_id=user.id).first():
            db.session.add(TestEligibility(
                test_id=test.id, student_id=user.id,
                extra_time_minutes=extra_time, extra_attempts=extra_attempts,
            ))
            enrolled += 1

    db.session.commit()
    return jsonify({
        "ok": True, "accounts_created": created, "newly_enrolled": enrolled, "skipped": skipped,
    })


@bp.route("/tests/<string:test_code>/results")
@require_api_key
def test_results(test_code):
    """Grade passback / gradebook sync: every attempt on this test,
    optionally filtered to ones submitted since a given timestamp — the
    endpoint an LMS would poll periodically rather than pulling the full
    history every time. See Organization.lms_webhook_url for the
    complementary push-based alternative to polling this."""
    test = Test.query.filter_by(org_id=g.api_org_id, test_code=test_code).first()
    if not test:
        return jsonify({"ok": False, "error": "test not found"}), 404

    query = Attempt.query.filter_by(test_id=test.id)
    since = request.args.get("since")
    if since:
        try:
            since_dt = datetime.fromisoformat(since)
        except ValueError:
            return jsonify({"ok": False, "error": "'since' must be an ISO-8601 timestamp"}), 400
        query = query.filter(
            (Attempt.submitted_at >= since_dt) | (Attempt.submitted_at.is_(None) & (Attempt.started_at >= since_dt))
        )

    attempts = query.order_by(Attempt.started_at.desc()).all()
    return jsonify({"ok": True, "test_code": test.test_code, "attempts": [_attempt_payload(a) for a in attempts]})


@bp.route("/students/<string:email>/attempts")
@require_api_key
def student_attempts(email):
    student = User.query.filter_by(email=email.strip().lower(), org_id=g.api_org_id, role="student").first()
    if not student:
        return jsonify({"ok": False, "error": "student not found"}), 404

    attempts = (
        Attempt.query.join(Test, Attempt.test_id == Test.id)
        .filter(Attempt.student_id == student.id, Test.org_id == g.api_org_id)
        .order_by(Attempt.started_at.desc())
        .all()
    )
    return jsonify({"ok": True, "student_email": student.email, "attempts": [_attempt_payload(a) for a in attempts]})
