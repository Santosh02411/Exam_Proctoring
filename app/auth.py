import re
from datetime import datetime, timedelta

from flask import Blueprint, render_template, redirect, url_for, flash, request, current_app, session
from flask_login import login_user, logout_user, login_required, current_user

from app import db
from app.forms import (
    RegisterForm, LoginForm, ForgotPasswordForm, ResetPasswordForm, TwoFactorLoginForm, OtpForm,
    RequestOrganizationForm, AcceptInvitationForm,
)
from app import invitations
from app.models import User, Organization, gen_user_id
from app.email_utils import send_email
from app.captcha import generate_captcha, verify_captcha
from app.utils import is_rate_limited
from app import security
from app import twofa
from app import otp

bp = Blueprint("auth", __name__)

# Session keys for the two OTP flows (see app.otp).
PENDING_VERIFY_EMAIL_KEY = "pending_verify_email"
PENDING_RESET_EMAIL_KEY = "pw_reset_email"
RESET_OTP_ID_KEY = "pw_reset_otp_id"

MAX_FAILED_ATTEMPTS = 5
LOCKOUT_MINUTES = 15
DEFAULT_ORG_SLUG = "default"


def _slugify(name):
    slug = re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")
    return slug or "org"


def _get_or_create_default_org():
    """The shared fallback tenant for a registration that doesn't specify
    an organization — keeps registration working exactly as before
    multi-tenancy existed for anything that posts the older, org-less
    form (including every existing integration/test)."""
    org = Organization.query.filter_by(slug=DEFAULT_ORG_SLUG).first()
    if not org:
        org = Organization(name="Default Organization", slug=DEFAULT_ORG_SLUG, status="active")
        db.session.add(org)
        db.session.flush()
    return org


def _unique_slug(name):
    base = _slugify(name)
    slug = base
    i = 2
    while Organization.query.filter_by(slug=slug).first():
        slug = f"{base}-{i}"
        i += 1
    return slug


def _resolve_registration_org(form):
    """The Organization a self-registering STUDENT joins: the chosen active
    organization, or the shared Default Organization if none was chosen.
    (Creating an organization is a separate, approval-gated flow — see
    request_organization — and staff accounts are invitation-only.)"""
    choice = (form.org_choice.data or "").strip()
    if choice.isdigit():
        org = Organization.query.get(int(choice))
        if org and org.status == "active":
            return org
    return _get_or_create_default_org()


def _org_choices():
    return [
        (str(o.id), o.name)
        for o in Organization.query.filter_by(status="active").order_by(Organization.name).all()
    ]


def _dev_echo(mode, text):
    """Show an emailed secret on screen — ONLY when no SMTP is configured AND
    the app is running in development/testing. In production this is never
    allowed (see app.config_validation): echoing a reset/verification code to
    whoever typed an email address would defeat the point of emailing it."""
    from app.config_validation import dev_mail_fallback_allowed

    if mode == "logged" and dev_mail_fallback_allowed(current_app.config):
        flash(f"(Dev mode — no SMTP configured) {text}", "info")


def _send_verification_email(user):
    """Email a fresh 6-digit verification code. Returns (code, mode) —
    mode is "logged" when no SMTP is configured (dev fallback)."""
    code = otp.issue_otp(user.email, otp.PURPOSE_VERIFY)
    mode = send_email(
        user.email,
        "Your Exam Proctoring verification code",
        otp.otp_email_body(user.name, otp.PURPOSE_VERIFY, code),
    )
    return code, mode


def _send_reset_email(user):
    """Email a fresh 6-digit password-reset code. Returns (code, mode)."""
    code = otp.issue_otp(user.email, otp.PURPOSE_RESET)
    mode = send_email(
        user.email,
        "Your Exam Proctoring password reset code",
        otp.otp_email_body(user.name, otp.PURPOSE_RESET, code),
    )
    return code, mode


