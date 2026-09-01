import json
import uuid
from datetime import datetime

from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash

from app import db


def gen_user_id(role):
    return f"{role[:3].upper()}{uuid.uuid4().hex[:8].upper()}"


class Organization(db.Model):
    """A tenant. Every User (except a platform-level super_admin, who isn't
    tied to one) and every Test/QuestionBankItem belongs to exactly one
    Organization — that's the whole of the multi-tenant boundary: every
    org-scoped query in app.admin filters on this, and app.utils.
    ensure_same_org()/org_scope() are what actually enforce it route by
    route. Nothing about attempts, questions, proctoring events, etc. needs
    its own org_id — they all hang off a Test or a User that already has
    one, so isolation follows transitively."""

    __tablename__ = "organizations"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(150), unique=True, nullable=False)
    slug = db.Column(db.String(60), unique=True, nullable=False, index=True)
    status = db.Column(db.String(20), nullable=False, default="active")  # active | inactive
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    # Org-level branding/theming (see app.branding). Both optional — an
    # org with neither set just gets the platform's default look. The logo
    # file itself lives on disk under ORG_BRANDING_DIR, named by this
    # column; primary_color is a "#rrggbb" hex string applied as a CSS
    # custom-property override for that org's users.
    logo_filename = db.Column(db.String(120), nullable=True)
    primary_color = db.Column(db.String(7), nullable=True)

    # LMS/API Integrations (see app.api_v1, app.notifications). Optional —
    # when set, a JSON payload is POSTed here every time a result is
    # published for one of this org's tests, so an external LMS (or
    # middleware sitting in front of one — Zapier/Make, a custom Moodle/
    # Canvas plugin, etc.) can pick up the grade without having to poll
    # GET /api/v1/tests/<code>/results itself. Best-effort, same as the
    # Slack webhook in app.alerting — a broken/unreachable URL never blocks
    # or fails the result-publish flow it's attached to.
    lms_webhook_url = db.Column(db.String(500), nullable=True)

    # Certificate Generation (see app.certificates): the name/title shown
    # as the signatory line on every certificate issued for this org's
    # tests. Optional — a certificate renders fine without a signatory,
    # just without that line.
    certificate_signatory_name = db.Column(db.String(120), nullable=True)
    certificate_signatory_title = db.Column(db.String(120), nullable=True)

    users = db.relationship("User", backref="organization", lazy=True)
    tests = db.relationship("Test", backref="organization", lazy=True)

    def __repr__(self):
        return f"<Organization {self.name}>"


