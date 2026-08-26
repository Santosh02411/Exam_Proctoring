import json
import secrets
from datetime import datetime

from flask import Blueprint, render_template, redirect, url_for, flash, request, abort, jsonify
from flask_login import current_user

from app import db
from app.models import Test, TestEligibility, Attempt, Answer, Question
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
        rows.append({
            "test": test, "attempt": latest, "eligibility": e,
            "completed_count": completed_count,
            "attempts_left": max(test.max_attempts - completed_count, 0),
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
    question_order, option_order = build_attempt_order(questions, token, test.randomize_questions)

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
    if completed_count >= test.max_attempts:
        flash(f"You've used all {test.max_attempts} attempt(s) allowed for this test.", "info")
        return redirect(url_for("student.dashboard"))

    attempt = _get_or_create_attempt(test, eligibility)

    all_questions = Question.query.filter_by(test_id=test.id).all()
    questions = ordered_questions(all_questions, attempt.question_order)
    option_order_by_qid = {q.id: get_option_order(attempt.option_order, q.id) for q in questions}

    reference_descriptor = json.loads(current_user.face_descriptor) if current_user.face_descriptor else None
    duration_seconds = (test.duration_minutes + eligibility.extra_time_minutes) * 60

    return render_template(
        "student/take_test.html", test=test, attempt=attempt, questions=questions,
        option_order_by_qid=option_order_by_qid,
        reference_descriptor=reference_descriptor,
        duration_seconds=duration_seconds,
        extra_time_minutes=eligibility.extra_time_minutes,
    )


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
        selected = request.form.get(f"q_{q_id}")
        if selected:
            selected = selected.lower()
        answer = Answer(attempt_id=attempt.id, question_id=q_id, selected_option=selected)
        db.session.add(answer)
        if selected and selected == question.correct_answer:
            score += question.marks
        elif selected and test.negative_marks_per_wrong:
            score -= test.negative_marks_per_wrong

    attempt.score = round(score, 2)
    attempt.submitted_at = datetime.utcnow()
    attempt.status = "submitted"
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
