import csv
import io
import random
import string
import time
from datetime import datetime

from flask import Blueprint, render_template, redirect, url_for, flash, request, abort, Response
from flask_login import current_user

from app import db
from app.forms import TestForm, QuestionForm, QuestionImportForm, UserImportForm
from app.models import Test, Question, User, TestEligibility, Attempt, ProctoringEvent, AdminActivityLog, gen_user_id
from app.utils import admin_required
from app.activity_log import log_activity
from app.email_utils import send_email

bp = Blueprint("admin", __name__, url_prefix="/admin")

PER_PAGE = 15


def _generate_temp_password():
    """Random password for bulk-imported students who didn't get one in
    the CSV, built to satisfy the same complexity policy as the register
    form (lower + upper + digit + special) so it's usable as-is."""
    return (
        random.choice(string.ascii_lowercase)
        + random.choice(string.ascii_uppercase)
        + random.choice(string.digits)
        + random.choice("!@#$%^&*")
        + "".join(random.choices(string.ascii_letters + string.digits, k=8))
    )


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
    test.partial_credit_multi = form.partial_credit_multi.data


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
        partial_credit_multi=orig.partial_credit_multi,
        created_by=current_user.id,
    )
    db.session.add(new_test)
    db.session.commit()
    log_activity("duplicated_test", f"Duplicated test '{orig.title}' as '{new_test.test_code}'")
    flash(f"Test duplicated as '{new_test.test_code}' (questions not copied).", "success")
    return redirect(url_for("admin.manage_tests"))


def _build_question_from_form(test, form):
    """Validate and construct a Question from QuestionForm data, handling the
    per-type rules that WTForms field validators alone can't express:
    single/multi need options + at least one correct pick, short answer needs
    neither options nor a letter — just the expected text. Returns
    (question_or_none, error_message_or_none).
    """
    qtype = form.question_type.data
    text = form.question_text.data
    marks = form.marks.data
    time_limit = form.time_limit_seconds.data or None

    if qtype in ("single", "multi"):
        options = [form.option_a.data, form.option_b.data, form.option_c.data, form.option_d.data]
        if not all(o and o.strip() for o in options):
            return None, "All four options are required for single/multiple choice questions."

        if qtype == "single":
            picked = request.form.get("correct_radio", "").strip().lower()
            if picked not in {"a", "b", "c", "d"}:
                return None, "Select which option is correct."
            correct_answer = picked
        else:
            picked = sorted({v.strip().lower() for v in request.form.getlist("correct_options") if v.strip()})
            if not picked or not set(picked).issubset({"a", "b", "c", "d"}):
                return None, "Select at least one correct option for a multiple-choice question."
            if len(picked) == 4:
                return None, "At least one option must be marked incorrect."
            correct_answer = ",".join(picked)

        return Question(
            test_id=test.id, question_text=text, question_type=qtype,
            option_a=form.option_a.data, option_b=form.option_b.data,
            option_c=form.option_c.data, option_d=form.option_d.data,
            correct_answer=correct_answer, marks=marks, time_limit_seconds=time_limit,
        ), None

    # short answer
    answer_text = (form.short_answer_text.data or "").strip()
    if not answer_text:
        return None, "Enter the expected correct answer for a short-answer question."
    return Question(
        test_id=test.id, question_text=text, question_type="short",
        option_a=None, option_b=None, option_c=None, option_d=None,
        correct_answer=answer_text, marks=marks, time_limit_seconds=time_limit,
    ), None