@bp.route("/register", methods=["GET", "POST"])
def register():
    """Public self-registration. Creates STUDENT accounts only. Any attempt to
    pick a privileged role (a hand-edited form field) is refused outright —
    admin/examiner/proctor accounts exist only via staff invitations, and
    new organizations only via the approval-gated request flow."""
    if current_user.is_authenticated:
        return redirect(url_for("index"))

    form = RegisterForm()
    form.org_choice.choices = _org_choices()

    if request.method == "POST":
        requested_role = (request.form.get("role") or "student").strip().lower()
        if requested_role != "student":
            flash(
                "Public registration is for students only. Admin, examiner and proctor accounts are "
                "created by invitation from your organization's administrator.",
                "error",
            )
            return render_template("auth/register.html", form=form), 403
        if is_rate_limited(
            "register", current_app.config["REGISTER_MAX_PER_IP"], current_app.config["REGISTER_WINDOW_MINUTES"]
        ):
            flash("Too many registration attempts from this network. Please try again later.", "error")
            return render_template("auth/register.html", form=form)

    if form.validate_on_submit():
        existing = User.query.filter_by(email=form.email.data.lower().strip()).first()
        if existing:
            flash("Email already registered!", "error")
        else:
            org = _resolve_registration_org(form)
            user = User(
                user_id=gen_user_id("student"),
                name=form.name.data.strip(),
                email=form.email.data.lower().strip(),
                phone=form.phone.data.strip(),
                role="student",
                status="active",
                email_verified=False,
                org_id=org.id,
            )
            user.set_password(form.password.data)
            if form.accepted_terms.data:
                user.terms_accepted_at = datetime.utcnow()
                user.terms_version_accepted = current_app.config["TERMS_VERSION"]
            db.session.add(user)
            db.session.commit()

            code, mode = _send_verification_email(user)
            session[PENDING_VERIFY_EMAIL_KEY] = user.email
            flash(
                f"Registration successful under {org.name}! "
                "We emailed you a 6-digit code — enter it below to verify your email.",
                "success",
            )
            _dev_echo(mode, f"Verification code: {code}")
            return redirect(url_for("auth.verify_email"))
    return render_template("auth/register.html", form=form)


@bp.route("/request-organization", methods=["GET", "POST"])
def request_organization():
    """Ask for a NEW organization. Nothing here grants any access: it creates
    a *pending* organization plus a pending administrator account, neither of
    which can do anything until a platform super_admin approves the request
    (organizations.approve_organization)."""
    if current_user.is_authenticated:
        return redirect(url_for("index"))
    form = RequestOrganizationForm()

    if request.method == "POST" and is_rate_limited(
        "register", current_app.config["REGISTER_MAX_PER_IP"], current_app.config["REGISTER_WINDOW_MINUTES"]
    ):
        flash("Too many requests from this network. Please try again later.", "error")
        return render_template("auth/request_organization.html", form=form)

    if form.validate_on_submit():
        email = form.email.data.lower().strip()
        org_name = form.org_name.data.strip()
        if User.query.filter_by(email=email).first():
            flash("Email already registered!", "error")
        elif Organization.query.filter_by(name=org_name).first():
            flash("An organization with that name already exists or has already been requested.", "error")
        else:
            org = Organization(name=org_name, slug=_unique_slug(org_name), status="pending")
            db.session.add(org)
            db.session.flush()
            user = User(
                user_id=gen_user_id("admin"), name=form.name.data.strip(), email=email,
                phone=form.phone.data.strip(), role="admin",
                status="pending_approval",      # cannot log in until the org is approved
                email_verified=False, org_id=org.id,
            )
            user.set_password(form.password.data)
            if form.accepted_terms.data:
                user.terms_accepted_at = datetime.utcnow()
                user.terms_version_accepted = current_app.config["TERMS_VERSION"]
            db.session.add(user)
            db.session.commit()

            _notify_super_admins_of_request(org, user)
            code, mode = _send_verification_email(user)
            session[PENDING_VERIFY_EMAIL_KEY] = user.email
            flash(
                f"Request received for '{org.name}'. Verify your email with the code we sent; a platform "
                "administrator must then approve the organization before you can log in.",
                "success",
            )
            _dev_echo(mode, f"Verification code: {code}")
            return redirect(url_for("auth.verify_email"))
    return render_template("auth/request_organization.html", form=form)


def _notify_super_admins_of_request(org, user):
    """Best-effort email to every active super_admin about a pending request."""
    try:
        for sa in User.query.filter_by(role="super_admin", status="active").all():
            send_email(
                sa.email,
                f"Organization approval requested: {org.name}",
                f"{user.name} <{user.email}> has asked to create the organization '{org.name}'.\n\n"
                f"Review it under Organizations → Pending approval:\n"
                f"{url_for('organizations.list_organizations', _external=True)}",
            )
    except Exception:
        current_app.logger.exception("could not notify super admins of organization request")


@bp.route("/invite/<token>", methods=["GET", "POST"])
def accept_invitation(token):
    """Accept a staff invitation: choose name/phone/password. The role and
    organization are whatever the invitation says — nothing about them is
    read from this form. Single use; expires; revocable."""
    if current_user.is_authenticated:
        flash("You are already logged in. Log out to accept an invitation.", "info")
        return redirect(url_for("index"))
    if request.method == "POST" and is_rate_limited(
        "accept_invite", current_app.config.get("INVITE_ACCEPT_MAX_PER_IP", 20), 15
    ):
        flash("Too many attempts from this network. Please try again later.", "error")
        return redirect(url_for("auth.login"))

    invitation = invitations.find_open(token)
    if not invitation:
        flash("This invitation link is invalid, expired or has already been used. Ask your administrator for a new one.", "error")
        return redirect(url_for("auth.login"))

    form = AcceptInvitationForm()
    if form.validate_on_submit():
        try:
            user = invitations.accept(
                invitation, form.name.data, form.phone.data, form.password.data, bool(form.accepted_terms.data)
            )
        except invitations.InvitationError as exc:
            flash(str(exc), "error")
            return redirect(url_for("auth.login"))
        flash(f"Welcome, {user.name}! Your {invitations.ROLE_LABELS[user.role]} account is ready — log in.", "success")
        return redirect(url_for("auth.login"))
    return render_template("auth/accept_invitation.html", form=form, invitation=invitation)


