import re

from flask_wtf import FlaskForm
from flask_wtf.file import FileField, FileRequired, FileAllowed
from wtforms import (
    StringField, PasswordField, SelectField, TextAreaField, IntegerField,
    DateTimeLocalField, HiddenField, BooleanField, FloatField
)
from wtforms.validators import DataRequired, Email, Length, Regexp, NumberRange, Optional, ValidationError

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
    role = SelectField("Register as", choices=[("student", "Student"), ("admin", "Admin")], validators=[DataRequired()])
    password = PasswordField("Password", validators=[
        DataRequired(),
        Length(min=PASSWORD_MIN_LENGTH, message=f"Min {PASSWORD_MIN_LENGTH} characters"),
        validate_password_complexity,
    ])


class LoginForm(FlaskForm):
    email = StringField("Email", validators=[DataRequired(), Email()])
    password = PasswordField("Password", validators=[DataRequired()])
    captcha_answer = StringField("Verification", validators=[DataRequired()])


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
    duration_minutes = IntegerField("Duration (minutes)", validators=[DataRequired(), NumberRange(min=1)])
    total_questions = IntegerField("Total Questions", validators=[DataRequired(), NumberRange(min=1)])
    passing_marks = IntegerField("Passing Marks", validators=[DataRequired(), NumberRange(min=0)])
    status = SelectField("Status", choices=[("draft", "Draft"), ("published", "Published")])
    start_time = DateTimeLocalField("Start time (optional)", format="%Y-%m-%dT%H:%M", validators=[Optional()])
    end_time = DateTimeLocalField("End time (optional)", format="%Y-%m-%dT%H:%M", validators=[Optional()])
    max_attempts = IntegerField("Max attempts per student", default=1, validators=[DataRequired(), NumberRange(min=1, max=20)])
    randomize_questions = BooleanField("Shuffle question & option order per student", default=True)
    negative_marks_per_wrong = FloatField("Negative marks per wrong answer", default=0.0, validators=[NumberRange(min=0)])
    allow_review = BooleanField("Let students review answers after submitting", default=True)
    partial_credit_multi = BooleanField(
        "Award partial credit on multiple-choice questions (instead of all-or-nothing)", default=False
    )


class QuestionForm(FlaskForm):
    question_type = SelectField(
        "Question type",
        choices=[("single", "Single choice (one correct answer)"),
                  ("multi", "Multiple choice (2+ correct answers)"),
                  ("short", "Short answer (free text)")],
        default="single",
    )
    question_text = TextAreaField("Question text", validators=[DataRequired()])
    option_a = StringField("Option A", validators=[Optional(), Length(max=500)])
    option_b = StringField("Option B", validators=[Optional(), Length(max=500)])
    option_c = StringField("Option C", validators=[Optional(), Length(max=500)])
    option_d = StringField("Option D", validators=[Optional(), Length(max=500)])
    short_answer_text = StringField("Correct answer (short text)", validators=[Optional(), Length(max=500)])
    marks = IntegerField("Marks", validators=[DataRequired(), NumberRange(min=1)])
    time_limit_seconds = IntegerField(
        "Time limit for this question, in seconds (optional)", validators=[Optional(), NumberRange(min=5)]
    )


class QuestionBankForm(QuestionForm):
    category = StringField("Category / topic (optional)", validators=[Optional(), Length(max=100)])


class QuestionImportForm(FlaskForm):
    csv_file = FileField("CSV file", validators=[
        FileRequired(), FileAllowed(["csv"], "CSV files only"),
    ])


class UserImportForm(FlaskForm):
    csv_file = FileField("CSV file", validators=[
        FileRequired(), FileAllowed(["csv"], "CSV files only"),
    ])
