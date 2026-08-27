import json
import secrets
from datetime import datetime

from flask import Blueprint, render_template, redirect, url_for, flash, request, abort, jsonify
from flask_login import current_user

from app import db
from app.models import Test, TestEligibility, Attempt, Answer, Question, Section
from app.utils import student_required
from app.randomize import build_attempt_order, ordered_questions, get_option_order

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
    return render_template("student/enroll_face.html")


def _get_or_create_attempt(test, eligibility):
    existing = Attempt.query.filter_by(test_id=test.id, student_id=current_user.id).order_by(
        Attempt.started_at.desc()
    ).first()
    if existing and existing.status == "in_progress":
        return existing

    questions = Question.query.filter_by(test_id=test.id).order_by(Question.id).all()
    token = secrets.token_hex(16)
    question_order, option_order = build_attempt_order(
        questions, token, test.randomize_questions, test.randomize_options
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
    return attempt


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

    attempt = _get_or_create_attempt(test, eligibility)

    all_questions = Question.query.filter_by(test_id=test.id).all()
    questions = ordered_questions(all_questions, attempt.question_order)
    option_order_by_qid = {q.id: get_option_order(attempt.option_order, q.id) for q in questions}

    reference_descriptor = json.loads(current_user.face_descriptor) if current_user.face_descriptor else None
    duration_seconds = (test.duration_minutes + eligibility.extra_time_minutes) * 60

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
        duration_seconds=duration_seconds,
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

    questions = Question.query.filter_by(test_id=attempt.test_id).all()
    answers = {}
    for q in questions:
        value = _extract_submitted_answer(q, request.form)
        if value is not None:
            answers[str(q.id)] = value

    attempt.autosaved_answers = json.dumps(answers)
    db.session.commit()
    return jsonify({"ok": True, "saved": True, "saved_at": datetime.utcnow().isoformat()})


def _extract_submitted_answer(question, form):
    """Read a submitted answer for one question from the POSTed form, in the
    representation Question.is_correct() expects to compare against."""
    field = f"q_{question.id}"
    if question.question_type == "multi":
        picks = sorted({v.strip().lower() for v in form.getlist(field) if v.strip()})
        return ",".join(picks) if picks else None
    if question.question_type == "short":
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

    test = attempt.test
    questions = {q.id: q for q in Question.query.filter_by(test_id=attempt.test_id).all()}
    score = 0.0
    for q_id, question in questions.items():
        selected = _extract_submitted_answer(question, request.form)
        answer = Answer(attempt_id=attempt.id, question_id=q_id, selected_option=selected)
        db.session.add(answer)
        if not selected:
            continue
        earned = question.score_for(selected, partial_credit_multi=test.partial_credit_multi)
        if earned > 0:
            score += earned
        elif test.negative_marks_per_wrong:
            score -= test.negative_marks_per_wrong

    attempt.score = round(score, 2)
    attempt.submitted_at = datetime.utcnow()
    attempt.status = "submitted"
    attempt.autosaved_answers = None
    db.session.commit()

    return jsonify({"ok": True, "redirect": url_for("student.result", attempt_id=attempt.id)})


@bp.route("/attempts/<int:attempt_id>/result")
@student_required
def result(attempt_id):
    attempt = Attempt.query.get_or_404(attempt_id)
    if attempt.student_id != current_user.id:
        abort(403)
    test = attempt.test
    total_marks = test.total_marks()
    passed = (attempt.score or 0) >= test.passing_marks if attempt.status != "terminated" else False
    return render_template("student/result.html", attempt=attempt, test=test, total_marks=total_marks, passed=passed)


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
