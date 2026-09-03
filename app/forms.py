import re

from flask_wtf import FlaskForm
from flask_wtf.file import FileField, FileRequired, FileAllowed
from wtforms import (
    StringField, PasswordField, SelectField, TextAreaField, IntegerField,
    DateTimeLocalField, HiddenField, BooleanField, FloatField
)
from wtforms.validators import DataRequired, Email, Length, Regexp, NumberRange, Optional, ValidationError, URL

PASSWORD_MIN_LENGTH = 8


def validate_password_complexity(form, field):
    """Require a mix of character classes, not just length, so a password
    like 'aaaaaaaa' doesn't pass just because it's 8+ characters."""
    password = field.data or ""
    missing = []
    if not re.search(r"[a-z]", password):
        missing.append("a lowercase letter")
    if not re.search(r"[A-Z]", password):
        missing.append("an uppercase letter")
    if not re.search(r"\d", password):
        missing.append("a number")
    if not re.search(r"[^A-Za-z0-9]", password):
        missing.append("a special character")
    if missing:
        raise ValidationError("Password must also include " + ", ".join(missing) + ".")


class RegisterForm(FlaskForm):
    name = StringField("Full Name", validators=[DataRequired(), Length(max=120)])
    email = StringField("Email", validators=[DataRequired(), Email(), Length(max=150)])
    phone = StringField("Mobile Number", validators=[DataRequired(), Regexp(r"^\d{10}$", message="Enter exactly 10 digits")])
    role = SelectField("Register as", choices=[
        ("student", "Student"), ("examiner", "Examiner"), ("proctor", "Proctor"), ("admin", "Admin"),
    ], validators=[DataRequired()])
    # Multi-tenancy: which Organization this account belongs to. Choices are
    # populated in the route (from the current list of active
    # Organizations, plus a "create a new one" option) since they're
    # DB-dependent. Optional here — a blank/omitted submission falls back to
    # the shared Default Organization (see app.auth._resolve_registration_org),
    # so this stays backward-compatible with anything that posts the older,
    # org-less registration form.
    org_choice = SelectField("Organization", validators=[Optional()])
    new_org_name = StringField("New organization name", validators=[Optional(), Length(max=150)])
    password = PasswordField("Password", validators=[
        DataRequired(),
        Length(min=PASSWORD_MIN_LENGTH, message=f"Min {PASSWORD_MIN_LENGTH} characters"),
        validate_password_complexity,
    ])
    accepted_terms = BooleanField(
        "I have read and agree to the Terms of Service and Privacy Policy",
        validators=[Optional()],
    )

    def validate_new_org_name(self, field):
        if self.org_choice.data != "__new__":
            return
        if self.role.data != "admin":
            raise ValidationError("Only an Admin account can create a new organization.")
        if not (field.data or "").strip():
            raise ValidationError("Enter a name for the new organization.")


class LoginForm(FlaskForm):
    email = StringField("Email", validators=[DataRequired(), Email()])
    password = PasswordField("Password", validators=[DataRequired()])
    captcha_answer = StringField("Verification", validators=[DataRequired()])


class OrganizationForm(FlaskForm):
    name = StringField("Organization Name", validators=[DataRequired(), Length(max=150)])
    status = SelectField("Status", choices=[("active", "Active"), ("inactive", "Inactive")], validators=[DataRequired()])


class BrandingForm(FlaskForm):
    """Split into two independent one-click actions in the UI (see
    admin.branding_settings / organizations.org_branding) rather than one
    combined submit: an HTML5 type="color" input can never be truly
    empty, so a single shared submit button would silently overwrite the
    org's color with black every time someone only meant to upload a logo
    (or vice versa)."""

    logo = FileField("Logo (PNG/JPG/SVG/WebP, max 2 MB)")
    primary_color = StringField(
        "Primary color", validators=[Optional(), Length(min=7, max=7)],
        render_kw={"placeholder": "#0b6ef6", "type": "color"},
    )


class ApiKeyForm(FlaskForm):
    """Create a new LMS/API integration key (see app.api_v1) for this
    organization. label is purely descriptive — helps the admin later
    tell which integration a key belongs to (e.g. "Moodle production")
    without needing to see the key value itself, which is never shown
    again after creation."""

    label = StringField("Label", validators=[DataRequired(), Length(min=2, max=120)])


class LmsWebhookForm(FlaskForm):
    """Optional outbound webhook URL (see Organization.lms_webhook_url /
    app.notifications._maybe_send_lms_webhook) — POSTed to whenever a
    result is published, so an LMS-side receiver doesn't have to poll
    GET /api/v1/tests/<code>/results itself."""

    lms_webhook_url = StringField("Webhook URL", validators=[Optional(), Length(max=500), URL()])


