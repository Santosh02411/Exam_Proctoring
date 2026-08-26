import csv
import io
import time

from flask import Blueprint, render_template, redirect, url_for, flash, request, abort
from flask_login import current_user

from app import db
from app.forms import TestForm, QuestionForm, QuestionImportForm
from app.models import Test, Question, User, TestEligibility, Attempt, ProctoringEvent, AdminActivityLog
from app.utils import admin_required
from app.activity_log import log_activity
from app.email_utils import send_email

bp = Blueprint("admin", __name__, url_prefix="/admin")

PER_PAGE = 15


def _apply_test_form(test, form):
    test.test_code = form.test_code.data.strip()
    test.title = form.title.data.strip()
    test.description = form.description.data
    test.duration_minutes = form.duration_minutes.data
    test.total_questions = form.total_questions.data
    test.passing_marks = form.passing_marks.data
    test.status = form.status.data
    test.start_time = form.start_time.data
    test.end_time = form.end_time.data
    test.max_attempts = form.max_attempts.data
    test.randomize_questions = form.randomize_questions.data
    test.negative_marks_per_wrong = form.negative_marks_per_wrong.data
    test.allow_review = form.allow_review.data


@bp.route("/dashboard")
@admin_required
def dashboard():
    tests = Test.query.filter_by(created_by=current_user.id).order_by(Test.created_at.desc()).all()
    total_students = User.query.filter_by(role="student").count()
    total_attempts = Attempt.query.join(Test).filter(Test.created_by == current_user.id).count()
    return render_template(
        "admin/dashboard.html", tests=tests, total_students=total_students, total_attempts=total_attempts
    )


@bp.route("/tests/create", methods=["GET", "POST"])
@admin_required
def create_test():
    form = TestForm()
    if request.method == "GET":
        form.max_attempts.data = 1
        form.randomize_questions.data = True
        form.allow_review.data = True
    if form.validate_on_submit():
        if Test.query.filter_by(test_code=form.test_code.data.strip()).first():
            flash("Test code already exists — choose a unique one.", "error")
        else:
            test = Test(created_by=current_user.id)
            _apply_test_form(test, form)
            db.session.add(test)
            db.session.commit()
            log_activity("created_test", f"Created test '{test.title}' ({test.test_code})")
            flash("Test created. Now add some questions.", "success")
            return redirect(url_for("admin.add_question", test_id=test.id))
    return render_template("admin/create_test.html", form=form)


@bp.route("/tests/<int:test_id>/edit", methods=["GET", "POST"])
@admin_required
def edit_test(test_id):
    test = Test.query.get_or_404(test_id)
    form = TestForm(obj=test)
    if request.method == "GET":
        form.test_code.data = test.test_code
    if form.validate_on_submit():
        other = Test.query.filter(Test.test_code == form.test_code.data.strip(), Test.id != test.id).first()
        if other:
            flash("Another test already uses that code.", "error")
        else:
            _apply_test_form(test, form)
            db.session.commit()
            log_activity("edited_test", f"Edited test '{test.title}' ({test.test_code})")
            flash("Test updated successfully.", "success")
            return redirect(url_for("admin.manage_tests"))
    return render_template("admin/edit_test.html", form=form, test=test)


@bp.route("/tests")
@admin_required
def manage_tests():
    page = request.args.get("page", 1, type=int)
    mine_only = request.args.get("mine") == "1"

    query = Test.query.order_by(Test.created_at.desc())
    if mine_only:
        query = query.filter_by(created_by=current_user.id)

    pagination = query.paginate(page=page, per_page=PER_PAGE, error_out=False)
    return render_template("admin/manage_tests.html", pagination=pagination, tests=pagination.items, mine_only=mine_only)


@bp.route("/tests/<int:test_id>/delete", methods=["POST"])
@admin_required
def delete_test(test_id):
    test = Test.query.get_or_404(test_id)
    title = test.title
    db.session.delete(test)
    db.session.commit()
    log_activity("deleted_test", f"Deleted test '{title}'")
    flash("Test deleted.", "success")
    return redirect(url_for("admin.manage_tests"))