@bp.route("/tests/<int:test_id>/questions/add", methods=["GET", "POST"])
@admin_required
def add_question(test_id):
    test = Test.query.get_or_404(test_id)
    form = QuestionForm()
    import_form = QuestionImportForm()
    if form.validate_on_submit():
        question, error = _build_question_from_form(test, form)
        if error:
            flash(error, "error")
        else:
            db.session.add(question)
            db.session.commit()
            log_activity("added_question", f"Added a {question.question_type} question to '{test.title}'")
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
    required_cols = {"question_text", "correct_answer"}
    if not reader.fieldnames or not required_cols.issubset({c.strip().lower() for c in reader.fieldnames}):
        flash(
            "CSV must have columns: question_text, correct_answer, marks (optional), question_type "
            "(optional: single/multi/short, defaults to single), option_a..option_d "
            "(required for single/multi). For multi, correct_answer is letters joined with '+' or ';', "
            "e.g. 'a+c'.",
            "error",
        )
        return redirect(url_for("admin.add_question", test_id=test.id))

    added, skipped = 0, 0
    for i, row in enumerate(reader, start=2):
        row = {k.strip().lower(): (v.strip() if v else v) for k, v in row.items()}
        qtype = (row.get("question_type") or "single").strip().lower()
        if qtype not in {"single", "multi", "short"}:
            skipped += 1
            continue
        if not row.get("question_text") or not row.get("correct_answer"):
            skipped += 1
            continue
        try:
            marks = int(row.get("marks") or 1)
        except ValueError:
            marks = 1
        try:
            time_limit = int(row["time_limit_seconds"]) if row.get("time_limit_seconds") else None
        except ValueError:
            time_limit = None

        if qtype == "short":
            q = Question(
                test_id=test.id, question_text=row["question_text"], question_type="short",
                option_a=None, option_b=None, option_c=None, option_d=None,
                correct_answer=row["correct_answer"].strip(), marks=marks, time_limit_seconds=time_limit,
            )
        else:
            options = [row.get(f"option_{k}") for k in ("a", "b", "c", "d")]
            if not all(options):
                skipped += 1
                continue
            raw_correct = row["correct_answer"].lower().replace(";", "+").replace(",", "+")
            picks = sorted({p.strip() for p in raw_correct.split("+") if p.strip()})
            if not picks or not set(picks).issubset({"a", "b", "c", "d"}):
                skipped += 1
                continue
            if qtype == "single" and len(picks) != 1:
                skipped += 1
                continue
            q = Question(
                test_id=test.id, question_text=row["question_text"], question_type=qtype,
                option_a=options[0], option_b=options[1], option_c=options[2], option_d=options[3],
                correct_answer=",".join(picks), marks=marks, time_limit_seconds=time_limit,
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
        extra_attempts = request.form.get("extra_attempts", type=int, default=0) or 0
        notify = request.form.get("notify") == "on"
        added = 0
        for sid in student_ids:
            sid = int(sid)
            if not TestEligibility.query.filter_by(test_id=test.id, student_id=sid).first():
                db.session.add(TestEligibility(
                    test_id=test.id, student_id=sid,
                    extra_time_minutes=extra_time, extra_attempts=extra_attempts,
                ))
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

    search = request.args.get("q", "").strip()
    page = request.args.get("page", 1, type=int)

    query = User.query.filter_by(role="student", status="active")
    if assigned:
        query = query.filter(~User.id.in_(assigned.keys()))
    if search:
        like = f"%{search}%"
        query = query.filter(db.or_(User.name.ilike(like), User.email.ilike(like)))
    query = query.order_by(User.name)

    pagination = query.paginate(page=page, per_page=PER_PAGE, error_out=False)

    return render_template(
        "admin/assign_students.html", test=test, pagination=pagination,
        students=pagination.items, assigned=assigned, search=search,
    )


@bp.route("/tests/<int:test_id>/unassign/<int:student_id>", methods=["POST"])
@admin_required
def unassign_student(test_id, student_id):
    e = TestEligibility.query.filter_by(test_id=test_id, student_id=student_id).first_or_404()
    db.session.delete(e)
    db.session.commit()
    log_activity("unassigned_student", f"Removed a student from test #{test_id}")
    flash("Student removed from test.", "success")
    return redirect(url_for("admin.assign_students", test_id=test_id))


@bp.route("/tests/<int:test_id>/eligibility/<int:student_id>/update", methods=["POST"])
@admin_required
def update_eligibility(test_id, student_id):
    e = TestEligibility.query.filter_by(test_id=test_id, student_id=student_id).first_or_404()
    e.extra_time_minutes = max(request.form.get("extra_time_minutes", type=int, default=0) or 0, 0)
    e.extra_attempts = max(request.form.get("extra_attempts", type=int, default=0) or 0, 0)
    db.session.commit()
    log_activity(
        "updated_eligibility",
        f"Set extra time to {e.extra_time_minutes} min and extra attempts to {e.extra_attempts} "
        f"for {e.student.name} on '{e.test.title}'",
    )
    flash(f"Updated accommodations for {e.student.name}.", "success")
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


@bp.route("/users")
@admin_required
def manage_users():
    page = request.args.get("page", 1, type=int)
    search = request.args.get("q", "").strip()
    role_filter = request.args.get("role", "")
    status_filter = request.args.get("status", "")

    query = User.query
    if search:
        like = f"%{search}%"
        query = query.filter(
            db.or_(User.name.ilike(like), User.email.ilike(like), User.user_id.ilike(like))
        )
    if role_filter in ("student", "admin"):
        query = query.filter_by(role=role_filter)
    if status_filter in ("active", "inactive"):
        query = query.filter_by(status=status_filter)
    query = query.order_by(User.created_at.desc())

    pagination = query.paginate(page=page, per_page=PER_PAGE, error_out=False)

    return render_template(
        "admin/manage_users.html",
        pagination=pagination, users=pagination.items,
        search=search, role_filter=role_filter, status_filter=status_filter,
        total_users=User.query.count(),
        total_students=User.query.filter_by(role="student").count(),
        total_active=User.query.filter_by(status="active").count(),
        total_inactive=User.query.filter_by(status="inactive").count(),
    )


@bp.route("/users/<int:user_id>/toggle-status", methods=["POST"])
@admin_required
def toggle_user_status(user_id):
    user = User.query.get_or_404(user_id)
    if user.id == current_user.id:
        flash("You can't deactivate your own account.", "error")
        return redirect(url_for("admin.manage_users"))

    user.status = "inactive" if user.status == "active" else "active"
    db.session.commit()
    log_activity(
        "deactivated_user" if user.status == "inactive" else "activated_user",
        f"Set {user.role} '{user.name}' ({user.email}) to {user.status}",
    )
    flash(f"{user.name}'s account is now {user.status}.", "success")
    return redirect(url_for("admin.manage_users"))


@bp.route("/users/<int:user_id>/delete", methods=["POST"])
@admin_required
def delete_user(user_id):
    user = User.query.get_or_404(user_id)
    if user.id == current_user.id:
        flash("You can't delete your own account.", "error")
        return redirect(url_for("admin.manage_users"))

    has_attempts = Attempt.query.filter_by(student_id=user.id).first() is not None
    has_created_tests = Test.query.filter_by(created_by=user.id).first() is not None
    if has_attempts or has_created_tests:
        flash(
            f"Can't delete {user.name} — they have exam history or created content on record. "
            "Deactivate the account instead to preserve it.",
            "error",
        )
        return redirect(url_for("admin.manage_users"))

    TestEligibility.query.filter_by(student_id=user.id).delete()
    name, email = user.name, user.email
    db.session.delete(user)
    db.session.commit()
    log_activity("deleted_user", f"Deleted user '{name}' ({email})")
    flash(f"{name}'s account has been deleted.", "success")
    return redirect(url_for("admin.manage_users"))


@bp.route("/users/export")
@admin_required
def export_users():
    role_filter = request.args.get("role", "")
    status_filter = request.args.get("status", "")

    query = User.query
    if role_filter in ("student", "admin"):
        query = query.filter_by(role=role_filter)
    if status_filter in ("active", "inactive"):
        query = query.filter_by(status=status_filter)
    users = query.order_by(User.name).all()

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["user_id", "name", "email", "phone", "role", "status", "email_verified", "created_at"])
    for u in users:
        writer.writerow([
            u.user_id, u.name, u.email, u.phone or "", u.role, u.status,
            "yes" if u.email_verified else "no",
            u.created_at.strftime("%Y-%m-%d %H:%M") if u.created_at else "",
        ])

    log_activity("exported_users", f"Exported user roster ({len(users)} user(s))")
    response = Response(buf.getvalue(), mimetype="text/csv")
    filename = f"user_roster_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.csv"
    response.headers["Content-Disposition"] = f"attachment; filename={filename}"
    return response


