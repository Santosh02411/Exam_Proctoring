from datetime import datetime, timedelta

from flask import Blueprint, render_template, redirect, url_for, flash, request, current_app
from flask_login import login_user, logout_user, login_required, current_user

from app import db
from app.forms import RegisterForm, LoginForm, ForgotPasswordForm, ResetPasswordForm
from app.models import User, gen_user_id
from app.email_utils import generate_token, verify_token, send_email
from app.captcha import generate_captcha, verify_captcha
from app.utils import is_rate_limited

bp = Blueprint("auth", __name__)

EMAIL_VERIFY_SALT = "email-verify"
PASSWORD_RESET_SALT = "password-reset"

MAX_FAILED_ATTEMPTS = 5
LOCKOUT_MINUTES = 15


def _send_verification_email(user):
    token = generate_token(user.email, EMAIL_VERIFY_SALT)
    link = url_for("auth.verify_email", token=token, _external=True)
    mode = send_email(
        user.email,
        "Verify your Exam Proctoring account",
        f"Hi {user.name},\n\nPlease verify your email by visiting:\n{link}\n\nThis link expires in 24 hours.",
    )
    return link, mode


def _send_reset_email(user):
    token = generate_token(user.email, PASSWORD_RESET_SALT)
    link = url_for("auth.reset_password", token=token, _external=True)
    mode = send_email(
        user.email,
        "Reset your Exam Proctoring password",
        f"Hi {user.name},\n\nReset your password by visiting:\n{link}\n\nThis link expires in 1 hour.",
    )
    return link, mode


@bp.route("/register", methods=["GET", "POST"])
def register():
    if current_user.is_authenticated:
        return redirect(url_for("index"))

    form = RegisterForm()

    if request.method == "POST" and is_rate_limited(
        "register", current_app.config["REGISTER_MAX_PER_IP"], current_app.config["REGISTER_WINDOW_MINUTES"]
    ):
        flash("Too many registration attempts from this network. Please try again later.", "error")
        return render_template("auth/register.html", form=form)

    if form.validate_on_submit():
        existing = User.query.filter_by(email=form.email.data.lower().strip()).first()
        if existing:
            flash("Email already registered!", "error")
        else:
            user = User(
                user_id=gen_user_id(form.role.data),
                name=form.name.data.strip(),
                email=form.email.data.lower().strip(),
                phone=form.phone.data.strip(),
                role=form.role.data,
                status="active",
                email_verified=False,
            )
            user.set_password(form.password.data)
            db.session.add(user)
            db.session.commit()

            link, mode = _send_verification_email(user)
            flash(f"Registration successful as {form.role.data}! Please verify your email to log in.", "success")
            if mode == "logged":
                flash(f"(Dev mode — no SMTP configured) Verification link: {link}", "info")
            return redirect(url_for("auth.login"))
    return render_template("auth/register.html", form=form)


@bp.route("/verify-email/<token>")
def verify_email(token):
    email = verify_token(token, EMAIL_VERIFY_SALT, max_age_seconds=86400)
    if not email:
        flash("That verification link is invalid or has expired.", "error")
        return redirect(url_for("auth.login"))

    user = User.query.filter_by(email=email).first()
    if not user:
        flash("Account not found.", "error")
        return redirect(url_for("auth.login"))

    if user.email_verified:
        flash("Your email is already verified — you can log in.", "info")
    else:
        user.email_verified = True
        db.session.commit()
        flash("Email verified! You can now log in.", "success")
    return redirect(url_for("auth.login"))


@bp.route("/resend-verification", methods=["POST"])
def resend_verification():
    if is_rate_limited(
        "resend_verification",
        current_app.config["RESEND_VERIFICATION_MAX_PER_IP"],
        current_app.config["RESEND_VERIFICATION_WINDOW_MINUTES"],
    ):
        flash("Too many requests from this network. Please try again later.", "error")
        return redirect(url_for("auth.login"))

    email = request.form.get("email", "").lower().strip()
    user = User.query.filter_by(email=email).first()
    if user and not user.email_verified:
        link, mode = _send_verification_email(user)
        flash("Verification email sent.", "success")
        if mode == "logged":
            flash(f"(Dev mode — no SMTP configured) Verification link: {link}", "info")
    else:
        # Don't reveal whether the account exists.
        flash("If that account exists and is unverified, a new email has been sent.", "info")
    return redirect(url_for("auth.login"))


@bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("index"))

    form = LoginForm()

    if form.validate_on_submit():
        captcha_ok = verify_captcha(form.captcha_answer.data)
        user = User.query.filter_by(email=form.email.data.lower().strip()).first()

        if not captcha_ok:
            flash("Incorrect verification answer — please try again.", "error")
        elif user and user.locked_until and user.locked_until > datetime.utcnow():
            remaining = int((user.locked_until - datetime.utcnow()).total_seconds() // 60) + 1
            flash(f"Too many failed attempts. Try again in about {remaining} minute(s).", "error")
        elif not user:
            flash("No account found with that email!", "error")
        elif user.status != "active":
            flash("Account is not active!", "error")
        elif not user.check_password(form.password.data):
            user.failed_login_attempts += 1
            if user.failed_login_attempts >= MAX_FAILED_ATTEMPTS:
                user.locked_until = datetime.utcnow() + timedelta(minutes=LOCKOUT_MINUTES)
                user.failed_login_attempts = 0
                flash(f"Too many failed attempts. Account locked for {LOCKOUT_MINUTES} minutes.", "error")
            else:
                remaining_tries = MAX_FAILED_ATTEMPTS - user.failed_login_attempts
                flash(f"Invalid password! {remaining_tries} attempt(s) remaining before a temporary lock.", "error")
            db.session.commit()
        elif not user.email_verified:
            user.failed_login_attempts = 0
            db.session.commit()
            flash("Please verify your email before logging in.", "warning")
            captcha_question = generate_captcha()
            return render_template("auth/login.html", form=form, unverified_email=user.email, captcha_question=captcha_question)
        else:
            user.failed_login_attempts = 0
            user.locked_until = None
            db.session.commit()
            login_user(user)
            flash(f"Welcome back, {user.name}!", "success")
            next_url = request.args.get("next")
            if next_url:
                return redirect(next_url)
            return redirect(url_for("index"))

    captcha_question = generate_captcha()
    return render_template("auth/login.html", form=form, captcha_question=captcha_question)


@bp.route("/logout")
@login_required
def logout():
    logout_user()
    flash("You have been logged out.", "info")
    return redirect(url_for("auth.login"))


@bp.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    if current_user.is_authenticated:
        return redirect(url_for("index"))

    form = ForgotPasswordForm()

    if request.method == "POST" and is_rate_limited(
        "forgot_password",
        current_app.config["FORGOT_PASSWORD_MAX_PER_IP"],
        current_app.config["FORGOT_PASSWORD_WINDOW_MINUTES"],
    ):
        flash("Too many requests from this network. Please try again later.", "error")
        return render_template("auth/forgot_password.html", form=form)

    if form.validate_on_submit():
        user = User.query.filter_by(email=form.email.data.lower().strip()).first()
        if user:
            link, mode = _send_reset_email(user)
            flash("If that email is registered, a reset link has been sent.", "success")
            if mode == "logged":
                flash(f"(Dev mode — no SMTP configured) Reset link: {link}", "info")
        else:
            flash("If that email is registered, a reset link has been sent.", "success")
        return redirect(url_for("auth.login"))
    return render_template("auth/forgot_password.html", form=form)


@bp.route("/reset-password/<token>", methods=["GET", "POST"])
def reset_password(token):
    if current_user.is_authenticated:
        return redirect(url_for("index"))

    email = verify_token(token, PASSWORD_RESET_SALT, max_age_seconds=3600)
    if not email:
        flash("That reset link is invalid or has expired.", "error")
        return redirect(url_for("auth.forgot_password"))

    user = User.query.filter_by(email=email).first()
    if not user:
        flash("Account not found.", "error")
        return redirect(url_for("auth.forgot_password"))

    form = ResetPasswordForm()
    if form.validate_on_submit():
        user.set_password(form.password.data)
        db.session.commit()
        flash("Password updated — you can now log in.", "success")
        return redirect(url_for("auth.login"))
    return render_template("auth/reset_password.html", form=form)
