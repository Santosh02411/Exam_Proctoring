import json
import secrets
from datetime import datetime

from flask import Blueprint, render_template, redirect, url_for, flash, request, abort, jsonify, current_app, Response
from flask_login import current_user

from app import db
from app.models import (
    Test, TestEligibility, Attempt, Answer, Question, Section, IdentityDocument, AnswerEvent,
    recompute_attempt_score,
)
from app.utils import student_required
from app.randomize import build_attempt_order, ordered_questions, get_option_order
from app.notifications import notify_exam_completed_and_maybe_published
from app.exam_sessions import claim_session, record_blocked_concurrent_session, validate_session_token
from app import certificates

bp = Blueprint("student", __name__, url_prefix="/student")


@bp.route("/dashboard")
@student_required
def dashboard():
    eligibilities = TestEligibility.query.filter_by(student_id=current_user.id).all()
    rows = []
    for e in eligibilities:
        test = e.test
        attempts = Attempt.query.filter_by(test_id=test.id, student_id=current_user.id).order_by(
            Attempt.started_at.desc()
        ).all()
        latest = attempts[0] if attempts else None
        completed_count = len([a for a in attempts if a.status != "in_progress"])
        allowed_attempts = test.max_attempts + e.extra_attempts
        rows.append({
            "test": test, "attempt": latest, "eligibility": e,
            "completed_count": completed_count,
            "attempts_left": max(allowed_attempts - completed_count, 0),
        })
    return render_template("student/dashboard.html", rows=rows)


@bp.route("/enroll-face")
@student_required
def enroll_face():
    document = IdentityDocument.query.filter_by(user_id=current_user.id).first()
    return render_template("student/enroll_face.html", document=document)


def _get_or_create_attempt(test, eligibility):
    """Returns (attempt, is_resumed) — is_resumed is True when an
    already-in-progress attempt was found (page refresh, browser crash,
    or a reconnect after a dropped connection), False for a brand new
    attempt. The exam UI uses this to skip straight past the "fresh
    start" framing and tell the student they're continuing where they
    left off, with the correct time remaining (see _remaining_seconds)."""
    existing = Attempt.query.filter_by(test_id=test.id, student_id=current_user.id).order_by(
        Attempt.started_at.desc()
    ).first()
    if existing and existing.status == "in_progress":
        return existing, True

    questions = Question.query.filter_by(test_id=test.id).order_by(Question.id).all()
    token = secrets.token_hex(16)
    question_order, option_order = build_attempt_order(
        questions, token, test.randomize_questions, test.randomize_options,
        pool_size=test.question_pool_size,
    )

    attempt = Attempt(
        attempt_token=token,
        test_id=test.id,
        student_id=current_user.id,
        status="in_progress",
        question_order=question_order,
        option_order=option_order,
    )
    db.session.add(attempt)
    db.session.commit()
    return attempt, False


def _allotted_seconds(test, eligibility):
    return (test.duration_minutes + eligibility.extra_time_minutes) * 60


def _remaining_seconds(attempt, test, eligibility):
    """Time left, computed server-side from attempt.started_at rather than
    trusting a client-held countdown. This is what makes the timer survive
    a refresh, a crash, or a long connection drop: the client always
    re-derives "time left" from this on load (and again via /heartbeat),
    instead of restarting a fresh countdown from the full duration."""
    total = _allotted_seconds(test, eligibility)
    elapsed = (datetime.utcnow() - attempt.started_at).total_seconds()
    return max(int(total - elapsed), 0)


def _merge_question_time(attempt, form):
    """Add this request's reported per-question time deltas (seconds since
    the client's last successful autosave/submit — see proctor.js) onto the
    attempt's running total. Additive rather than overwrite: the client
    resets its own in-memory counter after each successful send, so a
    refresh mid-exam (which resets the client's JS state but not the
    server's stored total) never double-counts or loses already-reported
    time. Malformed/missing data is silently ignored — this is a secondary
    analytics signal, not exam-critical."""
    raw = form.get("question_time_spent")
    if not raw:
        return
    try:
        delta_map = json.loads(raw)
    except (TypeError, ValueError):
        return
    if not isinstance(delta_map, dict):
        return

    current = json.loads(attempt.question_time_spent) if attempt.question_time_spent else {}
    for qid, seconds in delta_map.items():
        try:
            seconds = int(seconds)
        except (TypeError, ValueError):
            continue
        if seconds <= 0:
            continue
        current[str(qid)] = current.get(str(qid), 0) + seconds
    attempt.question_time_spent = json.dumps(current)