class CertificateSettingsForm(FlaskForm):
    """The signatory line shown on every certificate this org issues (see
    app.certificates) — applies org-wide, not per test, since it's
    normally the same institution/program lead signing every certificate
    regardless of which test it's for."""

    certificate_signatory_name = StringField("Signatory name", validators=[Optional(), Length(max=120)])
    certificate_signatory_title = StringField("Signatory title", validators=[Optional(), Length(max=120)])


class ProctoringPolicyForm(FlaskForm):
    """CSRF-only — the actual per-event-type action selects (see
    app.proctoring.POLICY_CONFIGURABLE_EVENT_TYPES) are rendered and
    parsed by hand in admin.proctoring_policy/update_proctoring_policy
    rather than as bound WTForms fields, since the field set is generated
    dynamically from that list rather than being fixed at class-definition
    time."""
    pass


class RetentionPolicyForm(FlaskForm):
    """Editable retention-window overrides — see app.retention. These six
    fields are the ones an organization's own admin can override; the
    platform-wide form (PlatformRetentionPolicyForm below) adds a seventh
    for the error log, which isn't tied to any one organization. Every
    field is optional: leaving it blank clears that override and falls
    back to the platform default (or the config.py default, for the
    platform form itself)."""

    recording_retention_days = IntegerField("Exam recordings (days)", validators=[Optional(), NumberRange(min=1)])
    snapshot_retention_days = IntegerField("Proctoring snapshots (days)", validators=[Optional(), NumberRange(min=1)])
    ended_login_session_retention_days = IntegerField("Ended login sessions (days)", validators=[Optional(), NumberRange(min=1)])
    login_security_event_retention_days = IntegerField("Login security events (days)", validators=[Optional(), NumberRange(min=1)])
    activity_log_retention_days = IntegerField("Admin activity log (days)", validators=[Optional(), NumberRange(min=1)])
    notification_log_retention_days = IntegerField("Notification log (days)", validators=[Optional(), NumberRange(min=1)])


class PlatformRetentionPolicyForm(RetentionPolicyForm):
    """The platform-wide (super_admin, org_id NULL) retention form — same
    six org-level fields, plus the error log, which only ever applies at
    the platform level (see app.retention/RetentionPolicy)."""

    error_log_retention_days = IntegerField("Error log (days)", validators=[Optional(), NumberRange(min=1)])


class ForgotPasswordForm(FlaskForm):
    email = StringField("Email", validators=[DataRequired(), Email()])


class ResetPasswordForm(FlaskForm):
    password = PasswordField("New Password", validators=[
        DataRequired(),
        Length(min=PASSWORD_MIN_LENGTH, message=f"Min {PASSWORD_MIN_LENGTH} characters"),
        validate_password_complexity,
    ])
    confirm_password = PasswordField("Confirm New Password", validators=[DataRequired()])

    def validate_confirm_password(self, field):
        if field.data != self.password.data:
            raise ValidationError("Passwords do not match.")


class TestForm(FlaskForm):
    test_code = StringField("Test Code", validators=[DataRequired(), Length(max=64)])
    title = StringField("Title", validators=[DataRequired(), Length(max=200)])
    description = TextAreaField("Description", validators=[Optional()])
    instructions = TextAreaField(
        "Instructions for students (optional)", validators=[Optional()]
    )
    duration_minutes = IntegerField("Duration (minutes)", validators=[DataRequired(), NumberRange(min=1)])
    total_questions = IntegerField("Total Questions", validators=[DataRequired(), NumberRange(min=1)])
    passing_marks = IntegerField("Passing Marks", validators=[DataRequired(), NumberRange(min=0)])
    status = SelectField("Status", choices=[("draft", "Draft"), ("published", "Published")])
    start_time = DateTimeLocalField("Start time (optional)", format="%Y-%m-%dT%H:%M", validators=[Optional()])
    end_time = DateTimeLocalField("End time (optional)", format="%Y-%m-%dT%H:%M", validators=[Optional()])
    max_attempts = IntegerField("Max attempts per student", default=1, validators=[DataRequired(), NumberRange(min=1, max=20)])
    randomize_questions = BooleanField("Shuffle question order per student", default=True)
    randomize_options = BooleanField("Shuffle answer-option order per student", default=True)
    negative_marks_per_wrong = FloatField("Negative marks per wrong answer", default=0.0, validators=[NumberRange(min=0)])
    allow_review = BooleanField("Let students review answers after submitting", default=True)
    partial_credit_multi = BooleanField(
        "Award partial credit on multiple-choice questions (instead of all-or-nothing)", default=False
    )
    question_pool_size = IntegerField(
        "Question pool size (optional)", validators=[Optional(), NumberRange(min=1)]
    )
    certificate_enabled = BooleanField(
        "Issue a downloadable certificate to students who pass this test", default=False
    )