@bp.route("/verify-email", methods=["GET", "POST"])
def verify_email():
    """Enter the 6-digit code emailed at registration (or via "Send
    verification code" on the login page) to verify the account's email."""
    email = session.get(PENDING_VERIFY_EMAIL_KEY) or request.form.get("email", "").lower().strip()
    if not email:
        flash("Enter your email on the login page to get a verification code.", "info")
        return redirect(url_for("auth.login"))

    form = OtpForm()
    if form.validate_on_submit():
        result, _row = otp.verify_otp(email, otp.PURPOSE_VERIFY, form.code.data)
        if result == otp.OK:
            user = User.query.filter_by(email=email).first()
            session.pop(PENDING_VERIFY_EMAIL_KEY, None)
            if not user:
                flash("Account not found.", "error")
                return redirect(url_for("auth.login"))
            if not user.email_verified:
                user.email_verified = True
                db.session.commit()
            flash("Email verified! You can now log in.", "success")
            return redirect(url_for("auth.login"))
        flash(otp.failure_message(result), "error")
    return render_template("auth/verify_email_otp.html", form=form, email=email)


@bp.route("/verify-email/<token>")
def verify_email_legacy(token):
    """Old emailed verification links — verification is by 6-digit code now."""
    flash("Email verification now uses a 6-digit code. Log in or register to get a new one.", "info")
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
        wait = otp.seconds_until_resend(user.email, otp.PURPOSE_VERIFY)
        session[PENDING_VERIFY_EMAIL_KEY] = user.email
        if wait:
            flash(f"A code was just sent. You can request another in {wait} second(s).", "info")
        else:
            code, mode = _send_verification_email(user)
            flash("Verification code sent to your email.", "success")
            _dev_echo(mode, f"Verification code: {code}")
        return redirect(url_for("auth.verify_email"))
    # Don't reveal whether the account exists.
    flash("If that account exists and is unverified, a new code has been sent.", "info")
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
        elif user.status == "pending_approval":
            flash("Your organization request is awaiting approval by a platform administrator.", "warning")
        elif user.status != "active":
            flash("Account is not active!", "error")
        elif user.org_id is not None and getattr(db.session.get(Organization, user.org_id), "status", "active") in ("pending", "rejected"):
            flash("Your organization has not been approved.", "error")
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
            if twofa.requires_2fa(user):
                # Don't call login_user() yet — the password alone isn't
                # enough for this account. Stash just the user id (not
                # "logged in" in any Flask-Login sense) and send them to
                # the second step; nothing about this session is
                # authenticated until verify_login_code succeeds.
                session[twofa.PENDING_2FA_SESSION_KEY] = user.id
                session["pending_2fa_next"] = request.args.get("next")
                return redirect(url_for("auth.login_verify_2fa"))
            session.pop("impersonate_org_id", None)
            login_user(user)
            security.register_login(user)
            flash(f"Welcome back, {user.name}!", "success")
            next_url = request.args.get("next")
            if next_url:
                return redirect(next_url)
            return redirect(url_for("index"))

    captcha_question = generate_captcha()
    return render_template("auth/login.html", form=form, captcha_question=captcha_question)


@bp.route("/login/verify-2fa", methods=["GET", "POST"])
def login_verify_2fa():
    """The second step of login for an account with 2FA enabled — reached
    only after auth.login's password+captcha check already succeeded and
    stashed a pending user id in the session (see twofa.PENDING_2FA_SESSION_KEY).
    Accepts either a live 6-digit authenticator code or a one-time backup
    code in the same field."""
    user_id = session.get(twofa.PENDING_2FA_SESSION_KEY)
    if not user_id:
        return redirect(url_for("auth.login"))
    user = User.query.get(user_id)
    if not user or not twofa.requires_2fa(user):
        session.pop(twofa.PENDING_2FA_SESSION_KEY, None)
        return redirect(url_for("auth.login"))

    form = TwoFactorLoginForm()
    if form.validate_on_submit():
        code = form.code.data.strip()
        ok = twofa.verify_code(user.totp_secret, code) or twofa.consume_backup_code(user, code)
        if ok:
            session.pop(twofa.PENDING_2FA_SESSION_KEY, None)
            next_url = session.pop("pending_2fa_next", None)
            session.pop("impersonate_org_id", None)
            login_user(user)
            security.register_login(user)
            flash(f"Welcome back, {user.name}!", "success")
            return redirect(next_url or url_for("index"))
        flash("Invalid or expired code. Please try again.", "error")

    return render_template("auth/verify_2fa.html", form=form)