def _finalize_attempt(attempt, answers_map):
    """Shared write path for turning a set of question_id -> submitted_value
    answers into persisted Answer rows and a final score. Used both by the
    real submit endpoint (answers_map built from the POSTed form) and by
    the server-side time-expiry path (answers_map built from whatever was
    last autosaved), so a student who was offline when their timer ran out
    still gets graded on their last known answers instead of losing the
    attempt outright."""
    all_questions = Question.query.filter_by(test_id=attempt.test_id).all()
    questions = {q.id: q for q in ordered_questions(all_questions, attempt.question_order)}
    time_spent = json.loads(attempt.question_time_spent) if attempt.question_time_spent else {}
    for q_id, question in questions.items():
        selected = answers_map.get(str(q_id))
        db.session.add(Answer(
            attempt_id=attempt.id, question_id=q_id, selected_option=selected,
            time_spent_seconds=time_spent.get(str(q_id)),
        ))
    db.session.flush()

    attempt.score = recompute_attempt_score(attempt)
    attempt.submitted_at = datetime.utcnow()
    attempt.status = "submitted"
    attempt.autosaved_answers = None


@bp.route("/tests/<int:test_id>/start")
@student_required
def start_test(test_id):
    test = Test.query.get_or_404(test_id)

    eligibility = TestEligibility.query.filter_by(test_id=test.id, student_id=current_user.id).first()
    if not eligibility:
        abort(403, description="You are not eligible for this test.")

    if test.status != "published":
        flash("This test is not currently available.", "error")
        return redirect(url_for("student.dashboard"))

    if not test.is_open_now():
        flash("This test is not open at this time.", "error")
        return redirect(url_for("student.dashboard"))

    if not current_user.face_descriptor:
        flash("Please enroll your face for identity verification before starting a proctored test.", "warning")
        return redirect(url_for("student.enroll_face"))

    completed_count = Attempt.query.filter_by(test_id=test.id, student_id=current_user.id).filter(
        Attempt.status != "in_progress"
    ).count()
    allowed_attempts = test.max_attempts + eligibility.extra_attempts
    if completed_count >= allowed_attempts:
        flash(f"You've used all {allowed_attempts} attempt(s) allowed for this test.", "info")
        return redirect(url_for("student.dashboard"))

    attempt, is_resume = _get_or_create_attempt(test, eligibility)

    if attempt.status == "in_progress" and _remaining_seconds(attempt, test, eligibility) <= 0:
        # The allotted time ran out while nobody was actively submitting —
        # most commonly because the student was disconnected (or had
        # crashed/closed the tab) right up to the deadline. Grade on
        # whatever was last autosaved rather than leaving the attempt
        # stuck in_progress forever, then send them straight to results
        # instead of re-rendering an exam page with a dead timer.
        saved = json.loads(attempt.autosaved_answers) if attempt.autosaved_answers else {}
        attempt.termination_reason = "Time expired."
        _finalize_attempt(attempt, saved)
        db.session.commit()
        notify_exam_completed_and_maybe_published(attempt)
        flash("Time expired for this attempt — it was auto-submitted with your last saved answers.", "info")
        return redirect(url_for("student.result", attempt_id=attempt.id))

    if attempt.status == "in_progress":
        claimed = claim_session(
            attempt, current_app.config["EXAM_SESSION_STALE_AFTER_SECONDS"],
            enforce=current_app.config["EXAM_SESSION_ENFORCE_SINGLE_SESSION"],
        )
        if not claimed:
            record_blocked_concurrent_session(attempt)
            return render_template("student/session_blocked.html", test=test), 409

    all_questions = Question.query.filter_by(test_id=test.id).all()
    questions = ordered_questions(all_questions, attempt.question_order)
    option_order_by_qid = {q.id: get_option_order(attempt.option_order, q.id) for q in questions}

    reference_descriptor = json.loads(current_user.face_descriptor) if current_user.face_descriptor else None
    remaining_seconds = _remaining_seconds(attempt, test, eligibility)

    saved_answers = json.loads(attempt.autosaved_answers) if attempt.autosaved_answers else {}

    sections = Section.query.filter_by(test_id=test.id).order_by(Section.order_index).all()
    questions_by_section = {s.id: [] for s in sections}
    unsectioned = []
    for q in questions:
        if q.section_id and q.section_id in questions_by_section:
            questions_by_section[q.section_id].append(q)
        else:
            unsectioned.append(q)

    return render_template(
        "student/take_test.html", test=test, attempt=attempt, questions=questions,
        option_order_by_qid=option_order_by_qid,
        reference_descriptor=reference_descriptor,
        duration_seconds=remaining_seconds,
        is_resume=is_resume,
        extra_time_minutes=eligibility.extra_time_minutes,
        saved_answers=saved_answers,
        sections=sections, questions_by_section=questions_by_section, unsectioned=unsectioned,
    )


