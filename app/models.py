import uuid
from datetime import datetime

from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash

from app import db


def gen_user_id(role):
    return f"{role[:3].upper()}{uuid.uuid4().hex[:8].upper()}"


class User(UserMixin, db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.String(32), unique=True, nullable=False, default=lambda: gen_user_id("usr"))
    name = db.Column(db.String(120), nullable=False)
    email = db.Column(db.String(150), unique=True, nullable=False, index=True)
    phone = db.Column(db.String(15))
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), nullable=False, default="student")  # student | examiner | proctor | admin
    status = db.Column(db.String(20), nullable=False, default="active")  # active | inactive
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    email_verified = db.Column(db.Boolean, nullable=False, default=False)
    # 128-float face-api.js descriptor captured during face enrollment, stored as JSON text.
    # Used to verify the student's identity against the live webcam feed during a proctored test.
    face_descriptor = db.Column(db.Text, nullable=True)

    # Login rate-limiting / brute-force protection
    failed_login_attempts = db.Column(db.Integer, nullable=False, default=0)
    locked_until = db.Column(db.DateTime, nullable=True)

    tests_created = db.relationship("Test", backref="creator", lazy=True)
    attempts = db.relationship("Attempt", backref="student", lazy=True)

    def set_password(self, raw_password):
        self.password_hash = generate_password_hash(raw_password)

    def check_password(self, raw_password):
        return check_password_hash(self.password_hash, raw_password)

    def __repr__(self):
        return f"<User {self.email} ({self.role})>"


class Test(db.Model):
    __tablename__ = "tests"

    id = db.Column(db.Integer, primary_key=True)
    test_code = db.Column(db.String(64), unique=True, nullable=False)
    title = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text)
    # Longer-form rules/instructions shown to the student on the pre-exam
    # consent screen, separate from the short `description` blurb shown
    # everywhere else (dashboard, results, etc).
    instructions = db.Column(db.Text, nullable=True)
    duration_minutes = db.Column(db.Integer, nullable=False, default=30)
    total_questions = db.Column(db.Integer, nullable=False, default=0)
    passing_marks = db.Column(db.Integer, nullable=False, default=0)
    status = db.Column(db.String(20), nullable=False, default="draft")  # draft | published
    start_time = db.Column(db.DateTime, nullable=True)
    end_time = db.Column(db.DateTime, nullable=True)
    created_by = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    # Retake policy
    max_attempts = db.Column(db.Integer, nullable=False, default=1)
    # Anti-collusion shuffling — question order and per-question option order
    # are independent toggles so an admin can shuffle one without the other.
    randomize_questions = db.Column(db.Boolean, nullable=False, default=True)
    randomize_options = db.Column(db.Boolean, nullable=False, default=True)
    # Negative marking: points deducted per wrong answer (0 disables it)
    negative_marks_per_wrong = db.Column(db.Float, nullable=False, default=0.0)
    # Whether students can review their answers (with correct answers shown) after submitting
    allow_review = db.Column(db.Boolean, nullable=False, default=True)
    # Partial credit for multi-select questions: award proportional marks based on
    # how many correct options were picked minus how many incorrect ones were,
    # instead of all-or-nothing. Doesn't affect single-choice or short-answer grading.
    partial_credit_multi = db.Column(db.Boolean, nullable=False, default=False)

    questions = db.relationship("Question", backref="test", cascade="all, delete-orphan", lazy=True)
    eligibility = db.relationship("TestEligibility", backref="test", cascade="all, delete-orphan", lazy=True)
    attempts = db.relationship("Attempt", backref="test", cascade="all, delete-orphan", lazy=True)
    sections = db.relationship(
        "Section", backref="test", cascade="all, delete-orphan", lazy=True,
        order_by="Section.order_index",
    )

    def total_marks(self):
        return sum(q.marks for q in self.questions) or 0

    def is_open_now(self):
        now = datetime.utcnow()
        if self.start_time and now < self.start_time:
            return False
        if self.end_time and now > self.end_time:
            return False
        return True