class SectionForm(FlaskForm):
    name = StringField("Section name", validators=[DataRequired(), Length(max=150)])
    description = TextAreaField("Description (optional)", validators=[Optional()])
    duration_minutes = IntegerField(
        "Time limit for this section, in minutes (optional)", validators=[Optional(), NumberRange(min=1)]
    )


class QuestionForm(FlaskForm):
    question_type = SelectField(
        "Question type",
        choices=[("single", "Single choice (one correct answer)"),
                  ("multi", "Multiple choice (2+ correct answers)"),
                  ("true_false", "True / False"),
                  ("short", "Short answer (free text)"),
                  ("fill_blank", "Fill in the blank"),
                  ("descriptive", "Descriptive (essay — manually graded)"),
                  ("coding", "Coding (manually graded)")],
        default="single",
    )
    question_text = TextAreaField("Question text", validators=[DataRequired()])
    option_a = StringField("Option A", validators=[Optional(), Length(max=500)])
    option_b = StringField("Option B", validators=[Optional(), Length(max=500)])
    option_c = StringField("Option C", validators=[Optional(), Length(max=500)])
    option_d = StringField("Option D", validators=[Optional(), Length(max=500)])
    short_answer_text = StringField("Correct answer (short text)", validators=[Optional(), Length(max=500)])
    blank_answer = StringField(
        "Accepted answer(s) for the blank", validators=[Optional(), Length(max=500)]
    )
    model_answer = TextAreaField(
        "Model answer / grading notes (optional — for the grader only, never auto-compared)",
        validators=[Optional()],
    )
    starter_code = TextAreaField("Starter code (optional)", validators=[Optional()])
    code_language = StringField("Language (optional, e.g. python)", validators=[Optional(), Length(max=30)])
    marks = IntegerField("Marks", validators=[DataRequired(), NumberRange(min=1)])
    time_limit_seconds = IntegerField(
        "Time limit for this question, in seconds (optional)", validators=[Optional(), NumberRange(min=5)]
    )
    category = StringField("Category / topic (optional)", validators=[Optional(), Length(max=100)])
    difficulty = SelectField(
        "Difficulty", choices=[("easy", "Easy"), ("medium", "Medium"), ("hard", "Hard")], default="medium",
    )
    media_type = SelectField(
        "Attach media (optional)",
        choices=[("", "None"), ("image", "Image"), ("video", "Video")], default="",
        validators=[Optional()],
    )
    media_url = StringField("Media URL (optional — used if no file is uploaded)", validators=[Optional(), Length(max=500)])
    media_file = FileField(
        "Upload image/video (optional)",
        validators=[Optional(), FileAllowed(
            ["png", "jpg", "jpeg", "gif", "webp", "mp4", "webm", "ogg"], "Unsupported file type"
        )],
    )


class QuestionBankForm(QuestionForm):
    pass


class QuestionImportForm(FlaskForm):
    csv_file = FileField("CSV file", validators=[
        FileRequired(), FileAllowed(["csv"], "CSV files only"),
    ])


class UserImportForm(FlaskForm):
    csv_file = FileField("CSV file", validators=[
        FileRequired(), FileAllowed(["csv"], "CSV files only"),
    ])


class ProfileForm(FlaskForm):
    name = StringField("Full Name", validators=[DataRequired(), Length(max=120)])
    phone = StringField("Mobile Number", validators=[DataRequired(), Regexp(r"^\d{10}$", message="Enter exactly 10 digits")])


class ChangePasswordForm(FlaskForm):
    current_password = PasswordField("Current Password", validators=[DataRequired()])
    new_password = PasswordField("New Password", validators=[
        DataRequired(),
        Length(min=PASSWORD_MIN_LENGTH, message=f"Min {PASSWORD_MIN_LENGTH} characters"),
        validate_password_complexity,
    ])
    confirm_new_password = PasswordField("Confirm New Password", validators=[DataRequired()])

    def validate_confirm_new_password(self, field):
        if field.data != self.new_password.data:
            raise ValidationError("New passwords do not match.")
