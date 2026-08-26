from flask_wtf import FlaskForm
from flask_wtf.file import FileField, FileRequired, FileAllowed
from wtforms import (
    StringField, PasswordField, SelectField, TextAreaField, IntegerField,
    DateTimeLocalField, HiddenField, BooleanField, FloatField
)
from wtforms.validators import DataRequired, Email, Length, Regexp, NumberRange, Optional, ValidationError


class RegisterForm(FlaskForm):
    name = StringField("Full Name", validators=[DataRequired(), Length(max=120)])
    email = StringField("Email", validators=[DataRequired(), Email(), Length(max=150)])
    phone = StringField("Mobile Number", validators=[DataRequired(), Regexp(r"^\d{10}$", message="Enter exactly 10 digits")])
    role = SelectField("Register as", choices=[("student", "Student"), ("admin", "Admin")], validators=[DataRequired()])
    password = PasswordField("Password", validators=[DataRequired(), Length(min=6, message="Min 6 characters")])


class LoginForm(FlaskForm):
    email = StringField("Email", validators=[DataRequired(), Email()])
    password = PasswordField("Password", validators=[DataRequired()])
    captcha_answer = StringField("Verification", validators=[DataRequired()])


class ForgotPasswordForm(FlaskForm):
    email = StringField("Email", validators=[DataRequired(), Email()])


class ResetPasswordForm(FlaskForm):
    password = PasswordField("New Password", validators=[DataRequired(), Length(min=6, message="Min 6 characters")])
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


class QuestionForm(FlaskForm):
    question_text = TextAreaField("Question text", validators=[DataRequired()])
    option_a = StringField("Option A", validators=[DataRequired(), Length(max=500)])
    option_b = StringField("Option B", validators=[DataRequired(), Length(max=500)])
    option_c = StringField("Option C", validators=[DataRequired(), Length(max=500)])
    option_d = StringField("Option D", validators=[DataRequired(), Length(max=500)])
    correct_answer = SelectField("Correct answer", choices=[("a", "A"), ("b", "B"), ("c", "C"), ("d", "D")])
    marks = IntegerField("Marks", validators=[DataRequired(), NumberRange(min=1)])


class QuestionImportForm(FlaskForm):
    csv_file = FileField("CSV file", validators=[
        FileRequired(), FileAllowed(["csv"], "CSV files only"),
    ])