@bp.route("/tests/<int:test_id>/toggle", methods=["POST"])
@admin_required
def toggle_test(test_id):
    test = Test.query.get_or_404(test_id)
    test.status = "draft" if test.status == "published" else "published"
    db.session.commit()
    log_activity(
        "published_test" if test.status == "published" else "unpublished_test",
        f"Set test '{test.title}' to {test.status}",
    )
    flash(f"Test status updated to {test.status}.", "success")
    return redirect(url_for("admin.manage_tests"))


@bp.route("/tests/<int:test_id>/duplicate", methods=["POST"])
@admin_required
def duplicate_test(test_id):
    orig = Test.query.get_or_404(test_id)
    new_test = Test(
        test_code=f"{orig.test_code}_COPY_{int(time.time())}",
        title=orig.title,
        description=orig.description,
        duration_minutes=orig.duration_minutes,
        total_questions=orig.total_questions,
        passing_marks=orig.passing_marks,
        status="draft",
        max_attempts=orig.max_attempts,
        randomize_questions=orig.randomize_questions,
        negative_marks_per_wrong=orig.negative_marks_per_wrong,
        allow_review=orig.allow_review,
        created_by=current_user.id,
    )
    db.session.add(new_test)
    db.session.commit()
    log_activity("duplicated_test", f"Duplicated test '{orig.title}' as '{new_test.test_code}'")
    flash(f"Test duplicated as '{new_test.test_code}' (questions not copied).", "success")
    return redirect(url_for("admin.manage_tests"))


@bp.route("/tests/<int:test_id>/questions/add", methods=["GET", "POST"])
@admin_required
def add_question(test_id):
    test = Test.query.get_or_404(test_id)
    form = QuestionForm()
    import_form = QuestionImportForm()
    if form.validate_on_submit():
        q = Question(
            test_id=test.id,
            question_text=form.question_text.data,
            option_a=form.option_a.data,
            option_b=form.option_b.data,
            option_c=form.option_c.data,
            option_d=form.option_d.data,
            correct_answer=form.correct_answer.data,
            marks=form.marks.data,
        )
        db.session.add(q)
        db.session.commit()
        log_activity("added_question", f"Added a question to '{test.title}'")
        flash("Question added.", "success")
        return redirect(url_for("admin.add_question", test_id=test.id))
    return render_template("admin/add_question.html", form=form, import_form=import_form, test=test)


@bp.route("/tests/<int:test_id>/questions/import", methods=["POST"])
@admin_required
def import_questions(test_id):
    test = Test.query.get_or_404(test_id)
    form = QuestionImportForm()
    if not form.validate_on_submit():
        flash("Please choose a valid CSV file.", "error")
        return redirect(url_for("admin.add_question", test_id=test.id))

    file_storage = form.csv_file.data
    try:
        raw = file_storage.read().decode("utf-8-sig")
    except UnicodeDecodeError:
        flash("Could not read the file — please upload a UTF-8 encoded CSV.", "error")
        return redirect(url_for("admin.add_question", test_id=test.id))

    reader = csv.DictReader(io.StringIO(raw))
    required_cols = {"question_text", "option_a", "option_b", "option_c", "option_d", "correct_answer"}
    if not reader.fieldnames or not required_cols.issubset({c.strip().lower() for c in reader.fieldnames}):
        flash(
            "CSV must have columns: question_text, option_a, option_b, option_c, option_d, "
            "correct_answer, marks (marks is optional, defaults to 1).",
            "error",
        )
        return redirect(url_for("admin.add_question", test_id=test.id))

    added, skipped = 0, 0
    for i, row in enumerate(reader, start=2):
        row = {k.strip().lower(): (v.strip() if v else v) for k, v in row.items()}
        correct = (row.get("correct_answer") or "").lower()
        if not row.get("question_text") or correct not in {"a", "b", "c", "d"}:
            skipped += 1
            continue
        try:
            marks = int(row.get("marks") or 1)
        except ValueError:
            marks = 1
        q = Question(
            test_id=test.id,
            question_text=row["question_text"],
            option_a=row.get("option_a", ""),
            option_b=row.get("option_b", ""),
            option_c=row.get("option_c", ""),
            option_d=row.get("option_d", ""),
            correct_answer=correct,
            marks=marks,
        )
        db.session.add(q)
        added += 1

    db.session.commit()
    log_activity("imported_questions", f"Imported {added} question(s) into '{test.title}' ({skipped} skipped)")
    flash(f"Imported {added} question(s).{f' Skipped {skipped} invalid row(s).' if skipped else ''}", "success")
    return redirect(url_for("admin.add_question", test_id=test.id))