class User(UserMixin, db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.String(32), unique=True, nullable=False, default=lambda: gen_user_id("usr"))
    name = db.Column(db.String(120), nullable=False)
    email = db.Column(db.String(150), unique=True, nullable=False, index=True)
    phone = db.Column(db.String(15))
    password_hash = db.Column(db.String(255), nullable=False)
    # student | examiner | proctor | admin | super_admin. "admin" here means
    # an organization's own admin — scoped to org_id like everyone else — not
    # the platform-level role; that's super_admin, whose org_id is NULL.
    role = db.Column(db.String(20), nullable=False, default="student")
    status = db.Column(db.String(20), nullable=False, default="active")  # active | inactive
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    # NULL only for role == "super_admin" — every org-scoped role belongs to
    # exactly one Organization, assigned at registration (see app.auth).
    org_id = db.Column(db.Integer, db.ForeignKey("organizations.id"), nullable=True, index=True)

    email_verified = db.Column(db.Boolean, nullable=False, default=False)
    # 128-float face-api.js descriptor captured during face enrollment, stored as JSON text.
    # Used to verify the student's identity against the live webcam feed during a proctored test.
    face_descriptor = db.Column(db.Text, nullable=True)

    # Login rate-limiting / brute-force protection
    failed_login_attempts = db.Column(db.Integer, nullable=False, default=0)
    locked_until = db.Column(db.DateTime, nullable=True)

    # Terms of Service / Privacy Policy acceptance (see app.legal). Recorded
    # at registration — terms_version_accepted lets you tell who agreed to
    # which version if the policy text is later updated.
    terms_accepted_at = db.Column(db.DateTime, nullable=True)
    terms_version_accepted = db.Column(db.String(20), nullable=True)

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
    # Tenant this test belongs to — set from its creator's org at creation
    # and never changed afterward. Every admin.py query that lists or
    # fetches a Test is scoped to this (see app.utils.org_scope/ensure_same_org).
    org_id = db.Column(db.Integer, db.ForeignKey("organizations.id"), nullable=False, index=True)

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
    # Certificate Generation (see app.certificates): when on, a student who
    # passes this test can download a certificate from their result page.
    # Off by default — most tests (quizzes, practice runs) don't warrant
    # one, so this is an opt-in per test rather than automatic for every
    # passing score.
    certificate_enabled = db.Column(db.Boolean, nullable=False, default=False)
    # Partial credit for multi-select questions: award proportional marks based on
    # how many correct options were picked minus how many incorrect ones were,
    # instead of all-or-nothing. Doesn't affect single-choice or short-answer grading.
    partial_credit_multi = db.Column(db.Boolean, nullable=False, default=False)

    # Question pool: when set (and smaller than the test's total question
    # count), each student's attempt draws this many questions at random
    # from the full question set instead of taking all of them — a second,
    # independent layer of anti-collusion on top of randomize_questions'
    # order shuffling, since two students sitting side by side may not even
    # share the same question subset. NULL/0 means "no pooling" (every
    # student gets every question, as before). The subset is chosen
    # deterministically from the attempt's token (see app.randomize) so a
    # refresh/resume never reshuffles which questions a student already
    # started answering.
    question_pool_size = db.Column(db.Integer, nullable=True)

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
    # Tenant this bank item belongs to — set from its creator's org at
    # creation. Bank items are pulled into a specific test's question list
    # (see copies/bank_item below), so this only needs to gate the bank's
    # own listing/search/add/edit/delete — once copied into a Question it's
    # covered by that Question's Test's org_id like everything else.
    org_id = db.Column(db.Integer, db.ForeignKey("organizations.id"), nullable=False, index=True)
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

    # Persisted so the Review Queue can sort by risk at the database level
    # instead of recomputing a score for every attempt on every page load.
    # Recomputed and written by proctoring._record_violation() each time a
    # new violation lands — see compute_suspicion_score() for the formula.
    suspicion_score = db.Column(db.Integer, nullable=False, default=0)
    risk_level = db.Column(db.String(10), nullable=False, default="low")  # low | medium | high | critical

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

    # Cumulative per-question time-on-screen, in seconds (JSON: question_id
    # -> seconds), reported incrementally by the client via IntersectionObserver
    # (see proctor.js's question time tracker) and merged additively here on
    # every autosave/submit — see student._merge_question_time. This is a
    # "time visible on screen" measurement, not "time actively working on
    # it": a student who scrolls past a question and comes back later, or
    # leaves it open while thinking about something else, still accrues
    # time. Copied into each Answer.time_spent_seconds when the attempt is
    # finalized, for the per-question analytics breakdown.
    question_time_spent = db.Column(db.Text, nullable=True)

    # Set the first time this attempt's risk crosses into "high"/"critical"
    # (see app.notifications.maybe_send_high_risk_alert) — a one-shot flag
    # so a proctor/admin gets exactly one alert per attempt instead of one
    # per additional violation once it's already flagged as high-risk.
    high_risk_alert_sent = db.Column(db.Boolean, nullable=False, default=False)

    # ---- Exam Session Device Management (see app.exam_sessions) ----
    # device_info/ip_address describe whichever browser tab currently holds
    # this attempt (overwritten each time ownership changes — see
    # session_token below), for admins reviewing the attempt to see what it
    # was taken on. device_info is a small JSON object (user agent, and
    # anything richer the client chooses to report — screen size, timezone,
    # platform, language).
    device_info = db.Column(db.Text, nullable=True)
    ip_address = db.Column(db.String(45), nullable=True)
    # A fresh random token minted whenever a browser tab claims ownership
    # of this attempt (on start/resume — see exam_sessions.claim_session).
    # Every autosave/submit/heartbeat call from the client must present the
    # matching token (exam_sessions.validate_session_token) or it's treated
    # as a since-superseded tab and rejected — this is what actually stops
    # an older tab from continuing to save answers once a newer one has
    # taken over. NULL means no tab currently holds a claim (never
    # started, or explicitly released — see the /session/release beacon
    # fired on page unload/refresh).
    session_token = db.Column(db.String(64), nullable=True)
    session_started_at = db.Column(db.DateTime, nullable=True)
    # Refreshed on every accepted autosave/submit/heartbeat from the
    # owning tab; how "claim_session" tells a genuinely still-open tab
    # (recent) from a crashed one (stale — see EXAM_SESSION_STALE_AFTER_SECONDS)
    # that's safe to let a new tab/device take over.
    session_last_seen_at = db.Column(db.DateTime, nullable=True)
    # Identifies which *browser* (Flask login-session cookie, not tab —
    # see exam_sessions.get_browser_id) most recently claimed this attempt.
    # A claim from the same browser is always allowed through regardless
    # of staleness (an ordinary page refresh, or a second tab in the same
    # browser, isn't "a different device" trying to run the exam) — only a
    # claim from a *different* browser while the current one is still
    # fresh gets blocked as a concurrent session.
    session_owner_key = db.Column(db.String(64), nullable=True)

    answers = db.relationship("Answer", backref="attempt", cascade="all, delete-orphan", lazy=True)
    events = db.relationship("ProctoringEvent", backref="attempt", cascade="all, delete-orphan", lazy=True)

    def max_marks(self):
        """Maximum marks obtainable on this specific attempt. For a pooled
        test (Test.question_pool_size set), this is the total for just the
        subset of questions this attempt drew — which can differ between
        students — not the full question bank's total, so "score / max"
        stays meaningful per attempt. Falls back to the test's full total
        for attempts with no stored question_order (pre-dates pooling, or
        randomize_questions/pooling was never used)."""
        if not self.question_order:
            return self.test.total_marks()
        ids = set(json.loads(self.question_order))
        if not ids:
            return self.test.total_marks()
        return sum(q.marks for q in self.test.questions if q.id in ids) or 0


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

    # Seconds this question was visible on screen during the attempt — see
    # Attempt.question_time_spent for how it's collected. NULL for attempts
    # finalized before this existed, or if the client never reported any
    # (e.g. IntersectionObserver unsupported) — analytics treats NULL as
    # "no data" rather than zero.
    time_spent_seconds = db.Column(db.Integer, nullable=True)

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
    # audio_violation | copy_paste_attempt | window_blur | server_snapshot_flag |
    # phone_detected | book_detected | extra_person_detected | looking_away |
    # connection_lost | connection_restored |
    # identity_spotcheck_passed | identity_spotcheck_failed | liveness_check_failed |
    # laptop_detected | unauthorized_object_detected
    # (see app.proctoring.VALID_EVENT_TYPES for the authoritative set, and
    # proctor.js's object-detection section for what COCO-SSD can and can't
    # tell apart)
    severity = db.Column(db.String(20), nullable=False, default="warning")  # warning | violation
    details = db.Column(db.String(500))
    # Model confidence (0-1) for genuinely probabilistic detections only —
    # face-api.js's detection score for no_face/multiple_faces, COCO-SSD's
    # class score for phone/book/laptop/extra_person/unauthorized_object
    # detections, or a distance-derived
    # confidence for identity_mismatch. NULL for events that are direct
    # browser observations rather than model predictions (tab_hidden,
    # fullscreen_exit, dev_tools, copy_paste_attempt, window_blur,
    # audio_violation, connection_lost, connection_restored) — those are
    # certain by construction, so assigning them a fake confidence score
    # would be misleading rather than informative.
    confidence = db.Column(db.Float, nullable=True)
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
    # saved_question_to_bank | approved_id_document | rejected_id_document
    description = db.Column(db.String(500), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    admin = db.relationship("User")


class IdentityDocument(db.Model):
    """A student's uploaded government/college ID for enhanced identity
    verification (one active document per student — a re-upload replaces
    the previous file and resets review). Two independent things happen
    against this record, on two different tracks:

    1. Automatic face match + liveness (see app.proctoring.confirm_id_match,
       called right after upload): the browser extracts a face descriptor
       from the ID photo and a fresh live webcam capture (both via
       face-api.js) and the server recomputes their distance itself rather
       than trusting a client-reported number. A pass is what actually sets
       User.face_descriptor and lets the student take proctored tests — see
       student.start_test's existing "face_descriptor is None" gate.
    2. Admin document review (review_status below): a person separately
       checks the document itself is legible, the right type, and that the
       OCR fields look plausible against the roster. This can happen any
       time after upload and doesn't block the automatic path above — but
       an admin rejection revokes the student's enrollment (clears
       User.face_descriptor) since it means the underlying ID wasn't valid.
    """

    __tablename__ = "identity_documents"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, unique=True)
    doc_type = db.Column(db.String(20), nullable=False, default="government_id")  # government_id | college_id
    filename = db.Column(db.String(255), nullable=False)
    uploaded_at = db.Column(db.DateTime, default=datetime.utcnow)

    # Best-effort OCR text extraction (Tesseract) — a starting point for the
    # student to sanity-check and for an admin to cross-reference against
    # the roster during review. Never treated as an automated identity
    # decision by itself; parsing is regex-based and can miss or mis-split
    # fields on unusual ID layouts.
    ocr_raw_text = db.Column(db.Text, nullable=True)
    ocr_name = db.Column(db.String(200), nullable=True)
    ocr_id_number = db.Column(db.String(100), nullable=True)
    ocr_dob = db.Column(db.String(30), nullable=True)

    # Result of the automatic face-match + liveness check described above.
    face_match_distance = db.Column(db.Float, nullable=True)
    face_match_passed = db.Column(db.Boolean, nullable=True)
    liveness_passed = db.Column(db.Boolean, nullable=True)
    liveness_blink_count = db.Column(db.Integer, nullable=True)
    matched_at = db.Column(db.DateTime, nullable=True)

    review_status = db.Column(db.String(20), nullable=False, default="pending")  # pending | approved | rejected
    reviewed_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    reviewed_at = db.Column(db.DateTime, nullable=True)
    review_notes = db.Column(db.String(500), nullable=True)

    user = db.relationship("User", foreign_keys=[user_id])
    reviewer = db.relationship("User", foreign_keys=[reviewed_by_id])