@bp.route("/logout")
@login_required
def logout():
    security.end_session(reason="logout")
    logout_user()
    session.pop("impersonate_org_id", None)
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
        email = form.email.data.lower().strip()
        user = User.query.filter_by(email=email).first()
        # Same response and same next page whether or not the account
        # exists, so this can't be used to probe which emails are registered.
        if user and not otp.seconds_until_resend(user.email, otp.PURPOSE_RESET):
            code, mode = _send_reset_email(user)
            _dev_echo(mode, f"Reset code: {code}")
        session[PENDING_RESET_EMAIL_KEY] = email
        session.pop(RESET_OTP_ID_KEY, None)
        flash("If that email is registered, a 6-digit reset code has been sent.", "success")
        return redirect(url_for("auth.reset_password"))
    return render_template("auth/forgot_password.html", form=form)


@bp.route("/forgot-password/resend", methods=["POST"])
def resend_reset_code():
    if current_user.is_authenticated:
        return redirect(url_for("index"))
    email = session.get(PENDING_RESET_EMAIL_KEY)
    if not email:
        return redirect(url_for("auth.forgot_password"))
    if is_rate_limited(
        "forgot_password",
        current_app.config["FORGOT_PASSWORD_MAX_PER_IP"],
        current_app.config["FORGOT_PASSWORD_WINDOW_MINUTES"],
    ):
        flash("Too many requests from this network. Please try again later.", "error")
        return redirect(url_for("auth.reset_password"))
    user = User.query.filter_by(email=email).first()
    if user:
        wait = otp.seconds_until_resend(user.email, otp.PURPOSE_RESET)
        if wait:
            flash(f"A code was just sent. You can request another in {wait} second(s).", "info")
            return redirect(url_for("auth.reset_password"))
        code, mode = _send_reset_email(user)
        _dev_echo(mode, f"Reset code: {code}")
    session.pop(RESET_OTP_ID_KEY, None)
    flash("If that email is registered, a new 6-digit code has been sent.", "success")
    return redirect(url_for("auth.reset_password"))


@bp.route("/reset-password", methods=["GET", "POST"])
def reset_password():
    """Two steps on one page: (1) enter the emailed 6-digit code; once it
    checks out, (2) the same page shows the new-password form."""
    if current_user.is_authenticated:
        return redirect(url_for("index"))

    email = session.get(PENDING_RESET_EMAIL_KEY)
    if not email:
        flash("Enter your email to get a reset code.", "info")
        return redirect(url_for("auth.forgot_password"))

    verified_row = otp.is_redeemable(session.get(RESET_OTP_ID_KEY), email, otp.PURPOSE_RESET)

    if verified_row is None:
        # Step 1 — code entry.
        form = OtpForm()
        if form.validate_on_submit():
            result, row = otp.verify_otp(email, otp.PURPOSE_RESET, form.code.data)
            if result == otp.OK:
                session[RESET_OTP_ID_KEY] = row.id
                flash("Code verified — choose a new password.", "success")
                return redirect(url_for("auth.reset_password"))
            flash(otp.failure_message(result), "error")
        return render_template("auth/reset_password.html", stage="otp", otp_form=form, email=email)

    # Step 2 — code already verified; set the new password.
    user = User.query.filter_by(email=email).first()
    if not user:
        session.pop(PENDING_RESET_EMAIL_KEY, None)
        session.pop(RESET_OTP_ID_KEY, None)
        flash("Account not found.", "error")
        return redirect(url_for("auth.forgot_password"))

    form = ResetPasswordForm()
    if form.validate_on_submit():
        user.set_password(form.password.data)
        user.failed_login_attempts = 0
        user.locked_until = None
        otp.redeem(verified_row)  # commits with the password change
        db.session.commit()
        session.pop(PENDING_RESET_EMAIL_KEY, None)
        session.pop(RESET_OTP_ID_KEY, None)
        flash("Password updated — you can now log in.", "success")
        return redirect(url_for("auth.login"))
    return render_template("auth/reset_password.html", stage="password", form=form, email=email)


@bp.route("/reset-password/<token>")
def reset_password_legacy(token):
    """Old emailed reset links — password reset is by 6-digit code now."""
    flash("Password reset now uses a 6-digit code. Request a new one below.", "info")
    return redirect(url_for("auth.forgot_password"))