class Section(db.Model):
    """An optional named grouping of a test's questions (e.g. 'Verbal
    Reasoning', 'Coding'). A test with no sections behaves exactly as
    before — sections are purely additive. duration_minutes, if set,
    gives the section its own countdown in the exam UI independent of
    the overall test timer; questions in a section with no duration are
    only bound by the overall exam timer."""

    __tablename__ = "sections"

    id = db.Column(db.Integer, primary_key=True)
    test_id = db.Column(db.Integer, db.ForeignKey("tests.id"), nullable=False)
    name = db.Column(db.String(150), nullable=False)
    description = db.Column(db.Text, nullable=True)
    order_index = db.Column(db.Integer, nullable=False, default=0)
    duration_minutes = db.Column(db.Integer, nullable=True)  # None = no section-specific timer

    questions = db.relationship("Question", backref="section", lazy=True)


class QuestionBankItem(db.Model):
    """A reusable question template, independent of any test. Admins pull
    items from here into a specific test's question list (a copy, so each
    test's Question stays independently editable/gradable), instead of
    retyping the same question every time it's needed across tests."""

    __tablename__ = "question_bank"

    id = db.Column(db.Integer, primary_key=True)
    created_by = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    # Free-text topic/category tag for filtering (e.g. "Python Basics", "HR Policy").
    category = db.Column(db.String(100), nullable=True, index=True)
    difficulty = db.Column(db.String(10), nullable=True, default="medium")  # easy | medium | hard

    question_type = db.Column(db.String(10), nullable=False, default="single")
    option_a = db.Column(db.String(500), nullable=True)
    option_b = db.Column(db.String(500), nullable=True)
    option_c = db.Column(db.String(500), nullable=True)
    option_d = db.Column(db.String(500), nullable=True)
    correct_answer = db.Column(db.String(500), nullable=False)
    marks = db.Column(db.Integer, nullable=False, default=1)
    time_limit_seconds = db.Column(db.Integer, nullable=True)
    question_text = db.Column(db.Text, nullable=False)
    # Optional media attached to the question (works with any question_type,
    # e.g. an MCQ built around a diagram). media_url is either an uploaded
    # file's static path or an admin-supplied external URL.
    media_type = db.Column(db.String(10), nullable=True)  # image | video | None
    media_url = db.Column(db.String(500), nullable=True)
    # coding-type only: language is free text (display/hint only, no execution)
    starter_code = db.Column(db.Text, nullable=True)
    code_language = db.Column(db.String(30), nullable=True)

    creator = db.relationship("User")
    copies = db.relationship("Question", backref="bank_item", lazy=True)

    def options(self):
        return {"a": self.option_a, "b": self.option_b, "c": self.option_c, "d": self.option_d}

    def correct_set(self):
        return {c.strip().lower() for c in self.correct_answer.split(",") if c.strip()}


