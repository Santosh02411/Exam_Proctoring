from flask import Blueprint, render_template, redirect, url_for, flash, request, session
from flask_login import login_required, current_user

from app import db
from app import twofa
from app.forms import ProfileForm, ChangePasswordForm, TwoFactorSetupForm, TwoFactorDisableForm, TwoFactorRegenerateForm

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
