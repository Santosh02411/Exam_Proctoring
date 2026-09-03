import csv
import io
import json
import os
import random
import string
import time
import uuid
from datetime import datetime

from flask import (
    Blueprint, render_template, redirect, url_for, flash, request, abort, Response, current_app,
    stream_with_context,
)
from flask_login import current_user
from werkzeug.utils import secure_filename

from app import db
from app.forms import (
    TestForm, QuestionForm, QuestionImportForm, UserImportForm, QuestionBankForm, SectionForm,
    RetentionPolicyForm, BrandingForm, ApiKeyForm, LmsWebhookForm, CertificateSettingsForm, ProctoringPolicyForm,
)
from app.models import (
    Test, Question, User, TestEligibility, Attempt, Answer, ProctoringEvent, AdminActivityLog,
    QuestionBankItem, Section, IdentityDocument, NotificationLog, AnswerSimilarityFlag,
    LoginSession, LoginSecurityEvent, ApiKey, gen_user_id, recompute_attempt_score,
)
from app.proctoring import compute_suspicion_score, build_timeline, get_live_alerts_since, EVENT_TYPE_LABELS
from app.utils import (
    admin_required, roles_required, roles_required_or_impersonating,
    org_scope, ensure_same_org, is_super_admin, current_org_id, is_impersonating,
)
from app.activity_log import log_activity
from app.email_utils import send_email
from app.notifications import (
    notify_exam_scheduled, notify_result_published_if_now_complete, send_starting_soon_reminders,
)
from app import analytics
from app import proctoring
from app import similarity as similarity_module
from app import retention as retention_module
from app import org_export
from app import org_reports
from app import branding as branding_module

bp = Blueprint("admin", __name__, url_prefix="/admin")

PER_PAGE = 15

# Test/question authoring and grading is shared between admins and examiners.
# Proctoring review (results + individual attempt/violation review) additionally
# opens up to the proctor role, whose whole job is that review queue.
content_access = roles_required_or_impersonating("admin", "examiner")
review_access = roles_required_or_impersonating("admin", "examiner", "proctor")

_IMAGE_EXTS = {"png", "jpg", "jpeg", "gif", "webp"}
_VIDEO_EXTS = {"mp4", "webm", "ogg"}


def _save_question_media(form, existing=None):
    """Resolve the media_type/media_url for a question from the submitted
    QuestionForm: an uploaded file wins if present, then an external URL,
    then (on edit) whatever media the existing Question/QuestionBankItem
    already had — so editing a question without touching the media fields
    doesn't silently wipe its attached image/video. Returns
    (media_type, media_url), both possibly None. Uploaded files are saved
    under app/static/uploads/questions/ with a random filename (avoids
    collisions and doesn't trust the original name for anything but its
    extension)."""
    file_storage = form.media_file.data
    if file_storage and file_storage.filename:
        ext = file_storage.filename.rsplit(".", 1)[-1].lower()
        media_type = "image" if ext in _IMAGE_EXTS else "video" if ext in _VIDEO_EXTS else None
        if not media_type:
            return None, None
        upload_dir = os.path.join(current_app.root_path, "static", "uploads", "questions")
        os.makedirs(upload_dir, exist_ok=True)
        filename = f"{uuid.uuid4().hex}.{ext}"
        file_storage.save(os.path.join(upload_dir, secure_filename(filename)))
        return media_type, f"/static/uploads/questions/{filename}"

    url = (form.media_url.data or "").strip()
    media_type = form.media_type.data or None
    if url and media_type:
        return media_type, url
    if existing is not None:
        return existing.media_type, existing.media_url
    return None, None


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
    test.instructions = form.instructions.data
    test.duration_minutes = form.duration_minutes.data
    test.total_questions = form.total_questions.data
    test.passing_marks = form.passing_marks.data
    test.status = form.status.data
    test.start_time = form.start_time.data
    test.end_time = form.end_time.data
    test.max_attempts = form.max_attempts.data
    test.randomize_questions = form.randomize_questions.data
    test.randomize_options = form.randomize_options.data
    test.negative_marks_per_wrong = form.negative_marks_per_wrong.data
    test.allow_review = form.allow_review.data
    test.partial_credit_multi = form.partial_credit_multi.data
    test.question_pool_size = form.question_pool_size.data or None
    test.certificate_enabled = form.certificate_enabled.data


@bp.route("/dashboard")
@content_access
def dashboard():
    """"My tests" for a normal admin/examiner (created_by == them) — but a
    super_admin viewing this while impersonating never created anything
    themselves, so for them this shows every test in the org they're
    impersonating instead, which is the useful "what does this org have"
    view a support visit actually wants."""
    if is_impersonating():
        tests = org_scope(Test.query, Test).order_by(Test.created_at.desc()).all()
        total_attempts = Attempt.query.join(Test).filter(Test.org_id == current_org_id()).count()
    else:
        tests = Test.query.filter_by(created_by=current_user.id).order_by(Test.created_at.desc()).all()
        total_attempts = Attempt.query.join(Test).filter(Test.created_by == current_user.id).count()
    total_students = User.query.filter_by(role="student", org_id=current_org_id()).count()
    return render_template(
        "admin/dashboard.html", tests=tests, total_students=total_students, total_attempts=total_attempts
    )


@bp.route("/tests/create", methods=["GET", "POST"])
@content_access
def create_test():
    form = TestForm()
    if request.method == "GET":
        form.max_attempts.data = 1
        form.randomize_questions.data = True
        form.randomize_options.data = True
        form.allow_review.data = True
    if form.validate_on_submit():
        if Test.query.filter_by(test_code=form.test_code.data.strip()).first():
            flash("Test code already exists — choose a unique one.", "error")
        else:
            test = Test(created_by=current_user.id, org_id=current_user.org_id)
            _apply_test_form(test, form)
            db.session.add(test)
            db.session.commit()
            log_activity("created_test", f"Created test '{test.title}' ({test.test_code})")
            flash("Test created. Now add some questions.", "success")
            return redirect(url_for("admin.add_question", test_id=test.id))
    return render_template("admin/create_test.html", form=form)


@bp.route("/tests/<int:test_id>/edit", methods=["GET", "POST"])
@content_access
def edit_test(test_id):
    test = Test.query.get_or_404(test_id)
    ensure_same_org(test)
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
@content_access
def manage_tests():
    page = request.args.get("page", 1, type=int)
    mine_only = request.args.get("mine") == "1"

    query = org_scope(Test.query, Test).order_by(Test.created_at.desc())
    if mine_only:
        query = query.filter_by(created_by=current_user.id)

    pagination = query.paginate(page=page, per_page=PER_PAGE, error_out=False)
    return render_template("admin/manage_tests.html", pagination=pagination, tests=pagination.items, mine_only=mine_only)


@bp.route("/tests/<int:test_id>/delete", methods=["POST"])
@content_access
def delete_test(test_id):
    test = Test.query.get_or_404(test_id)
    ensure_same_org(test)
    title = test.title
    db.session.delete(test)
    db.session.commit()
    log_activity("deleted_test", f"Deleted test '{title}'")
    flash("Test deleted.", "success")
    return redirect(url_for("admin.manage_tests"))


@bp.route("/tests/<int:test_id>/toggle", methods=["POST"])
@content_access
def toggle_test(test_id):
    test = Test.query.get_or_404(test_id)
    ensure_same_org(test)
    test.status = "draft" if test.status == "published" else "published"
    db.session.commit()
    log_activity(
        "published_test" if test.status == "published" else "unpublished_test",
        f"Set test '{test.title}' to {test.status}",
    )
    flash(f"Test status updated to {test.status}.", "success")
    return redirect(url_for("admin.manage_tests"))