class Question(db.Model):
    __tablename__ = "questions"

    id = db.Column(db.Integer, primary_key=True)
    test_id = db.Column(db.Integer, db.ForeignKey("tests.id"), nullable=False)
    # Set when this question was copied in from the shared question bank, so
    # the bank item's "used in N test(s)" count can be computed and an admin
    # can trace where a question came from. The copy is independent afterward
    # — editing/deleting it here never touches the bank item or other tests
    # that copied the same item.
    bank_item_id = db.Column(db.Integer, db.ForeignKey("question_bank.id"), nullable=True)
    # Optional grouping into one of the test's Sections (see Section above).
    # NULL means the question isn't in any section — fine for tests that
    # don't use sections at all.
    section_id = db.Column(db.Integer, db.ForeignKey("sections.id"), nullable=True)
    # Free-text topic/category tag, independent of any section assignment
    # (e.g. filtering/reporting by topic even within a single section).
    category = db.Column(db.String(100), nullable=True, index=True)
    difficulty = db.Column(db.String(10), nullable=True, default="medium")  # easy | medium | hard
    question_text = db.Column(db.Text, nullable=False)

    # single      -> one correct option (a-d); correct_answer = "b"
    # multi       -> one or more correct options; correct_answer = "a,c" (sorted, comma-joined)
    # true_false  -> correct_answer = "true" or "false"; options unused
    # short       -> free-text answer; options unused; correct_answer = the expected text,
    #                graded case-insensitively with whitespace trimmed
    # fill_blank  -> question_text contains a blank (e.g. "____"); correct_answer is one or
    #                more acceptable answers separated by ";" (e.g. "colour;color")
    # descriptive -> free-text essay answer; NOT auto-graded. correct_answer optionally holds
    #                a model answer / rubric shown only to the grader, never compared.
    # coding      -> free-text code answer; NOT auto-graded (no sandboxed execution — a human
    #                reviews it). correct_answer optionally holds reference notes for the grader.
    question_type = db.Column(db.String(10), nullable=False, default="single")

    option_a = db.Column(db.String(500), nullable=True)
    option_b = db.Column(db.String(500), nullable=True)
    option_c = db.Column(db.String(500), nullable=True)
    option_d = db.Column(db.String(500), nullable=True)
    correct_answer = db.Column(db.String(500), nullable=False)
    marks = db.Column(db.Integer, nullable=False, default=1)
    # Optional soft per-question pacing limit, in seconds. All questions are shown
    # on one page (not a stepper), so this is enforced client-side: once a
    # question's clock runs out, its inputs lock, but the rest of the exam
    # continues normally on the overall exam timer. NULL means no per-question limit.
    time_limit_seconds = db.Column(db.Integer, nullable=True)
    # Optional media attached to the question — see QuestionBankItem for the same fields.
    media_type = db.Column(db.String(10), nullable=True)  # image | video | None
    media_url = db.Column(db.String(500), nullable=True)
    starter_code = db.Column(db.Text, nullable=True)
    code_language = db.Column(db.String(30), nullable=True)

    def options(self):
        return {"a": self.option_a, "b": self.option_b, "c": self.option_c, "d": self.option_d}

    def correct_set(self):
        """Correct option letters as a set, for single/multi questions."""
        return {c.strip().lower() for c in self.correct_answer.split(",") if c.strip()}

    @property
    def needs_manual_grading(self):
        """Descriptive (essay) and coding answers aren't string-matched —
        a human reviews them and enters marks via the grading route."""
        return self.question_type in ("descriptive", "coding")

    def accepted_blank_answers(self):
        """fill_blank only: one or more acceptable answers, separated by ';'
        in correct_answer (e.g. 'colour;color')."""
        return {a.strip().lower() for a in self.correct_answer.split(";") if a.strip()}

    def is_correct(self, submitted):
        """Grade a submitted answer string against this question's correct
        answer. For single/multi, submitted is a comma-joined set of option
        letters (order doesn't matter). For short/true_false, it's matched
        case-insensitively with surrounding whitespace trimmed. For
        fill_blank, it's matched against any of the accepted alternatives.
        descriptive/coding are never auto-graded — always False here."""
        if not submitted:
            return False
        if self.needs_manual_grading:
            return False
        if self.question_type in ("short", "true_false"):
            return submitted.strip().lower() == self.correct_answer.strip().lower()
        if self.question_type == "fill_blank":
            return submitted.strip().lower() in self.accepted_blank_answers()
        submitted_set = {c.strip().lower() for c in submitted.split(",") if c.strip()}
        return submitted_set == self.correct_set()

    def score_for(self, submitted, partial_credit_multi=False):
        """Marks earned for a submitted answer (float). Single/short/
        true_false/fill_blank questions are all-or-nothing. Multi-select is
        all-or-nothing too unless partial_credit_multi is set, in which case
        it awards proportional credit: (correct options picked - incorrect
        options picked) / total correct options, floored at 0 marks — so
        guessing extra wrong options can only reduce credit toward zero,
        never below it. descriptive/coding always return 0 here — their
        marks come from Answer.manual_score once a grader enters one."""
        if not submitted or self.needs_manual_grading:
            return 0.0

        if self.question_type != "multi" or not partial_credit_multi:
            return float(self.marks) if self.is_correct(submitted) else 0.0

        correct = self.correct_set()
        selected = {c.strip().lower() for c in submitted.split(",") if c.strip()}
        if not correct:
            return 0.0
        num_correct_picked = len(selected & correct)
        num_incorrect_picked = len(selected - correct)
        fraction = max((num_correct_picked - num_incorrect_picked) / len(correct), 0.0)
        return round(self.marks * fraction, 2)