@bp.route("/attempts/<int:attempt_id>/autosave", methods=["POST"])
@student_required
def autosave_answers(attempt_id):
    """Periodic in-progress save so a refresh/crash mid-exam doesn't lose
    answers already picked — see Attempt.autosaved_answers. Never affects
    grading; final scoring only ever reads the real POST to submit_answers."""
    attempt = Attempt.query.get_or_404(attempt_id)
    if attempt.student_id != current_user.id:
        abort(403)
    if attempt.status != "in_progress":
        return jsonify({"ok": True, "saved": False, "reason": "attempt not in progress"})
    if not validate_session_token(attempt, request.form.get("session_token")):
        return jsonify({"ok": False, "error": "session_superseded"}), 409

    all_questions = Question.query.filter_by(test_id=attempt.test_id).all()
    questions = ordered_questions(all_questions, attempt.question_order)
    previous = json.loads(attempt.autosaved_answers) if attempt.autosaved_answers else {}
    answers = {}
    for q in questions:
        value = _extract_submitted_answer(q, request.form)
        if value is not None:
            answers[str(q.id)] = value

    _log_answer_changes(attempt, previous, answers)
    attempt.autosaved_answers = json.dumps(answers)
    _merge_question_time(attempt, request.form)
    db.session.commit()
    return jsonify({"ok": True, "saved": True, "saved_at": datetime.utcnow().isoformat()})


def _log_answer_changes(attempt, previous, answers):
    """Complete Exam Replay: record a lightweight, content-free timestamp
    (see AnswerEvent) each time this autosave's answers differ from the
    previous autosave — either a question getting its first value, or an
    already-answered question's value changing. Skipped entirely when
    nothing changed (the common case — most autosave ticks fire with no
    new input since the last one), so this doesn't add a row per
    autosave, just per actual edit."""
    for question_id, value in answers.items():
        old_value = previous.get(question_id)
        if value == old_value:
            continue
        action = "first_answered" if not old_value else "changed"
        db.session.add(AnswerEvent(
            attempt_id=attempt.id, question_id=int(question_id), action=action,
        ))


def _extract_submitted_answer(question, form):
    """Read a submitted answer for one question from the POSTed form, in the
    representation Question.is_correct() expects to compare against.
    true_false submits like single (form.get, lowercased) — the value is
    "true"/"false" rather than a letter, but the handling is identical.
    short/fill_blank/descriptive/coding are free text — kept at original
    case/whitespace (trimmed) since case matters for code and for some
    fill-in-the-blank answers, and lowercasing an essay would be odd."""
    field = f"q_{question.id}"
    if question.question_type == "multi":
        picks = sorted({v.strip().lower() for v in form.getlist(field) if v.strip()})
        return ",".join(picks) if picks else None
    if question.question_type in ("short", "fill_blank", "descriptive", "coding"):
        text = (form.get(field) or "").strip()
        return text or None
    selected = form.get(field)
    return selected.strip().lower() if selected else None


@bp.route("/attempts/<int:attempt_id>/submit", methods=["POST"])
@student_required
def submit_answers(attempt_id):
    attempt = Attempt.query.get_or_404(attempt_id)
    if attempt.student_id != current_user.id:
        abort(403)
    if attempt.status != "in_progress":
        return jsonify({"ok": True, "already_submitted": True, "redirect": url_for("student.dashboard")})
    if not validate_session_token(attempt, request.form.get("session_token")):
        return jsonify({"ok": False, "error": "session_superseded"}), 409

    all_questions = Question.query.filter_by(test_id=attempt.test_id).all()
    questions = {q.id: q for q in ordered_questions(all_questions, attempt.question_order)}
    answers_map = {
        str(q_id): _extract_submitted_answer(question, request.form)
        for q_id, question in questions.items()
    }
    _merge_question_time(attempt, request.form)
    _finalize_attempt(attempt, answers_map)
    db.session.commit()
    notify_exam_completed_and_maybe_published(attempt)

    return jsonify({"ok": True, "redirect": url_for("student.result", attempt_id=attempt.id)})