@bp.route("/tests/<int:test_id>/duplicate", methods=["POST"])
@content_access
def duplicate_test(test_id):
    orig = Test.query.get_or_404(test_id)
    ensure_same_org(orig)
    new_test = Test(
        test_code=f"{orig.test_code}_COPY_{int(time.time())}",
        title=orig.title,
        description=orig.description,
        instructions=orig.instructions,
        duration_minutes=orig.duration_minutes,
        total_questions=orig.total_questions,
        passing_marks=orig.passing_marks,
        status="draft",
        max_attempts=orig.max_attempts,
        randomize_questions=orig.randomize_questions,
        randomize_options=orig.randomize_options,
        negative_marks_per_wrong=orig.negative_marks_per_wrong,
        allow_review=orig.allow_review,
        partial_credit_multi=orig.partial_credit_multi,
        question_pool_size=orig.question_pool_size,
        created_by=current_user.id,
        org_id=orig.org_id,
    )
    db.session.add(new_test)
    db.session.commit()
    log_activity("duplicated_test", f"Duplicated test '{orig.title}' as '{new_test.test_code}'")
    flash(f"Test duplicated as '{new_test.test_code}' (questions not copied).", "success")
    return redirect(url_for("admin.manage_tests"))


def _parse_question_fields(form, existing=None):
    """Validate and extract the fields common to both a test Question and a
    QuestionBankItem from a submitted QuestionForm/QuestionBankForm,
    handling the per-type rules WTForms field validators alone can't
    express. `existing` (an already-persisted Question/QuestionBankItem) is
    passed when editing, so media fields fall back to what's already there
    if the edit didn't touch them. Returns
    (fields_dict_or_none, error_message_or_none)."""
    qtype = form.question_type.data
    text = form.question_text.data
    marks = form.marks.data
    time_limit = form.time_limit_seconds.data or None
    category = (form.category.data or "").strip() or None
    difficulty = form.difficulty.data or "medium"
    media_type, media_url = _save_question_media(form, existing=existing)
    common = {
        "question_text": text, "marks": marks, "time_limit_seconds": time_limit,
        "category": category, "difficulty": difficulty,
        "media_type": media_type, "media_url": media_url,
    }

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

        return {
            **common, "question_type": qtype,
            "option_a": form.option_a.data, "option_b": form.option_b.data,
            "option_c": form.option_c.data, "option_d": form.option_d.data,
            "correct_answer": correct_answer,
        }, None

    if qtype == "true_false":
        picked = request.form.get("tf_correct", "").strip().lower()
        if picked not in {"true", "false"}:
            return None, "Select True or False."
        return {
            **common, "question_type": "true_false",
            "option_a": None, "option_b": None, "option_c": None, "option_d": None,
            "correct_answer": picked,
        }, None

    if qtype == "fill_blank":
        if "___" not in text:
            return None, "Fill-in-the-blank questions need a blank in the question text — use three or more underscores, e.g. 'The capital of France is ____.'"
        answer_text = (form.blank_answer.data or "").strip()
        if not answer_text:
            return None, "Enter at least one accepted answer for the blank (separate alternatives with ';')."
        return {
            **common, "question_type": "fill_blank",
            "option_a": None, "option_b": None, "option_c": None, "option_d": None,
            "correct_answer": answer_text,
        }, None

    if qtype == "descriptive":
        return {
            **common, "question_type": "descriptive",
            "option_a": None, "option_b": None, "option_c": None, "option_d": None,
            "correct_answer": (form.model_answer.data or "").strip(),
        }, None

    if qtype == "coding":
        return {
            **common, "question_type": "coding",
            "option_a": None, "option_b": None, "option_c": None, "option_d": None,
            "correct_answer": (form.model_answer.data or "").strip(),
            "starter_code": form.starter_code.data or None,
            "code_language": (form.code_language.data or "").strip() or None,
        }, None

    # short answer
    answer_text = (form.short_answer_text.data or "").strip()
    if not answer_text:
        return None, "Enter the expected correct answer for a short-answer question."
    return {
        **common, "question_type": "short",
        "option_a": None, "option_b": None, "option_c": None, "option_d": None,
        "correct_answer": answer_text,
    }, None


def _build_question_from_form(test, form):
    fields, error = _parse_question_fields(form)
    if error:
        return None, error
    section_id = request.form.get("section_id", type=int)
    if section_id and not Section.query.filter_by(id=section_id, test_id=test.id).first():
        section_id = None
    return Question(test_id=test.id, section_id=section_id, **fields), None


@bp.route("/tests/<int:test_id>/questions/add", methods=["GET", "POST"])
@content_access
def add_question(test_id):
    test = Test.query.get_or_404(test_id)
    ensure_same_org(test)
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
    return render_template(
        "admin/add_question.html", form=form, import_form=import_form, test=test,
        sections=Section.query.filter_by(test_id=test.id).order_by(Section.order_index).all(),
    )


@bp.route("/tests/<int:test_id>/questions/import", methods=["POST"])
@content_access
def import_questions(test_id):
    test = Test.query.get_or_404(test_id)
    ensure_same_org(test)
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
            "(optional: single/multi/short/true_false/fill_blank, defaults to single), option_a..option_d "
            "(required for single/multi), category (optional), difficulty (optional: easy/medium/hard). "
            "For multi, correct_answer is letters joined with '+' or ';', e.g. 'a+c'. For true_false, "
            "correct_answer is 'true' or 'false'. For fill_blank, correct_answer is one or more accepted "
            "answers separated by ';'. descriptive/coding questions (manually graded) aren't supported via "
            "CSV import — add those individually.",
            "error",
        )
        return redirect(url_for("admin.add_question", test_id=test.id))

    added, skipped = 0, 0
    for i, row in enumerate(reader, start=2):
        row = {k.strip().lower(): (v.strip() if v else v) for k, v in row.items()}
        qtype = (row.get("question_type") or "single").strip().lower()
        if qtype not in {"single", "multi", "short", "true_false", "fill_blank"}:
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
        category = row.get("category") or None
        difficulty = (row.get("difficulty") or "medium").strip().lower()
        if difficulty not in {"easy", "medium", "hard"}:
            difficulty = "medium"

        if qtype == "short":
            q = Question(
                test_id=test.id, question_text=row["question_text"], question_type="short",
                option_a=None, option_b=None, option_c=None, option_d=None,
                correct_answer=row["correct_answer"].strip(), marks=marks, time_limit_seconds=time_limit,
                category=category, difficulty=difficulty,
            )
        elif qtype == "true_false":
            tf = row["correct_answer"].strip().lower()
            if tf not in {"true", "false"}:
                skipped += 1
                continue
            q = Question(
                test_id=test.id, question_text=row["question_text"], question_type="true_false",
                option_a=None, option_b=None, option_c=None, option_d=None,
                correct_answer=tf, marks=marks, time_limit_seconds=time_limit,
                category=category, difficulty=difficulty,
            )
        elif qtype == "fill_blank":
            if "___" not in row["question_text"]:
                skipped += 1
                continue
            q = Question(
                test_id=test.id, question_text=row["question_text"], question_type="fill_blank",
                option_a=None, option_b=None, option_c=None, option_d=None,
                correct_answer=row["correct_answer"].strip(), marks=marks, time_limit_seconds=time_limit,
                category=category, difficulty=difficulty,
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
                category=category, difficulty=difficulty,
            )
        db.session.add(q)
        added += 1

    db.session.commit()
    log_activity("imported_questions", f"Imported {added} question(s) into '{test.title}' ({skipped} skipped)")
    flash(f"Imported {added} question(s).{f' Skipped {skipped} invalid row(s).' if skipped else ''}", "success")
    return redirect(url_for("admin.add_question", test_id=test.id))