class TestEligibility(db.Model):
    __tablename__ = "test_eligibility"

    id = db.Column(db.Integer, primary_key=True)
    test_id = db.Column(db.Integer, db.ForeignKey("tests.id"), nullable=False)
    student_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    assigned_at = db.Column(db.DateTime, default=datetime.utcnow)

    student = db.relationship("User")

    # Accessibility accommodation: extra minutes added to the test duration for this student
    extra_time_minutes = db.Column(db.Integer, nullable=False, default=0)
    # Admin override: extra attempts granted to this student beyond the test's
    # normal max_attempts (e.g. after a proctoring issue voided a prior attempt).
    extra_attempts = db.Column(db.Integer, nullable=False, default=0)

    __table_args__ = (db.UniqueConstraint("test_id", "student_id", name="uq_test_student"),)


class Attempt(db.Model):
    __tablename__ = "attempts"

    id = db.Column(db.Integer, primary_key=True)
    attempt_token = db.Column(db.String(64), unique=True, nullable=False)
    test_id = db.Column(db.Integer, db.ForeignKey("tests.id"), nullable=False)
    student_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    started_at = db.Column(db.DateTime, default=datetime.utcnow)
    submitted_at = db.Column(db.DateTime, nullable=True)
    score = db.Column(db.Float, nullable=True)
    status = db.Column(db.String(20), nullable=False, default="in_progress")
    # in_progress | submitted | terminated
    violation_count = db.Column(db.Integer, nullable=False, default=0)
    termination_reason = db.Column(db.String(255), nullable=True)

    # Per-attempt randomized question order (list of question ids) and per-question
    # option display order (dict of question_id -> list of option keys in display
    # order), generated deterministically from attempt_token so a page refresh
    # doesn't reshuffle mid-exam. Grading always uses the original option letters,
    # so this only randomizes what's shown, not how answers are scored.
    question_order = db.Column(db.Text, nullable=True)
    option_order = db.Column(db.Text, nullable=True)

    # Periodically saved in-progress answers (JSON: question_id -> value or
    # list of values), so a refresh/crash/browser close mid-exam doesn't lose
    # what the student had already picked. Written by the autosave endpoint,
    # read back to pre-fill the form when an in-progress attempt is resumed,
    # and cleared once the attempt is actually submitted/terminated.
    autosaved_answers = db.Column(db.Text, nullable=True)

    answers = db.relationship("Answer", backref="attempt", cascade="all, delete-orphan", lazy=True)
    events = db.relationship("ProctoringEvent", backref="attempt", cascade="all, delete-orphan", lazy=True)


class Answer(db.Model):
    __tablename__ = "answers"

    id = db.Column(db.Integer, primary_key=True)
    attempt_id = db.Column(db.Integer, db.ForeignKey("attempts.id"), nullable=False)
    question_id = db.Column(db.Integer, db.ForeignKey("questions.id"), nullable=False)
    # single/true_false -> "b"/"true" ; multi -> "a,c" (sorted, comma-joined) ;
    # short/fill_blank/descriptive/coding -> free text. NULL/empty if unanswered.
    selected_option = db.Column(db.String(500), nullable=True)

    # Manual grading, for descriptive/coding answers only (see
    # Question.needs_manual_grading). NULL manual_score means "not graded
    # yet" — it contributes 0 to Attempt.score until a grader enters one via
    # the grading route, which is safe to call repeatedly/idempotently.
    manual_score = db.Column(db.Float, nullable=True)
    graded_at = db.Column(db.DateTime, nullable=True)
    graded_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)

    question = db.relationship("Question")
    grader = db.relationship("User")

    __table_args__ = (db.UniqueConstraint("attempt_id", "question_id", name="uq_attempt_question"),)