class NotificationLog(db.Model):
    """A record of every notification the system has sent (or attempted to
    send) — the "notification history" half of the feature. Written by
    app.notifications.notify() regardless of whether the underlying email
    actually reached an inbox (see send_status), so this table is always the
    authoritative record of what was communicated to whom, independent of
    whatever's in instance/outbox.log for the dev SMTP fallback."""

    __tablename__ = "notification_log"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    # exam_scheduled | exam_starting_soon | exam_completed | result_published | high_risk_alert
    notif_type = db.Column(db.String(40), nullable=False)
    subject = db.Column(db.String(255), nullable=False)
    body_preview = db.Column(db.String(1000), nullable=False)
    channel = db.Column(db.String(20), nullable=False, default="email")
    send_status = db.Column(db.String(20), nullable=False, default="sent")  # sent | logged | failed
    test_id = db.Column(db.Integer, db.ForeignKey("tests.id"), nullable=True)
    attempt_id = db.Column(db.Integer, db.ForeignKey("attempts.id"), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    user = db.relationship("User", foreign_keys=[user_id])
    test = db.relationship("Test")
    attempt = db.relationship("Attempt")


class AnswerSimilarityFlag(db.Model):
    """One flagged pair of descriptive/coding answers to the same question
    from two different students, produced by app.similarity.run_check() —
    see that module for how the percentage is computed. Rows are only
    created for pairs at/above the threshold the check was run with, so
    the mere existence of a row is itself the "suspicious" signal; an
    examiner still has to look at the two answers side by side and decide
    (see review_status) since a high text match can also mean two students
    both memorized the same textbook definition or both used a standard
    coding idiom.

    (answer_a_id, answer_b_id) are stored with the lower id first so the
    same pair is never flagged twice by a re-run."""

    __tablename__ = "answer_similarity_flags"

    id = db.Column(db.Integer, primary_key=True)
    test_id = db.Column(db.Integer, db.ForeignKey("tests.id"), nullable=False)
    question_id = db.Column(db.Integer, db.ForeignKey("questions.id"), nullable=False)
    answer_a_id = db.Column(db.Integer, db.ForeignKey("answers.id"), nullable=False)
    answer_b_id = db.Column(db.Integer, db.ForeignKey("answers.id"), nullable=False)
    # descriptive | coding — carried over from the question, so the queue can
    # filter/label without a join every time.
    answer_type = db.Column(db.String(10), nullable=False)
    similarity_pct = db.Column(db.Float, nullable=False)
    # difflib sequence-match ratio on normalized text — see app.similarity.
    method = db.Column(db.String(20), nullable=False, default="sequence_match")
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    review_status = db.Column(db.String(20), nullable=False, default="pending")  # pending | confirmed | dismissed
    reviewed_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    reviewed_at = db.Column(db.DateTime, nullable=True)
    review_notes = db.Column(db.String(500), nullable=True)

    test = db.relationship("Test")
    question = db.relationship("Question")
    answer_a = db.relationship("Answer", foreign_keys=[answer_a_id])
    answer_b = db.relationship("Answer", foreign_keys=[answer_b_id])
    reviewer = db.relationship("User")

    __table_args__ = (db.UniqueConstraint("answer_a_id", "answer_b_id", name="uq_similarity_pair"),)


class LoginSession(db.Model):
    """One row per successful login, tracking the device/IP it came from and
    whether it's still the account's active session. See app.security for
    how this is used to (a) track device/IP per login, and (b) prevent
    simultaneous sessions: when SINGLE_SESSION_PER_ACCOUNT is on, a new
    login ends every other still-active session for the same user, and a
    before_request check logs out any browser tab still holding one of
    those now-ended sessions on its next request — so at most one session
    per account is ever actually usable at a time, even though the old
    tab's Flask-Login cookie is technically still present until then."""

    __tablename__ = "login_sessions"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    session_token = db.Column(db.String(64), unique=True, nullable=False)
    ip_address = db.Column(db.String(45), nullable=False)
    user_agent = db.Column(db.String(300), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    last_seen_at = db.Column(db.DateTime, default=datetime.utcnow)

    is_active = db.Column(db.Boolean, nullable=False, default=True)
    ended_at = db.Column(db.DateTime, nullable=True)
    # logout | replaced_by_new_login | expired
    end_reason = db.Column(db.String(30), nullable=True)

    user = db.relationship("User")


class LoginSecurityEvent(db.Model):
    """A notable event around account login/session activity, surfaced to
    admins on the security log page. Written by app.security — see that
    module for the detection logic behind each event_type. This is a
    lightweight signal for a human to look at, not an automated lockout:
    none of these currently block the login themselves (concurrent_session
    kick-out is handled separately via LoginSession.is_active)."""

    __tablename__ = "login_security_events"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    login_session_id = db.Column(db.Integer, db.ForeignKey("login_sessions.id"), nullable=True)
    # new_device | new_location | concurrent_session_replaced | vpn_or_proxy_suspected
    event_type = db.Column(db.String(40), nullable=False)
    details = db.Column(db.String(500), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)

    user = db.relationship("User")
    login_session = db.relationship("LoginSession")


class ErrorLog(db.Model):
    """One row per unhandled exception the app hits in production — written
    by the generic Exception handler in app.__init__ (see
    app.error_monitoring.log_error()), which logs and then still returns a
    normal 500 response, so this is purely observational and never changes
    request handling. HTTPException subclasses (404, 403, etc.) are
    ordinary control flow, not bugs, so they're deliberately never logged
    here — only genuinely unexpected exceptions are."""

    __tablename__ = "error_logs"

    id = db.Column(db.Integer, primary_key=True)
    occurred_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    endpoint = db.Column(db.String(150), nullable=True)
    method = db.Column(db.String(10), nullable=True)
    path = db.Column(db.String(300), nullable=True)
    error_type = db.Column(db.String(150), nullable=False)
    error_message = db.Column(db.String(1000), nullable=True)
    traceback = db.Column(db.Text, nullable=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    resolved = db.Column(db.Boolean, nullable=False, default=False)

    user = db.relationship("User")


class RetentionPolicy(db.Model):
    """One retention-policy override — either platform-wide (org_id NULL,
    editable only by super_admin from /ops/retention) or scoped to a
    single organization (editable by that org's own admin from
    /admin/retention, or by super_admin from that org's detail page).
    Any field left NULL falls through to the org's row (if this is the
    platform row, or if the org itself has no override) and ultimately to
    the *_RETENTION_DAYS setting in config.py — see
    app.retention.effective_days(), which is the only place that resolves
    this three-level fallback. error_log_retention_days only ever applies
    on the platform row (org_id NULL): errors aren't tied to any one
    tenant's data, so there's nothing for an org-level override to mean
    here — the org-facing form simply never exposes that field.

    There is at most one row per org_id (including at most one row with
    org_id NULL) — enforced by application logic (get-or-create) rather
    than a DB unique constraint, since a UNIQUE constraint's treatment of
    multiple NULLs isn't reliable across database engines."""

    __tablename__ = "retention_policies"

    id = db.Column(db.Integer, primary_key=True)
    org_id = db.Column(db.Integer, db.ForeignKey("organizations.id"), nullable=True, index=True)

    recording_retention_days = db.Column(db.Integer, nullable=True)
    snapshot_retention_days = db.Column(db.Integer, nullable=True)
    ended_login_session_retention_days = db.Column(db.Integer, nullable=True)
    login_security_event_retention_days = db.Column(db.Integer, nullable=True)
    activity_log_retention_days = db.Column(db.Integer, nullable=True)
    notification_log_retention_days = db.Column(db.Integer, nullable=True)
    # Platform row only (org_id is NULL) — see class docstring.
    error_log_retention_days = db.Column(db.Integer, nullable=True)

    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    updated_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)

    organization = db.relationship("Organization")
    updated_by = db.relationship("User")


class Certificate(db.Model):
    """Issuance record for a passing attempt's certificate (see
    app.certificates) — the PDF itself is generated on demand each time
    it's requested rather than stored on disk (cheap to regenerate,
    avoids another category of file to retain/clean up), but this row
    persists so the same certificate_code always resolves to the same
    attempt for public verification (see app.student.verify_certificate),
    and so a re-download doesn't mint a new code every time."""

    __tablename__ = "certificates"

    id = db.Column(db.Integer, primary_key=True)
    attempt_id = db.Column(db.Integer, db.ForeignKey("attempts.id"), unique=True, nullable=False)
    certificate_code = db.Column(db.String(32), unique=True, nullable=False, index=True)
    issued_at = db.Column(db.DateTime, default=datetime.utcnow)

    attempt = db.relationship("Attempt", backref=db.backref("certificate", uselist=False))


class ApiKey(db.Model):
    """A credential for external systems — an institutional LMS plugin
    (Moodle/Canvas/Google Classroom), integration middleware, or a custom
    in-house connector — to call this organization's API (see app.api_v1)
    without a human login. Exactly like a user's password, only the salted
    hash is ever stored; the raw key is shown to the admin once, at
    creation time, and never again. `prefix` is a short, non-secret slice
    of the raw key kept in the clear purely so the admin's key list (and
    the lookup in app.api_v1.require_api_key) can identify a key without
    needing the full value — the same "abc123...def" idea GitHub/Stripe
    tokens use."""

    __tablename__ = "api_keys"

    id = db.Column(db.Integer, primary_key=True)
    org_id = db.Column(db.Integer, db.ForeignKey("organizations.id"), nullable=False, index=True)
    label = db.Column(db.String(120), nullable=False)
    key_hash = db.Column(db.String(255), nullable=False)
    prefix = db.Column(db.String(16), nullable=False, index=True)
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    last_used_at = db.Column(db.DateTime, nullable=True)
    revoked = db.Column(db.Boolean, nullable=False, default=False)
    revoked_at = db.Column(db.DateTime, nullable=True)

    organization = db.relationship("Organization")
    created_by = db.relationship("User")


class SystemAlert(db.Model):
    """A real-time system health alert — disk usage crossing its
    threshold, or the error rate spiking — written by app.alerting. See
    that module for the actual detection logic and notification delivery
    (email to every super_admin, plus an optional Slack webhook).

    Alerts are deduplicated by "is there already an unresolved alert of
    this type": app.alerting only creates (and notifies for) a new one
    once the previous one of the same type has been marked resolved, so a
    sustained problem doesn't re-notify on every check."""

    __tablename__ = "system_alerts"

    id = db.Column(db.Integer, primary_key=True)
    # disk_usage_high | error_rate_spike
    alert_type = db.Column(db.String(40), nullable=False)
    severity = db.Column(db.String(20), nullable=False, default="warning")  # warning | critical
    message = db.Column(db.String(500), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    notified_at = db.Column(db.DateTime, nullable=True)
    resolved = db.Column(db.Boolean, nullable=False, default=False)
    resolved_at = db.Column(db.DateTime, nullable=True)
