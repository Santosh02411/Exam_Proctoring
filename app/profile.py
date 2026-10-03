from flask import Blueprint, render_template, redirect, url_for, flash, request, session
from flask_login import login_required, current_user
import json

from app import db
from app import twofa
from app.forms import ProfileForm, ChangePasswordForm, TwoFactorSetupForm, TwoFactorDisableForm, TwoFactorRegenerateForm, NotificationPreferencesForm
from app.models import LoginSession, LoginSecurityEvent
from app.notifications import NOTIFICATION_PREF_LABELS

bp = Blueprint("profile", __name__, url_prefix="/profile")

# Session key holding the not-yet-confirmed secret between GET /2fa/setup
# (which generates it) and POST (which checks the user's first code
# against it) — kept out of the database until confirm_totp succeeds, so
# an abandoned setup never leaves a half-configured secret on the account.
PENDING_SETUP_SESSION_KEY = "pending_totp_secret"


@bp.route("/", methods=["GET", "POST"])
@login_required
def view_profile():
    form = ProfileForm(obj=current_user)
    password_form = ChangePasswordForm()
    disable_2fa_form = TwoFactorDisableForm()
    regenerate_2fa_form = TwoFactorRegenerateForm()

    if request.method == "POST" and request.form.get("form_name") == "password":
        if password_form.validate_on_submit():
            if not current_user.check_password(password_form.current_password.data):
                flash("Current password is incorrect.", "error")
            else:
                current_user.set_password(password_form.new_password.data)
                db.session.commit()
                flash("Password changed successfully.", "success")
                return redirect(url_for("profile.view_profile"))
    elif request.method == "POST":
        if form.validate_on_submit():
            current_user.name = form.name.data.strip()
            current_user.phone = form.phone.data.strip()
            db.session.commit()
            flash("Profile updated.", "success")
            return redirect(url_for("profile.view_profile"))

    return render_template("profile/view.html", form=form, password_form=password_form,
                            disable_2fa_form=disable_2fa_form, regenerate_2fa_form=regenerate_2fa_form)


@bp.route("/2fa/setup", methods=["GET", "POST"])
@login_required
def setup_2fa():
    if twofa.requires_2fa(current_user):
        flash("Two-factor authentication is already enabled.", "info")
        return redirect(url_for("profile.view_profile"))

    form = TwoFactorSetupForm()
    if form.validate_on_submit():
        secret = session.get(PENDING_SETUP_SESSION_KEY)
        if not secret or not twofa.verify_code(secret, form.code.data):
            flash("That code didn't match — please try again.", "error")
        else:
            current_user.totp_secret = secret
            current_user.totp_confirmed = True
            plain_codes, hashed_json = twofa.generate_backup_codes()
            current_user.backup_codes = hashed_json
            db.session.commit()
            session.pop(PENDING_SETUP_SESSION_KEY, None)
            flash("Two-factor authentication is now enabled.", "success")
            return render_template("profile/backup_codes.html", codes=plain_codes)

    secret = session.get(PENDING_SETUP_SESSION_KEY)
    if not secret:
        secret = twofa.generate_secret()
        session[PENDING_SETUP_SESSION_KEY] = secret
    qr_data_uri = twofa.qr_code_data_uri(twofa.provisioning_uri(current_user, secret))
    return render_template("profile/setup_2fa.html", form=form, qr_data_uri=qr_data_uri, secret=secret)


@bp.route("/2fa/disable", methods=["POST"])
@login_required
def disable_2fa():
    form = TwoFactorDisableForm()
    if form.validate_on_submit():
        if not current_user.check_password(form.password.data):
            flash("Incorrect password.", "error")
        else:
            current_user.totp_secret = None
            current_user.totp_confirmed = False
            current_user.backup_codes = None
            db.session.commit()
            flash("Two-factor authentication has been disabled.", "success")
    return redirect(url_for("profile.view_profile"))


@bp.route("/2fa/regenerate-backup-codes", methods=["POST"])
@login_required
def regenerate_backup_codes():
    if not twofa.requires_2fa(current_user):
        return redirect(url_for("profile.view_profile"))
    form = TwoFactorRegenerateForm()
    if not form.validate_on_submit():
        flash("Something went wrong — please try again.", "error")
        return redirect(url_for("profile.view_profile"))
    plain_codes, hashed_json = twofa.generate_backup_codes()
    current_user.backup_codes = hashed_json
    db.session.commit()
    flash("New backup codes generated — your old ones no longer work.", "success")
    return render_template("profile/backup_codes.html", codes=plain_codes)


@bp.route("/sign-in-activity")
@login_required
def signin_activity():
    """New Sign-In Alerts / self-service security review: this account's
    own recent sessions and flagged anomalies (see app.security) —
    previously only visible to an admin on the org-wide security log,
    even though the account owner is the one actually positioned to say
    "that wasn't me." Read-only; a session shown here already ends itself
    the normal way (logout, single-session replacement, expiry) rather
    than being revocable from this page."""
    sessions = LoginSession.query.filter_by(user_id=current_user.id).order_by(
        LoginSession.created_at.desc()
    ).limit(20).all()
    events = LoginSecurityEvent.query.filter_by(user_id=current_user.id).order_by(
        LoginSecurityEvent.created_at.desc()
    ).limit(20).all()
    return render_template("profile/signin_activity.html", sessions=sessions, events=events)


@bp.route("/notification-preferences", methods=["GET", "POST"])
@login_required
def notification_preferences():
    """Notification Preferences: per notif_type/channel opt-outs (see
    User.notification_prefs, app.notifications.channel_allowed) — nothing
    here can turn OFF the NotificationLog history a message would have
    left, only whether it actually gets sent; a skipped-by-preference
    entry still records that the notification "fired," just that this
    account asked not to receive it."""
    current_prefs = {}
    if current_user.notification_prefs:
        try:
            current_prefs = json.loads(current_user.notification_prefs)
        except (TypeError, ValueError):
            current_prefs = {}

    form = NotificationPreferencesForm()
    if form.validate_on_submit():
        new_prefs = {}
        for notif_type in NOTIFICATION_PREF_LABELS:
            entry = {}
            if request.form.get(f"email_{notif_type}") != "on":
                entry["email"] = False
            # A disabled checkbox (no phone on file) never submits a value
            # at all — that's "not applicable," not "the user unchecked
            # it," so it must not be recorded as an opt-out or an account
            # that later adds a phone number would find SMS silently off
            # for everything without ever having touched this page.
            if current_user.phone and request.form.get(f"sms_{notif_type}") != "on":
                entry["sms"] = False
            if entry:
                new_prefs[notif_type] = entry
        current_user.notification_prefs = json.dumps(new_prefs) if new_prefs else None
        db.session.commit()
        flash("Notification preferences updated.", "success")
        return redirect(url_for("profile.notification_preferences"))

    rows = [
        {
            "notif_type": nt, "label": label,
            "email_on": current_prefs.get(nt, {}).get("email", True),
            "sms_on": current_prefs.get(nt, {}).get("sms", True),
        }
        for nt, label in NOTIFICATION_PREF_LABELS.items()
    ]
    return render_template("profile/notification_preferences.html", form=form, rows=rows, has_phone=bool(current_user.phone))