def recompute_attempt_score(attempt):
    """Single source of truth for an attempt's score. Auto-graded questions
    score themselves via Question.score_for (with negative marking applied
    on a wrong answer); descriptive/coding questions instead contribute
    their Answer.manual_score once a grader has entered one (0 until then).
    Safe to call repeatedly/idempotently — e.g. once at submission and again
    each time an admin grades or re-grades a manual answer, since it always
    recomputes the full total from scratch rather than adding on top."""
    test = attempt.test
    answers = Answer.query.filter_by(attempt_id=attempt.id).all()
    score = 0.0
    for answer in answers:
        question = answer.question
        if not answer.selected_option:
            continue
        if question.needs_manual_grading:
            if answer.manual_score is not None:
                score += answer.manual_score
            continue
        earned = question.score_for(answer.selected_option, partial_credit_multi=test.partial_credit_multi)
        if earned > 0:
            score += earned
        elif test.negative_marks_per_wrong:
            score -= test.negative_marks_per_wrong
    return round(score, 2)


class ProctoringEvent(db.Model):
    __tablename__ = "proctoring_events"

    id = db.Column(db.Integer, primary_key=True)
    attempt_id = db.Column(db.Integer, db.ForeignKey("attempts.id"), nullable=False)
    event_type = db.Column(db.String(50), nullable=False)
    # no_face | multiple_faces | tab_hidden | fullscreen_exit | identity_mismatch |
    # audio_violation | copy_paste_attempt | window_blur | server_snapshot_flag
    severity = db.Column(db.String(20), nullable=False, default="warning")  # warning | violation
    details = db.Column(db.String(500))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class Recording(db.Model):
    __tablename__ = "recordings"

    id = db.Column(db.Integer, primary_key=True)
    attempt_id = db.Column(db.Integer, db.ForeignKey("attempts.id"), nullable=False)
    chunk_index = db.Column(db.Integer, nullable=False, default=0)
    filename = db.Column(db.String(255), nullable=False)
    content_type = db.Column(db.String(100), nullable=False, default="video/webm")
    file_size = db.Column(db.Integer, nullable=False, default=0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    attempt = db.relationship("Attempt", backref=db.backref("recordings", cascade="all, delete-orphan", lazy=True))


class Snapshot(db.Model):
    """A still frame saved when the server-side OpenCV check flags a proctoring
    snapshot as anomalous (no face / multiple faces), so the admin can visually
    confirm the automated verdict instead of trusting the count alone."""

    __tablename__ = "snapshots"

    id = db.Column(db.Integer, primary_key=True)
    attempt_id = db.Column(db.Integer, db.ForeignKey("attempts.id"), nullable=False)
    filename = db.Column(db.String(255), nullable=False)
    faces_detected = db.Column(db.Integer, nullable=False, default=0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    attempt = db.relationship("Attempt", backref=db.backref("snapshots", cascade="all, delete-orphan", lazy=True))


class IpRateLimit(db.Model):
    """One row per throttled request from a given IP, used to enforce
    per-IP rate limits on abuse-prone unauthenticated endpoints (account
    registration, password-reset requests, verification-email resends).
    A count of rows for (ip_address, action) within a trailing time window
    is compared against a configured max to decide whether to allow the
    request — see app.utils.is_rate_limited()."""

    __tablename__ = "ip_rate_limits"

    id = db.Column(db.Integer, primary_key=True)
    ip_address = db.Column(db.String(45), nullable=False, index=True)  # IPv4 or IPv6
    action = db.Column(db.String(50), nullable=False)  # register | forgot_password | resend_verification
    created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)


class AdminActivityLog(db.Model):
    """Audit trail of admin actions (who created/edited/deleted/published what
    and when), so multiple admins sharing the system can see a history of changes."""

    __tablename__ = "admin_activity_log"

    id = db.Column(db.Integer, primary_key=True)
    admin_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    action = db.Column(db.String(50), nullable=False)
    # created_test | edited_test | deleted_test | published_test | unpublished_test |
    # duplicated_test | added_question | deleted_question | assigned_students |
    # unassigned_student | imported_questions | updated_eligibility |
    # activated_user | deactivated_user | deleted_user | imported_users | exported_users |
    # added_bank_item | edited_bank_item | deleted_bank_item | added_questions_from_bank |
    # saved_question_to_bank
    description = db.Column(db.String(500), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    admin = db.relationship("User")