@bp.route("/tests/<int:test_id>/questions/<int:question_id>/delete", methods=["POST"])
@content_access
def delete_question(test_id, question_id):
    test = Test.query.get_or_404(test_id)
    ensure_same_org(test)
    q = Question.query.filter_by(id=question_id, test_id=test_id).first_or_404()
    db.session.delete(q)
    db.session.commit()
    log_activity("deleted_question", f"Removed a question from test #{test_id}")
    flash("Question removed.", "success")
    return redirect(url_for("admin.add_question", test_id=test_id))


@bp.route("/tests/<int:test_id>/questions/<int:question_id>/save-to-bank", methods=["POST"])
@content_access
def save_question_to_bank(test_id, question_id):
    test = Test.query.get_or_404(test_id)
    ensure_same_org(test)
    q = Question.query.filter_by(id=question_id, test_id=test_id).first_or_404()
    category = (request.form.get("category") or "").strip() or None
    item = QuestionBankItem(
        created_by=current_user.id, org_id=test.org_id, category=category, difficulty=q.difficulty,
        question_text=q.question_text, question_type=q.question_type,
        option_a=q.option_a, option_b=q.option_b, option_c=q.option_c, option_d=q.option_d,
        correct_answer=q.correct_answer, marks=q.marks, time_limit_seconds=q.time_limit_seconds,
        media_type=q.media_type, media_url=q.media_url,
        starter_code=q.starter_code, code_language=q.code_language,
    )
    db.session.add(item)
    db.session.commit()
    log_activity("saved_question_to_bank", f"Saved a question from '{q.test.title}' to the question bank")
    flash("Saved to the question bank.", "success")
    return redirect(url_for("admin.add_question", test_id=test_id))


@bp.route("/tests/<int:test_id>/questions/from-bank", methods=["GET", "POST"])
@content_access
def pick_from_bank(test_id):
    test = Test.query.get_or_404(test_id)
    ensure_same_org(test)

    if request.method == "POST":
        item_ids = [int(i) for i in request.form.getlist("item_ids")]
        added = 0
        for item in org_scope(QuestionBankItem.query, QuestionBankItem).filter(QuestionBankItem.id.in_(item_ids)).all():
            db.session.add(Question(
                test_id=test.id, bank_item_id=item.id, category=item.category, difficulty=item.difficulty,
                question_text=item.question_text, question_type=item.question_type,
                option_a=item.option_a, option_b=item.option_b, option_c=item.option_c, option_d=item.option_d,
                correct_answer=item.correct_answer, marks=item.marks, time_limit_seconds=item.time_limit_seconds,
                media_type=item.media_type, media_url=item.media_url,
                starter_code=item.starter_code, code_language=item.code_language,
            ))
            added += 1
        db.session.commit()
        log_activity("added_questions_from_bank", f"Added {added} question(s) from the bank to '{test.title}'")
        flash(f"Added {added} question(s) from the bank.", "success")
        return redirect(url_for("admin.add_question", test_id=test.id))

    search = request.args.get("q", "").strip()
    category = request.args.get("category", "").strip()
    page = request.args.get("page", 1, type=int)

    query = org_scope(QuestionBankItem.query, QuestionBankItem)
    if search:
        query = query.filter(QuestionBankItem.question_text.ilike(f"%{search}%"))
    if category:
        query = query.filter_by(category=category)
    query = query.order_by(QuestionBankItem.created_at.desc())

    pagination = query.paginate(page=page, per_page=PER_PAGE, error_out=False)
    categories = [c[0] for c in org_scope(db.session.query(QuestionBankItem.category), QuestionBankItem).filter(
        QuestionBankItem.category.isnot(None)
    ).distinct().order_by(QuestionBankItem.category).all()]
    already_added = {q.bank_item_id for q in test.questions if q.bank_item_id}

    return render_template(
        "admin/pick_from_bank.html", test=test, pagination=pagination, items=pagination.items,
        search=search, category=category, categories=categories, already_added=already_added,
    )


@bp.route("/tests/<int:test_id>/view")
@content_access
def view_test(test_id):
    test = Test.query.get_or_404(test_id)
    ensure_same_org(test)
    return render_template("admin/view_test.html", test=test)


@bp.route("/tests/<int:test_id>/proctoring-policy", methods=["GET", "POST"])
@content_access
def proctoring_policy(test_id):
    """Configurable Proctoring Policies: per-event-type action overrides
    for this test (see app.proctoring.get_policy/_record_violation). GET
    shows the editor; POST parses one select per configurable event type
    from the raw form data (see ProctoringPolicyForm's docstring for why
    these aren't bound WTForms fields) and stores only the entries that
    actually differ from "use the default", so the stored JSON stays a
    lean set of overrides rather than a full copy of every event type."""
    test = Test.query.get_or_404(test_id)
    ensure_same_org(test)
    form = ProctoringPolicyForm()
    current_policy = proctoring.get_policy(test)

    if form.validate_on_submit():
        new_policy = {}
        for event_type in proctoring.POLICY_CONFIGURABLE_EVENT_TYPES:
            action = request.form.get(f"policy_{event_type}", "default")
            if action not in proctoring.POLICY_ACTIONS:
                action = "default"

            entry = {}
            if action != "default":
                entry["action"] = action

            if action == "warning":
                limit_raw = request.form.get(f"warning_limit_{event_type}", "").strip()
                if limit_raw:
                    try:
                        limit = int(limit_raw)
                        if limit > 0:
                            entry["warning_limit"] = limit
                    except ValueError:
                        pass
                escalate = request.form.get(f"escalate_{event_type}", "")
                if escalate in proctoring.ESCALATE_ACTIONS and entry.get("warning_limit"):
                    entry["escalate_action"] = escalate

            message = request.form.get(f"message_{event_type}", "").strip()
            if message:
                entry["message"] = message[:300]

            grace_raw = request.form.get(f"grace_{event_type}", "").strip()
            if grace_raw:
                try:
                    grace = int(grace_raw)
                    if grace > 0:
                        entry["grace_period_seconds"] = grace
                except ValueError:
                    pass

            if entry:
                new_policy[event_type] = entry

        test.proctoring_policy = json.dumps(new_policy) if new_policy else None
        db.session.commit()
        log_activity("updated_proctoring_policy", f"Updated proctoring policy for '{test.title}'")
        flash("Proctoring policy updated.", "success")
        return redirect(url_for("admin.proctoring_policy", test_id=test.id))

    rows = [
        {
            "event_type": et,
            "label": proctoring.EVENT_TYPE_LABELS.get(et, et.replace("_", " ")),
            "weight": proctoring.EVENT_WEIGHTS.get(et, proctoring.DEFAULT_EVENT_WEIGHT),
            "current": current_policy.get(et, proctoring._DEFAULT_POLICY_ENTRY)["action"],
            "warning_limit": current_policy.get(et, proctoring._DEFAULT_POLICY_ENTRY)["warning_limit"] or "",
            "escalate_action": current_policy.get(et, proctoring._DEFAULT_POLICY_ENTRY)["escalate_action"] or "flag",
            "message": current_policy.get(et, proctoring._DEFAULT_POLICY_ENTRY)["message"] or "",
            "grace_period_seconds": current_policy.get(et, proctoring._DEFAULT_POLICY_ENTRY)["grace_period_seconds"] or "",
        }
        for et in proctoring.POLICY_CONFIGURABLE_EVENT_TYPES
    ]
    rows.sort(key=lambda r: r["label"])
    return render_template(
        "admin/proctoring_policy.html", test=test, form=form, rows=rows,
        escalate_actions=proctoring.ESCALATE_ACTIONS,
    )