@bp.route("/attempts/<int:attempt_id>/heartbeat")
@student_required
def heartbeat(attempt_id):
    """Lightweight, cheap-to-poll endpoint the exam page pings to detect
    whether it currently has a working connection to the server, and to
    resync against the server's authoritative clock on reconnect (rather
    than trusting a client-side countdown that may have drifted, or kept
    ticking down while the tab was asleep/offline). Also doubles as a way
    for the client to notice — the moment it's back online — that the
    attempt was terminated or auto-submitted while it was disconnected."""
    attempt = Attempt.query.get_or_404(attempt_id)
    if attempt.student_id != current_user.id:
        abort(403)

    session_conflict = False
    if attempt.status == "in_progress":
        if validate_session_token(attempt, request.args.get("session_token")):
            db.session.commit()  # persist the session_last_seen_at refresh from validate_session_token
        else:
            session_conflict = True

    remaining_seconds = None
    if attempt.status == "in_progress" and not session_conflict:
        test = attempt.test
        eligibility = TestEligibility.query.filter_by(test_id=test.id, student_id=current_user.id).first()
        remaining_seconds = _remaining_seconds(attempt, test, eligibility) if eligibility else None

    return jsonify({
        "ok": True,
        "status": attempt.status,
        "session_conflict": session_conflict,
        "remaining_seconds": remaining_seconds,
        "server_time": datetime.utcnow().isoformat(),
    })


@bp.route("/attempts/<int:attempt_id>/result")
@student_required
def result(attempt_id):
    attempt = Attempt.query.get_or_404(attempt_id)
    if attempt.student_id != current_user.id:
        abort(403)
    test = attempt.test
    total_marks = attempt.max_marks()
    passed = (attempt.score or 0) >= test.passing_marks if attempt.status != "terminated" else False
    pending_grading = any(
        a.question.needs_manual_grading and a.selected_option and a.manual_score is None
        for a in attempt.answers
    )
    return render_template(
        "student/result.html", attempt=attempt, test=test, total_marks=total_marks,
        passed=passed, pending_grading=pending_grading,
        certificate_eligible=certificates.is_eligible(attempt),
    )


@bp.route("/attempts/<int:attempt_id>/certificate")
@student_required
def download_certificate(attempt_id):
    attempt = Attempt.query.get_or_404(attempt_id)
    if attempt.student_id != current_user.id:
        abort(403)
    if not certificates.is_eligible(attempt):
        flash("A certificate isn't available for this attempt.", "error")
        return redirect(url_for("student.result", attempt_id=attempt.id))

    cert = certificates.get_or_create(attempt)
    pdf_bytes = certificates.render_pdf(attempt, cert)
    filename = f"certificate-{attempt.test.test_code}-{attempt.student.name.replace(' ', '_')}.pdf"
    return Response(
        pdf_bytes, mimetype="application/pdf",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@bp.route("/certificates/verify/<string:code>")
def verify_certificate(code):
    """Public (no login required) certificate verification — a third
    party (an employer, another institution) checking a certificate a
    student handed them doesn't have an account here to log in with.
    Deliberately shows only what's needed to confirm authenticity (name,
    test, date, pass/fail), not the full result — no score breakdown,
    proctoring detail, or anything else from the attempt."""
    from app.models import Certificate

    cert = Certificate.query.filter_by(certificate_code=code).first()
    valid = bool(cert) and certificates.is_eligible(cert.attempt)
    return render_template("student/verify_certificate.html", cert=cert, valid=valid)


@bp.route("/attempts/<int:attempt_id>/review")
@student_required
def review(attempt_id):
    attempt = Attempt.query.get_or_404(attempt_id)
    if attempt.student_id != current_user.id:
        abort(403)
    test = attempt.test
    if attempt.status == "in_progress":
        abort(404)
    if not test.allow_review:
        flash("Answer review isn't enabled for this test.", "info")
        return redirect(url_for("student.result", attempt_id=attempt.id))

    answers = {a.question_id: a for a in attempt.answers}
    questions = ordered_questions(Question.query.filter_by(test_id=test.id).all(), attempt.question_order)
    rows = [{"question": q, "answer": answers.get(q.id)} for q in questions]
    return render_template("student/review.html", attempt=attempt, test=test, rows=rows)