@bp.route("/users/import", methods=["GET", "POST"])
@admin_required
def import_users():
    form = UserImportForm()
    recently_added = []

    if form.validate_on_submit():
        file_storage = form.csv_file.data
        try:
            raw = file_storage.read().decode("utf-8-sig")
        except UnicodeDecodeError:
            flash("Could not read the file — please upload a UTF-8 encoded CSV.", "error")
            return redirect(url_for("admin.import_users"))

        reader = csv.DictReader(io.StringIO(raw))
        required_cols = {"name", "email"}
        if not reader.fieldnames or not required_cols.issubset({c.strip().lower() for c in reader.fieldnames}):
            flash(
                "CSV must have columns: name, email, phone (optional), password (optional — "
                "a random password is generated and emailed to the student if left blank).",
                "error",
            )
            return redirect(url_for("admin.import_users"))

        added, skipped, generated_creds = 0, 0, []
        for row in reader:
            row = {k.strip().lower(): (v.strip() if v else v) for k, v in row.items()}
            name = row.get("name")
            email = (row.get("email") or "").lower()
            if not name or not email:
                skipped += 1
                continue
            if User.query.filter_by(email=email).first():
                skipped += 1
                continue

            phone = row.get("phone") or "0000000000"
            password = row.get("password") or _generate_temp_password()

            user = User(
                user_id=gen_user_id("student"), name=name, email=email, phone=phone,
                role="student", status="active", email_verified=True,
            )
            user.set_password(password)
            db.session.add(user)
            db.session.flush()
            recently_added.append(user)
            if not row.get("password"):
                generated_creds.append((user, password))
            added += 1

        db.session.commit()

        for user, password in generated_creds:
            send_email(
                user.email,
                "Your Exam Proctoring account",
                f"Hi {user.name},\n\nAn account has been created for you.\n"
                f"Email: {user.email}\nTemporary password: {password}\n\n"
                f"Please log in and change your password after your first login.",
            )

        log_activity("imported_users", f"Bulk-imported {added} student(s) ({skipped} skipped)")
        flash(
            f"Imported {added} student(s)."
            + (f" Skipped {skipped} row(s) (missing data or already registered)." if skipped else ""),
            "success",
        )
        return render_template("admin/import_users.html", form=UserImportForm(), recently_added=recently_added)

    return render_template("admin/import_users.html", form=form, recently_added=recently_added)