@bp.route("/tests/<int:test_id>/sections", methods=["GET", "POST"])
@content_access
def manage_sections(test_id):
    test = Test.query.get_or_404(test_id)
    ensure_same_org(test)
    form = SectionForm()
    if form.validate_on_submit():
        next_order = (
            db.session.query(db.func.max(Section.order_index)).filter_by(test_id=test.id).scalar() or 0
        ) + 1
        section = Section(
            test_id=test.id, name=form.name.data.strip(), description=form.description.data,
            duration_minutes=form.duration_minutes.data, order_index=next_order,
        )
        db.session.add(section)
        db.session.commit()
        log_activity("added_section", f"Added section '{section.name}' to '{test.title}'")
        flash("Section added.", "success")
        return redirect(url_for("admin.manage_sections", test_id=test.id))

    sections = Section.query.filter_by(test_id=test.id).order_by(Section.order_index).all()
    return render_template("admin/manage_sections.html", test=test, form=form, sections=sections)


@bp.route("/tests/<int:test_id>/sections/<int:section_id>/edit", methods=["POST"])
@content_access
def edit_section(test_id, section_id):
    test = Test.query.get_or_404(test_id)
    ensure_same_org(test)
    section = Section.query.filter_by(id=section_id, test_id=test_id).first_or_404()
    name = (request.form.get("name") or "").strip()
    if not name:
        flash("Section name is required.", "error")
        return redirect(url_for("admin.manage_sections", test_id=test_id))
    section.name = name
    section.description = request.form.get("description") or None
    duration = request.form.get("duration_minutes", type=int)
    section.duration_minutes = duration if duration and duration > 0 else None
    db.session.commit()
    log_activity("edited_section", f"Edited section '{section.name}' on '{section.test.title}'")
    flash("Section updated.", "success")
    return redirect(url_for("admin.manage_sections", test_id=test_id))


@bp.route("/tests/<int:test_id>/sections/<int:section_id>/delete", methods=["POST"])
@content_access
def delete_section(test_id, section_id):
    test = Test.query.get_or_404(test_id)
    ensure_same_org(test)
    section = Section.query.filter_by(id=section_id, test_id=test_id).first_or_404()
    # Questions in this section aren't deleted — they just fall back to being
    # unsectioned, same as any question that was never assigned a section.
    Question.query.filter_by(section_id=section.id).update({"section_id": None})
    name = section.name
    db.session.delete(section)
    db.session.commit()
    log_activity("deleted_section", f"Deleted section '{name}' from test #{test_id}")
    flash("Section removed. Its questions are kept, now unsectioned.", "success")
    return redirect(url_for("admin.manage_sections", test_id=test_id))


@bp.route("/tests/<int:test_id>/sections/reorder", methods=["POST"])
@content_access
def reorder_sections(test_id):
    test = Test.query.get_or_404(test_id)
    ensure_same_org(test)
    order = request.form.getlist("section_id")
    for index, sid in enumerate(order):
        Section.query.filter_by(id=int(sid), test_id=test_id).update({"order_index": index})
    db.session.commit()
    flash("Section order updated.", "success")
    return redirect(url_for("admin.manage_sections", test_id=test_id))


@bp.route("/tests/<int:test_id>/assign", methods=["GET", "POST"])
@content_access
def assign_students(test_id):
    test = Test.query.get_or_404(test_id)
    ensure_same_org(test)
    if request.method == "POST":
        student_ids = request.form.getlist("student_ids")
        extra_time = request.form.get("extra_time_minutes", type=int, default=0) or 0
        extra_attempts = request.form.get("extra_attempts", type=int, default=0) or 0
        should_notify = request.form.get("notify") == "on"
        added = 0
        for sid in student_ids:
            sid = int(sid)
            # Only ever assign students from this test's own organization —
            # student_ids comes from a form the browser could tamper with,
            # so this can't just trust the posted list.
            student = User.query.filter_by(id=sid, role="student", org_id=test.org_id).first()
            if not student:
                continue
            if not TestEligibility.query.filter_by(test_id=test.id, student_id=sid).first():
                db.session.add(TestEligibility(
                    test_id=test.id, student_id=sid,
                    extra_time_minutes=extra_time, extra_attempts=extra_attempts,
                ))
                added += 1
                if should_notify:
                    notify_exam_scheduled(student, test)
        db.session.commit()
        log_activity("assigned_students", f"Assigned {added} student(s) to '{test.title}'")
        flash(f"Assigned {added} student(s) to this test.", "success")
        return redirect(url_for("admin.assign_students", test_id=test.id))

    assigned = {e.student_id: e for e in test.eligibility}

    search = request.args.get("q", "").strip()
    page = request.args.get("page", 1, type=int)

    query = User.query.filter_by(role="student", status="active", org_id=test.org_id)
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
@content_access
def unassign_student(test_id, student_id):
    test = Test.query.get_or_404(test_id)
    ensure_same_org(test)
    e = TestEligibility.query.filter_by(test_id=test_id, student_id=student_id).first_or_404()
    db.session.delete(e)
    db.session.commit()
    log_activity("unassigned_student", f"Removed a student from test #{test_id}")
    flash("Student removed from test.", "success")
    return redirect(url_for("admin.assign_students", test_id=test_id))


@bp.route("/tests/<int:test_id>/eligibility/<int:student_id>/update", methods=["POST"])
@content_access
def update_eligibility(test_id, student_id):
    test = Test.query.get_or_404(test_id)
    ensure_same_org(test)
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
@review_access
def view_results(test_id):
    test = Test.query.get_or_404(test_id)
    ensure_same_org(test)
    page = request.args.get("page", 1, type=int)
    pagination = Attempt.query.filter_by(test_id=test.id).order_by(Attempt.started_at.desc()).paginate(
        page=page, per_page=PER_PAGE, error_out=False
    )

    all_attempts = Attempt.query.filter_by(test_id=test.id).all()
    scored = [a for a in all_attempts if a.score is not None and a.status != "terminated"]
    submitted_count = len([a for a in all_attempts if a.status != "in_progress"])
    passed_count = len([a for a in scored if a.score >= test.passing_marks])
    stats = {
        "total_attempts": len(all_attempts),
        "submitted_count": submitted_count,
        "avg_score": round(sum(a.score for a in scored) / len(scored), 2) if scored else None,
        "pass_rate": round(100 * passed_count / len(scored), 1) if scored else None,
        "avg_violations": round(sum(a.violation_count for a in all_attempts) / len(all_attempts), 1) if all_attempts else None,
        "pending_grading_count": len({
            a.id for a in all_attempts for ans in a.answers
            if ans.question.needs_manual_grading and ans.selected_option and ans.manual_score is None
        }),
    }
    return render_template(
        "admin/view_results.html", test=test, pagination=pagination, attempts=pagination.items, stats=stats
    )


@bp.route("/tests/<int:test_id>/results/export")
@review_access
def export_results(test_id):
    test = Test.query.get_or_404(test_id)
    ensure_same_org(test)
    attempts = Attempt.query.filter_by(test_id=test.id).order_by(Attempt.started_at.desc()).all()

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([
        "student_name", "student_email", "status", "score", "total_marks", "result",
        "violation_count", "started_at", "submitted_at",
    ])
    for a in attempts:
        result = ""
        if a.score is not None and a.status != "terminated":
            result = "pass" if a.score >= test.passing_marks else "fail"
        writer.writerow([
            a.student.name, a.student.email, a.status, a.score if a.score is not None else "",
            test.total_marks(), result, a.violation_count,
            a.started_at.strftime("%Y-%m-%d %H:%M") if a.started_at else "",
            a.submitted_at.strftime("%Y-%m-%d %H:%M") if a.submitted_at else "",
        ])

    log_activity("exported_results", f"Exported results for '{test.title}' ({len(attempts)} attempt(s))")
    response = Response(buf.getvalue(), mimetype="text/csv")
    filename = f"results_{test.test_code}_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.csv"
    response.headers["Content-Disposition"] = f"attachment; filename={filename}"
    return response


