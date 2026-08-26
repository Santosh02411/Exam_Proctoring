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
    role = db.Column(db.String(20), nullable=False, default="student")  # student | admin
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
    # Anti-collusion: shuffle question order and per-question option order per attempt
    randomize_questions = db.Column(db.Boolean, nullable=False, default=True)
    # Negative marking: points deducted per wrong answer (0 disables it)
    negative_marks_per_wrong = db.Column(db.Float, nullable=False, default=0.0)
    # Whether students can review their answers (with correct answers shown) after submitting
    allow_review = db.Column(db.Boolean, nullable=False, default=True)

    questions = db.relationship("Question", backref="test", cascade="all, delete-orphan", lazy=True)
    eligibility = db.relationship("TestEligibility", backref="test", cascade="all, delete-orphan", lazy=True)
    attempts = db.relationship("Attempt", backref="test", cascade="all, delete-orphan", lazy=True)

    def total_marks(self):
        return sum(q.marks for q in self.questions) or 0

    def is_open_now(self):
        now = datetime.utcnow()
        if self.start_time and now < self.start_time:
            return False
        if self.end_time and now > self.end_time:
            return False
        return True


class Question(db.Model):
    __tablename__ = "questions"

    id = db.Column(db.Integer, primary_key=True)
    test_id = db.Column(db.Integer, db.ForeignKey("tests.id"), nullable=False)
    question_text = db.Column(db.Text, nullable=False)
    option_a = db.Column(db.String(500), nullable=False)
    option_b = db.Column(db.String(500), nullable=False)
    option_c = db.Column(db.String(500), nullable=False)
    option_d = db.Column(db.String(500), nullable=False)
    correct_answer = db.Column(db.String(1), nullable=False)  # a|b|c|d
    marks = db.Column(db.Integer, nullable=False, default=1)


class TestEligibility(db.Model):
    __tablename__ = "test_eligibility"

    id = db.Column(db.Integer, primary_key=True)
    test_id = db.Column(db.Integer, db.ForeignKey("tests.id"), nullable=False)
    student_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    assigned_at = db.Column(db.DateTime, default=datetime.utcnow)

    student = db.relationship("User")

    # Accessibility accommodation: extra minutes added to the test duration for this student
    extra_time_minutes = db.Column(db.Integer, nullable=False, default=0)

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

    answers = db.relationship("Answer", backref="attempt", cascade="all, delete-orphan", lazy=True)
    events = db.relationship("ProctoringEvent", backref="attempt", cascade="all, delete-orphan", lazy=True)


class Answer(db.Model):
    __tablename__ = "answers"

    id = db.Column(db.Integer, primary_key=True)
    attempt_id = db.Column(db.Integer, db.ForeignKey("attempts.id"), nullable=False)
    question_id = db.Column(db.Integer, db.ForeignKey("questions.id"), nullable=False)
    selected_option = db.Column(db.String(1), nullable=True)  # a|b|c|d or NULL if unanswered

    question = db.relationship("Question")

    __table_args__ = (db.UniqueConstraint("attempt_id", "question_id", name="uq_attempt_question"),)


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


class AdminActivityLog(db.Model):
    """Audit trail of admin actions (who created/edited/deleted/published what
    and when), so multiple admins sharing the system can see a history of changes."""

    __tablename__ = "admin_activity_log"

    id = db.Column(db.Integer, primary_key=True)
    admin_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    action = db.Column(db.String(50), nullable=False)
    # created_test | edited_test | deleted_test | published_test | unpublished_test |
    # duplicated_test | added_question | deleted_question | assigned_students |
    # unassigned_student | imported_questions
    description = db.Column(db.String(500), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    admin = db.relationship("User")