@bp.route("/tests/<int:test_id>/questions/<int:question_id>/delete", methods=["POST"])
@admin_required
def delete_question(test_id, question_id):
    q = Question.query.filter_by(id=question_id, test_id=test_id).first_or_404()
    db.session.delete(q)
    db.session.commit()
    log_activity("deleted_question", f"Removed a question from test #{test_id}")
    flash("Question removed.", "success")
    return redirect(url_for("admin.add_question", test_id=test_id))


@bp.route("/tests/<int:test_id>/view")
@admin_required
def view_test(test_id):
    test = Test.query.get_or_404(test_id)
    return render_template("admin/view_test.html", test=test)


@bp.route("/tests/<int:test_id>/assign", methods=["GET", "POST"])
@admin_required
def assign_students(test_id):
    test = Test.query.get_or_404(test_id)
    if request.method == "POST":
        student_ids = request.form.getlist("student_ids")
        extra_time = request.form.get("extra_time_minutes", type=int, default=0) or 0
        notify = request.form.get("notify") == "on"
        added = 0
        for sid in student_ids:
            sid = int(sid)
            if not TestEligibility.query.filter_by(test_id=test.id, student_id=sid).first():
                db.session.add(TestEligibility(test_id=test.id, student_id=sid, extra_time_minutes=extra_time))
                added += 1
                if notify:
                    student = db.session.get(User, sid)
                    if student:
                        send_email(
                            student.email,
                            f"New test assigned: {test.title}",
                            f"Hi {student.name},\n\nYou've been assigned a new test: {test.title}.\n"
                            f"Duration: {test.duration_minutes} minutes.\n\n"
                            f"Log in to Exam Proctoring to take it: {url_for('student.dashboard', _external=True)}",
                        )
        db.session.commit()
        log_activity("assigned_students", f"Assigned {added} student(s) to '{test.title}'")
        flash(f"Assigned {added} student(s) to this test.", "success")
        return redirect(url_for("admin.assign_students", test_id=test.id))

    assigned = {e.student_id: e for e in test.eligibility}
    students = User.query.filter_by(role="student", status="active").order_by(User.name).all()
    return render_template("admin/assign_students.html", test=test, students=students, assigned=assigned)


@bp.route("/tests/<int:test_id>/unassign/<int:student_id>", methods=["POST"])
@admin_required
def unassign_student(test_id, student_id):
    e = TestEligibility.query.filter_by(test_id=test_id, student_id=student_id).first_or_404()
    db.session.delete(e)
    db.session.commit()
    log_activity("unassigned_student", f"Removed a student from test #{test_id}")
    flash("Student removed from test.", "success")
    return redirect(url_for("admin.assign_students", test_id=test_id))


@bp.route("/tests/<int:test_id>/results")
@admin_required
def view_results(test_id):
    test = Test.query.get_or_404(test_id)
    page = request.args.get("page", 1, type=int)
    pagination = Attempt.query.filter_by(test_id=test.id).order_by(Attempt.started_at.desc()).paginate(
        page=page, per_page=PER_PAGE, error_out=False
    )
    return render_template("admin/view_results.html", test=test, pagination=pagination, attempts=pagination.items)


@bp.route("/attempts/<int:attempt_id>")
@admin_required
def view_attempt(attempt_id):
    attempt = Attempt.query.get_or_404(attempt_id)
    events = ProctoringEvent.query.filter_by(attempt_id=attempt.id).order_by(ProctoringEvent.created_at).all()
    return render_template("admin/view_attempt.html", attempt=attempt, events=events)


@bp.route("/activity-log")
@admin_required
def activity_log():
    page = request.args.get("page", 1, type=int)
    pagination = AdminActivityLog.query.order_by(AdminActivityLog.created_at.desc()).paginate(
        page=page, per_page=30, error_out=False
    )
    return render_template("admin/activity_log.html", pagination=pagination, entries=pagination.items)