@bp.route("/attempts/<int:attempt_id>")
@review_access
def view_attempt(attempt_id):
    attempt = Attempt.query.get_or_404(attempt_id)
    ensure_same_org(attempt.test)
    events = ProctoringEvent.query.filter_by(attempt_id=attempt.id).order_by(ProctoringEvent.created_at).all()
    risk = compute_suspicion_score(attempt)
    quality = proctoring.compute_quality_score(attempt)
    timeline = build_timeline(attempt)
    patterns = proctoring.detect_behavioral_patterns(attempt)
    return render_template(
        "admin/view_attempt.html", attempt=attempt, events=events, risk=risk, timeline=timeline,
        patterns=patterns, quality=quality,
    )


@bp.route("/attempts/<int:attempt_id>/grade", methods=["POST"])
@content_access
def grade_attempt(attempt_id):
    """Manually score descriptive/coding answers on one attempt (see
    Question.needs_manual_grading) — everything else is already scored at
    submission time. Recomputes the attempt's total from scratch afterward,
    so this is safe to call again later to fix a grade."""
    attempt = Attempt.query.get_or_404(attempt_id)
    ensure_same_org(attempt.test)
    answers = Answer.query.filter_by(attempt_id=attempt.id).join(Question).filter(
        Question.question_type.in_(("descriptive", "coding"))
    ).all()

    was_pending = any(a.selected_option and a.manual_score is None for a in answers)

    graded_count = 0
    for answer in answers:
        field = f"score_{answer.id}"
        if field not in request.form:
            continue
        raw = request.form.get(field, "").strip()
        if raw == "":
            continue
        try:
            value = float(raw)
        except ValueError:
            flash(f"Invalid score for one of the answers — must be a number.", "error")
            continue
        value = max(0.0, min(value, float(answer.question.marks)))
        answer.manual_score = value
        answer.graded_at = datetime.utcnow()
        answer.graded_by_id = current_user.id
        graded_count += 1

    db.session.flush()
    attempt.score = recompute_attempt_score(attempt)
    db.session.commit()
    notify_result_published_if_now_complete(attempt, was_pending_before=was_pending)
    log_activity("graded_attempt", f"Graded {graded_count} manual answer(s) on attempt #{attempt.id}")
    flash("Grades saved.", "success")
    return redirect(url_for("admin.view_attempt", attempt_id=attempt.id))


@bp.route("/tests/<int:test_id>/plagiarism")
@review_access
def plagiarism_queue(test_id):
    """Examiner-facing review queue for a test's flagged descriptive/coding
    answer pairs (see app.similarity). Doesn't run the check itself —
    that's a separate POST — this just lists whatever's already flagged,
    most-similar first, with pending flags surfaced before ones already
    reviewed."""
    test = Test.query.get_or_404(test_id)
    ensure_same_org(test)
    flags = AnswerSimilarityFlag.query.filter_by(test_id=test.id).order_by(
        (AnswerSimilarityFlag.review_status == "pending").desc(),
        AnswerSimilarityFlag.similarity_pct.desc(),
    ).all()
    threshold = current_app.config.get("SIMILARITY_THRESHOLD_DEFAULT", 70)
    return render_template("admin/plagiarism_queue.html", test=test, flags=flags, threshold=threshold)


@bp.route("/tests/<int:test_id>/plagiarism/run", methods=["POST"])
@review_access
def run_plagiarism_check(test_id):
    test = Test.query.get_or_404(test_id)
    ensure_same_org(test)
    try:
        threshold = float(request.form.get("threshold", current_app.config.get("SIMILARITY_THRESHOLD_DEFAULT", 70)))
    except ValueError:
        threshold = current_app.config.get("SIMILARITY_THRESHOLD_DEFAULT", 70)
    threshold = max(0.0, min(threshold, 100.0))

    new_flags = similarity_module.run_similarity_check(test, threshold_pct=threshold)
    log_activity(
        "ran_plagiarism_check",
        f"Ran similarity check on '{test.title}' at {threshold}% threshold — {new_flags} new flag(s)",
    )
    if new_flags:
        flash(f"Found {new_flags} new similar answer pair(s) at {threshold}%+ similarity.", "warning")
    else:
        flash(f"No new similar answer pairs found at {threshold}%+ similarity.", "success")
    return redirect(url_for("admin.plagiarism_queue", test_id=test.id))


@bp.route("/plagiarism/<int:flag_id>/review", methods=["POST"])
@review_access
def review_plagiarism_flag(flag_id):
    flag = AnswerSimilarityFlag.query.get_or_404(flag_id)
    ensure_same_org(flag.test)
    decision = request.form.get("decision")
    if decision not in ("confirmed", "dismissed"):
        abort(400)
    flag.review_status = decision
    flag.reviewed_by_id = current_user.id
    flag.reviewed_at = datetime.utcnow()
    flag.review_notes = (request.form.get("notes") or "").strip()[:500] or None
    db.session.commit()
    log_activity(
        f"{decision}_similarity_flag",
        f"Marked similarity flag #{flag.id} on '{flag.test.title}' as {decision}",
    )
    flash("Flag updated.", "success")
    return redirect(url_for("admin.plagiarism_queue", test_id=flag.test_id))


@bp.route("/review-queue")
@review_access
def proctor_queue():
    """Landing page for the proctor role (also open to admin/examiner):
    every attempt that was auto-terminated or picked up at least one
    violation, scoped to the current user's organization — not scoped to
    tests the current user created, since proctoring review isn't
    ownership-based the way test authoring is, just tenant-based. Sorted
    by suspicion score (highest first) by default so the attempts most
    worth a human's time surface first; ?sort=recent switches to
    most-recently-started first."""
    page = request.args.get("page", 1, type=int)
    sort = request.args.get("sort", "risk")
    query = Attempt.query.join(Test).filter(
        Test.org_id == current_org_id(),
        db.or_(Attempt.status == "terminated", Attempt.violation_count > 0),
    )
    if sort == "recent":
        query = query.order_by(Attempt.started_at.desc())
    else:
        sort = "risk"
        query = query.order_by(Attempt.suspicion_score.desc(), Attempt.started_at.desc())
    pagination = query.paginate(page=page, per_page=PER_PAGE, error_out=False)
    # A one-line "why" for each row on the page — one extra query per
    # attempt (compute_suspicion_score re-reads that attempt's events),
    # kept cheap by PER_PAGE capping how many rows are ever on a page.
    top_reasons = {}
    for a in pagination.items:
        reasons = compute_suspicion_score(a)["reasons"]
        top_reasons[a.id] = reasons[0] if reasons else None
    return render_template(
        "admin/proctor_queue.html", pagination=pagination, attempts=pagination.items, sort=sort,
        top_reasons=top_reasons,
    )


@bp.route("/proctor-alerts/stream")
@review_access
def proctor_alerts_stream():
    """Server-Sent Events stream of live, high-severity proctoring alerts
    (see app.proctoring.HIGH_SEVERITY_ALERT_EVENT_TYPES) for the current
    user's organization, polled straight from the database — no separate
    message broker, so it works the same whether the app is running as one
    process or several. The browser's EventSource reconnects on its own
    whenever a stream ends (each one is capped at PROCTOR_ALERT_STREAM_SECONDS
    to bound how long a single request/worker is tied up) and resumes from
    Last-Event-ID, so a reconnect never re-delivers or skips an alert.

    This holds one worker/thread for as long as a client stays connected,
    which is fine for the handful of proctors a deployment like this
    expects watching the queue at once; a much larger concurrent proctor
    base would want an async server or a real pub/sub broker instead of
    polling in-request."""
    org_id = current_org_id()
    last_id = request.headers.get("Last-Event-ID", type=int)
    if last_id is None:
        last_id = request.args.get("since_id", type=int)
    if last_id is None:
        # First connection with no history to resume: only alert on events
        # from this point forward, rather than replaying everything that
        # ever happened in the org.
        last_id = db.session.query(db.func.max(ProctoringEvent.id)).scalar() or 0

    poll_seconds = current_app.config.get("PROCTOR_ALERT_POLL_SECONDS", 3)
    max_stream_seconds = current_app.config.get("PROCTOR_ALERT_STREAM_SECONDS", 55)

    def generate(last_id):
        started = time.time()
        yield "retry: 3000\n\n"
        while time.time() - started < max_stream_seconds:
            for event in get_live_alerts_since(last_id, org_id):
                last_id = event.id
                payload = {
                    "id": event.id,
                    "attempt_id": event.attempt_id,
                    "student_name": event.attempt.student.name,
                    "test_title": event.attempt.test.title,
                    "event_type": event.event_type,
                    "label": EVENT_TYPE_LABELS.get(event.event_type, event.event_type.replace("_", " ")),
                    "details": event.details,
                    "created_at": event.created_at.strftime("%H:%M:%S"),
                    "view_url": url_for("admin.view_attempt", attempt_id=event.attempt_id),
                }
                yield f"id: {event.id}\ndata: {json.dumps(payload)}\n\n"
            # Release this thread's DB connection back to the pool between
            # polls instead of pinning it for the whole stream lifetime.
            db.session.remove()
            time.sleep(poll_seconds)

    response = Response(
        stream_with_context(generate(last_id)),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
    return response


@bp.route("/activity-log")
@admin_required
def activity_log():
    page = request.args.get("page", 1, type=int)
    pagination = AdminActivityLog.query.join(User, AdminActivityLog.admin_id == User.id).filter(
        User.org_id == current_user.org_id
    ).order_by(AdminActivityLog.created_at.desc()).paginate(
        page=page, per_page=30, error_out=False
    )
    return render_template("admin/activity_log.html", pagination=pagination, entries=pagination.items)


@bp.route("/security-log")
@admin_required
def security_log():
    """Exam integrity: recent login sessions (device/IP per login) and any
    anomalies flagged against them (new device, new location, a concurrent
    login that kicked out an older session, suspected VPN/proxy) — see
    app.security. Scoped to accounts in the current admin's organization.
    Account-security data, so admin-only rather than opened up to
    examiner/proctor the way proctoring review is."""
    page = request.args.get("page", 1, type=int)
    events_pagination = LoginSecurityEvent.query.join(User, LoginSecurityEvent.user_id == User.id).filter(
        User.org_id == current_user.org_id
    ).order_by(LoginSecurityEvent.created_at.desc()).paginate(
        page=page, per_page=30, error_out=False
    )
    active_sessions = LoginSession.query.join(User, LoginSession.user_id == User.id).filter(
        User.org_id == current_user.org_id, LoginSession.is_active.is_(True)
    ).order_by(LoginSession.last_seen_at.desc()).limit(50).all()
    return render_template(
        "admin/security_log.html", pagination=events_pagination, events=events_pagination.items,
        active_sessions=active_sessions,
    )


@bp.route("/notifications")
@admin_required
def notification_history():
    """Every notification the system has sent (or attempted to send) to
    someone in the current admin's organization, newest first — the
    audit-trail half of Notifications & Reminders. See
    app.notifications.notify, which is the single place that writes these."""
    page = request.args.get("page", 1, type=int)
    type_filter = request.args.get("type", "")

    query = NotificationLog.query.join(User, NotificationLog.user_id == User.id).filter(
        User.org_id == current_user.org_id
    )
    if type_filter:
        query = query.filter(NotificationLog.notif_type == type_filter)
    query = query.order_by(NotificationLog.created_at.desc())

    pagination = query.paginate(page=page, per_page=PER_PAGE, error_out=False)
    notif_types = [row[0] for row in db.session.query(NotificationLog.notif_type).join(
        User, NotificationLog.user_id == User.id
    ).filter(User.org_id == current_user.org_id).distinct().all()]
    return render_template(
        "admin/notification_history.html",
        pagination=pagination, entries=pagination.items, type_filter=type_filter, notif_types=notif_types,
    )


@bp.route("/notifications/send-reminders", methods=["POST"])
@admin_required
def trigger_reminders():
    """Manual trigger for the same reminder sweep the `send-reminders` CLI
    command runs, restricted to this admin's own organization's tests —
    useful for testing or for an admin who doesn't have cron access,
    without requiring a background scheduler in the app itself."""
    sent = send_starting_soon_reminders(org_id=current_user.org_id)
    flash(f"Sent {sent} starting-soon reminder(s).", "success")
    return redirect(url_for("admin.notification_history"))


@bp.route("/analytics")
@review_access
def analytics_overview():
    """Cross-exam view: how each test in the current user's organization
    compares, and org-wide violation trends over the last 30 days. Drill
    into a specific test's page for the topic/difficulty/skip breakdowns
    that only make sense per-exam."""
    tests = org_scope(Test.query, Test).order_by(Test.created_at.desc()).all()
    comparison = analytics.exam_comparison(tests)
    trends = analytics.violation_trends(days=30, org_id=current_org_id())
    score_trend = analytics.org_score_trend(current_org_id())
    return render_template(
        "admin/analytics_overview.html", comparison=comparison, trends=trends, score_trend=score_trend,
    )


@bp.route("/tests/<int:test_id>/analytics")
@review_access
def test_analytics(test_id):
    """Per-exam analytics: performance by topic, question difficulty
    (observed success rate vs. the admin-set label), question-wise success
    rate and average time spent, most-skipped questions, and this test's
    slice of the violation trend."""
    test = Test.query.get_or_404(test_id)
    ensure_same_org(test)
    topics = analytics.performance_by_topic(test)
    questions = analytics.question_stats(test)
    difficulty = analytics.question_difficulty(test)
    skipped = analytics.most_skipped_questions(test)
    weak = analytics.weak_areas(test)
    time_analysis = analytics.time_per_question_analysis(test)
    trends = analytics.violation_trends(attempts=test.attempts, days=30)
    return render_template(
        "admin/test_analytics.html", test=test, topics=topics, questions=questions,
        difficulty=difficulty, skipped=skipped, weak=weak, time_analysis=time_analysis, trends=trends,
    )


@bp.route("/students/<int:student_id>/analytics")
@review_access
def student_analytics(student_id):
    """One student's cross-exam view: score/violation trend over time, plus
    aggregate performance by topic across every test they've taken. Scoped
    to students in the current user's organization."""
    student = User.query.filter_by(id=student_id, role="student", org_id=current_org_id()).first_or_404()
    trend = analytics.student_performance_trend(student)
    topics = analytics.student_topic_performance(student)
    weak = analytics.student_weak_areas(student)
    return render_template("admin/student_analytics.html", student=student, trend=trend, topics=topics, weak=weak)


@bp.route("/bank")
@content_access
def manage_bank():
    page = request.args.get("page", 1, type=int)
    search = request.args.get("q", "").strip()
    category = request.args.get("category", "").strip()

    query = org_scope(QuestionBankItem.query, QuestionBankItem)
    if search:
        query = query.filter(QuestionBankItem.question_text.ilike(f"%{search}%"))
    if category:
        query = query.filter_by(category=category)
    query = query.order_by(QuestionBankItem.created_at.desc())

    pagination = query.paginate(page=page, per_page=PER_PAGE, error_out=False)
    categories = [c[0] for c in org_scope(db.session.query(QuestionBankItem.category), QuestionBankItem).filter(
        QuestionBankItem.category.isnot(None)
    ).distinct().order_by(QuestionBankItem.category).all()]
    usage_counts = {
        item.id: Question.query.filter_by(bank_item_id=item.id).count() for item in pagination.items
    }

    return render_template(
        "admin/manage_bank.html", pagination=pagination, items=pagination.items,
        search=search, category=category, categories=categories, usage_counts=usage_counts,
        total_items=org_scope(QuestionBankItem.query, QuestionBankItem).count(),
    )


@bp.route("/bank/add", methods=["GET", "POST"])
@content_access
def add_bank_item():
    form = QuestionBankForm()
    if form.validate_on_submit():
        fields, error = _parse_question_fields(form)
        if error:
            flash(error, "error")
        else:
            item = QuestionBankItem(created_by=current_user.id, org_id=current_user.org_id, **fields)
            db.session.add(item)
            db.session.commit()
            log_activity("added_bank_item", f"Added a {item.question_type} question to the question bank")
            flash("Added to the question bank.", "success")
            return redirect(url_for("admin.manage_bank"))
    return render_template("admin/bank_item_form.html", form=form, item=None)


@bp.route("/bank/<int:item_id>/edit", methods=["GET", "POST"])
@content_access
def edit_bank_item(item_id):
    item = QuestionBankItem.query.get_or_404(item_id)
    ensure_same_org(item)
    form = QuestionBankForm(obj=item)
    if request.method == "GET":
        if item.question_type == "short":
            form.short_answer_text.data = item.correct_answer
        elif item.question_type == "fill_blank":
            form.blank_answer.data = item.correct_answer
        elif item.question_type in ("descriptive", "coding"):
            form.model_answer.data = item.correct_answer
        form.media_type.data = item.media_type or ""
    if form.validate_on_submit():
        fields, error = _parse_question_fields(form, existing=item)
        if error:
            flash(error, "error")
        else:
            for key, value in fields.items():
                setattr(item, key, value)
            db.session.commit()
            log_activity("edited_bank_item", f"Edited a question bank item (#{item.id})")
            flash("Question bank item updated.", "success")
            return redirect(url_for("admin.manage_bank"))
    return render_template("admin/bank_item_form.html", form=form, item=item)


@bp.route("/bank/<int:item_id>/delete", methods=["POST"])
@content_access
def delete_bank_item(item_id):
    item = QuestionBankItem.query.get_or_404(item_id)
    ensure_same_org(item)
    # Copies already made from this item stay in their tests — only the
    # provenance link is cleared, not the questions themselves.
    Question.query.filter_by(bank_item_id=item.id).update({"bank_item_id": None})
    db.session.delete(item)
    db.session.commit()
    log_activity("deleted_bank_item", f"Deleted a question bank item (#{item_id})")
    flash("Removed from the question bank.", "success")
    return redirect(url_for("admin.manage_bank"))


@bp.route("/users")
@admin_required
def manage_users():
    page = request.args.get("page", 1, type=int)
    search = request.args.get("q", "").strip()
    role_filter = request.args.get("role", "")
    status_filter = request.args.get("status", "")

    query = org_scope(User.query, User)
    if search:
        like = f"%{search}%"
        query = query.filter(
            db.or_(User.name.ilike(like), User.email.ilike(like), User.user_id.ilike(like))
        )
    if role_filter in ("student", "examiner", "proctor", "admin"):
        query = query.filter_by(role=role_filter)
    if status_filter in ("active", "inactive"):
        query = query.filter_by(status=status_filter)
    query = query.order_by(User.created_at.desc())

    pagination = query.paginate(page=page, per_page=PER_PAGE, error_out=False)
    org_users = org_scope(User.query, User)

    return render_template(
        "admin/manage_users.html",
        pagination=pagination, users=pagination.items,
        search=search, role_filter=role_filter, status_filter=status_filter,
        total_users=org_users.count(),
        total_students=org_scope(User.query, User).filter_by(role="student").count(),
        total_active=org_scope(User.query, User).filter_by(status="active").count(),
        total_inactive=org_scope(User.query, User).filter_by(status="inactive").count(),
    )


@bp.route("/users/<int:user_id>/toggle-status", methods=["POST"])
@admin_required
def toggle_user_status(user_id):
    user = User.query.get_or_404(user_id)
    ensure_same_org(user)
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
    ensure_same_org(user)
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


@bp.route("/id-verification")
@admin_required
def id_verification_queue():
    """Every uploaded ID document that hasn't been reviewed yet, oldest
    first, plus a running total so admins can see how big the backlog is.
    Scoped to students in the current admin's organization. Reviewing a
    document (approve/reject) is separate from — and doesn't block — the
    automatic face-match + liveness check that actually enrolls the
    student; see IdentityDocument's docstring."""
    page = request.args.get("page", 1, type=int)
    status_filter = request.args.get("status", "pending")

    query = IdentityDocument.query.join(User, IdentityDocument.user_id == User.id).filter(
        User.org_id == current_user.org_id
    )
    if status_filter in ("pending", "approved", "rejected"):
        query = query.filter(IdentityDocument.review_status == status_filter)
    query = query.order_by(IdentityDocument.uploaded_at.asc())

    pagination = query.paginate(page=page, per_page=PER_PAGE, error_out=False)
    total_pending = IdentityDocument.query.join(User, IdentityDocument.user_id == User.id).filter(
        User.org_id == current_user.org_id, IdentityDocument.review_status == "pending"
    ).count()
    return render_template(
        "admin/id_verification_queue.html",
        pagination=pagination, documents=pagination.items, status_filter=status_filter,
        total_pending=total_pending,
    )


@bp.route("/id-verification/<int:doc_id>/review", methods=["POST"])
@admin_required
def review_id_document(doc_id):
    doc = IdentityDocument.query.get_or_404(doc_id)
    ensure_same_org(doc.user)
    decision = request.form.get("decision")
    notes = (request.form.get("notes") or "").strip()[:500]

    if decision not in ("approved", "rejected"):
        flash("Choose approve or reject.", "error")
        return redirect(url_for("admin.id_verification_queue"))

    doc.review_status = decision
    doc.reviewed_by_id = current_user.id
    doc.reviewed_at = datetime.utcnow()
    doc.review_notes = notes or None

    if decision == "rejected":
        # The underlying document wasn't valid, so the enrollment it fed
        # (if the automatic match had already passed) shouldn't stand either
        # — the student needs to upload a valid ID and re-verify before
        # they can take a proctored test again.
        doc.user.face_descriptor = None

    db.session.commit()
    log_activity(
        f"{decision}_id_document",
        f"{decision.capitalize()} the ID document uploaded by {doc.user.name} ({doc.user.email})"
        + (f" — {notes}" if notes else ""),
    )
    flash(f"Document {decision}.", "success")
    return redirect(url_for("admin.id_verification_queue", status=request.form.get("return_status", "pending")))


@bp.route("/users/export")
@admin_required
def export_users():
    role_filter = request.args.get("role", "")
    status_filter = request.args.get("status", "")

    query = org_scope(User.query, User)
    if role_filter in ("student", "examiner", "proctor", "admin"):
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
                role="student", status="active", email_verified=True, org_id=current_user.org_id,
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


@bp.route("/retention", methods=["GET", "POST"])
@admin_required
def retention_settings():
    """Let this organization's own admin override the platform's default
    retention windows for their own data — recordings, snapshots, and
    account/log history. Falls back to the platform default (or ultimately
    config.py) for anything left blank; see app.retention for the full
    fallback chain. error_log isn't org-scoped, so it never appears here —
    only super_admin edits that, from /ops/retention."""
    org = current_user.organization
    form = RetentionPolicyForm()

    if form.validate_on_submit():
        retention_module.save_form_to_policy(
            form, current_user.org_id, current_user.id, retention_module.ORG_EDITABLE_CATEGORIES
        )
        log_activity("updated_retention_policy", f"Updated retention policy overrides for '{org.name}'")
        flash("Retention settings updated.", "success")
        return redirect(url_for("admin.retention_settings"))

    if request.method == "GET":
        retention_module.populate_form_from_policy(form, current_user.org_id)

    effective = retention_module.effective_policy_for_org(org)
    return render_template(
        "admin/retention_settings.html", form=form, org=org,
        effective=effective, labels=retention_module.CATEGORY_LABELS,
    )


@bp.route("/org-backup")
@admin_required
def download_org_backup():
    """Self-service data export for this organization — the org-level
    counterpart to the platform-wide sqlite backup at /ops/backups (which
    is super_admin-only, since a single shared database file can't be
    split into a per-org copy). See app.org_export for exactly what's
    included."""
    org = current_user.organization
    data = org_export.export_organization_data(org)
    payload = json.dumps(data, indent=2)
    filename = f"{org.slug}_export_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.json"
    log_activity("exported_org_data", f"Downloaded a data export for '{org.name}'")
    return Response(
        payload, mimetype="application/json",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@bp.route("/org-report")
@admin_required
def org_report():
    """Self-service organization-level report for this org's own admin —
    the same headcount/test/attempt/pass-rate summary a super_admin sees
    on that org's detail page, computed by the same shared
    app.org_reports.build_org_summary() so the numbers always agree."""
    org = current_user.organization
    summary = org_reports.build_org_summary(org)
    return render_template("admin/org_report.html", summary=summary)


@bp.route("/org-report/export.csv")
@admin_required
def export_org_report_csv():
    org = current_user.organization
    summary = org_reports.build_org_summary(org)
    csv_text = org_reports.render_summary_csv(summary)
    filename = f"{org.slug}_report_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.csv"
    log_activity("exported_org_report", f"Exported CSV report for '{org.name}'")
    return Response(
        csv_text, mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@bp.route("/org-report/export.pdf")
@admin_required
def export_org_report_pdf():
    org = current_user.organization
    summary = org_reports.build_org_summary(org)
    pdf_bytes = org_reports.render_summary_pdf(summary)
    filename = f"{org.slug}_report_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.pdf"
    log_activity("exported_org_report", f"Exported PDF report for '{org.name}'")
    return Response(
        pdf_bytes, mimetype="application/pdf",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@bp.route("/branding")
@admin_required
def branding_settings():
    """Let this organization's own admin upload a logo and set a primary
    accent color, applied to their org's users via app.branding — see
    that module's docstring for exactly where it does and doesn't apply
    (never on the login/register pages, since there's no way to know an
    anonymous visitor's organization before they authenticate). Logo
    upload and color are two independent forms/routes below, not one
    combined submit — an HTML5 type="color" input can never be truly
    empty, so a shared submit button would silently overwrite the org's
    color with black every time someone only meant to upload a logo."""
    org = current_user.organization
    form = BrandingForm()
    certificate_form = CertificateSettingsForm(obj=org)
    return render_template(
        "admin/branding_settings.html", form=form, org=org, certificate_form=certificate_form,
    )


@bp.route("/branding/logo", methods=["POST"])
@admin_required
def upload_branding_logo():
    org = current_user.organization
    form = BrandingForm()
    if form.validate_on_submit() and form.logo.data:
        try:
            branding_module.save_logo(org, form.logo.data)
            log_activity("updated_org_branding", f"Updated logo for '{org.name}'")
            flash("Logo updated.", "success")
        except ValueError as exc:
            flash(str(exc), "error")
    else:
        flash("Choose a logo file to upload.", "error")
    return redirect(url_for("admin.branding_settings"))


@bp.route("/branding/color", methods=["POST"])
@admin_required
def set_branding_color():
    org = current_user.organization
    form = BrandingForm()
    if form.validate_on_submit() and form.primary_color.data:
        try:
            org.primary_color = branding_module.validate_color(form.primary_color.data)
            db.session.commit()
            log_activity("updated_org_branding", f"Updated primary color for '{org.name}'")
            flash("Primary color updated.", "success")
        except ValueError as exc:
            flash(str(exc), "error")
    else:
        flash("Choose a color first.", "error")
    return redirect(url_for("admin.branding_settings"))


@bp.route("/branding/remove-logo", methods=["POST"])
@admin_required
def remove_branding_logo():
    org = current_user.organization
    branding_module.remove_logo(org)
    log_activity("removed_org_logo", f"Removed logo for '{org.name}'")
    flash("Logo removed.", "success")
    return redirect(url_for("admin.branding_settings"))


@bp.route("/branding/remove-color", methods=["POST"])
@admin_required
def remove_branding_color():
    org = current_user.organization
    org.primary_color = None
    db.session.commit()
    log_activity("removed_org_color", f"Removed primary color for '{org.name}'")
    flash("Primary color removed.", "success")
    return redirect(url_for("admin.branding_settings"))


@bp.route("/branding/certificate", methods=["POST"])
@admin_required
def set_certificate_settings():
    """The signatory line shown on this org's certificates (see
    app.certificates) — org-wide, applies to every test with
    certificate_enabled on, not set per test."""
    org = current_user.organization
    form = CertificateSettingsForm()
    if form.validate_on_submit():
        org.certificate_signatory_name = form.certificate_signatory_name.data.strip() or None
        org.certificate_signatory_title = form.certificate_signatory_title.data.strip() or None
        db.session.commit()
        log_activity("updated_certificate_settings", f"Updated certificate signatory for '{org.name}'")
        flash("Certificate settings updated.", "success")
    else:
        flash("Couldn't save those certificate settings.", "error")
    return redirect(url_for("admin.branding_settings"))


# ---------------------------------------------------------------------------
# LMS/API Integrations (see app.api_v1) — API key management and the
# optional outbound result-published webhook.
# ---------------------------------------------------------------------------

@bp.route("/integrations", methods=["GET"])
@admin_required
def api_keys():
    org = current_user.organization
    keys = ApiKey.query.filter_by(org_id=org.id).order_by(ApiKey.created_at.desc()).all()
    key_form = ApiKeyForm()
    webhook_form = LmsWebhookForm(lms_webhook_url=org.lms_webhook_url)
    new_key = None
    if request.args.get("new_key_id"):
        # One-shot display right after creation — see create_api_key below.
        # The raw key itself was never stored, so it only ever exists in
        # this single response; a page refresh won't bring it back.
        new_key = request.args.get("new_key_raw")
    return render_template(
        "admin/api_keys.html", org=org, keys=keys, key_form=key_form, webhook_form=webhook_form,
        new_key=new_key,
    )


@bp.route("/integrations/keys", methods=["POST"])
@admin_required
def create_api_key():
    from app.api_v1 import generate_api_key

    form = ApiKeyForm()
    if not form.validate_on_submit():
        flash("Give the key a label first.", "error")
        return redirect(url_for("admin.api_keys"))

    raw_key, prefix, key_hash = generate_api_key()
    key = ApiKey(
        org_id=current_user.org_id, label=form.label.data.strip(), key_hash=key_hash, prefix=prefix,
        created_by_id=current_user.id,
    )
    db.session.add(key)
    db.session.commit()
    log_activity("created_api_key", f"Created API key '{key.label}'")
    flash("API key created — copy it now, it won't be shown again.", "success")
    return redirect(url_for("admin.api_keys", new_key_id=key.id, new_key_raw=raw_key))


@bp.route("/integrations/keys/<int:key_id>/revoke", methods=["POST"])
@admin_required
def revoke_api_key(key_id):
    key = ApiKey.query.get_or_404(key_id)
    ensure_same_org(key)
    key.revoked = True
    key.revoked_at = datetime.utcnow()
    db.session.commit()
    log_activity("revoked_api_key", f"Revoked API key '{key.label}'")
    flash("API key revoked.", "success")
    return redirect(url_for("admin.api_keys"))


@bp.route("/integrations/webhook", methods=["POST"])
@admin_required
def set_lms_webhook():
    org = current_user.organization
    form = LmsWebhookForm()
    if form.validate_on_submit():
        org.lms_webhook_url = form.lms_webhook_url.data.strip() or None
        db.session.commit()
        log_activity("updated_lms_webhook", f"Updated LMS result webhook for '{org.name}'")
        flash("Webhook URL updated.", "success")
    else:
        flash("Enter a valid URL, or leave it blank to disable the webhook.", "error")
    return redirect(url_for("admin.api_keys"))
