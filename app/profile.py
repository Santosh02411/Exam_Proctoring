from flask import Blueprint, render_template, redirect, url_for, flash, request
from flask_login import login_required, current_user

from app import db
from app.forms import ProfileForm, ChangePasswordForm

bp = Blueprint("profile", __name__, url_prefix="/profile")


@bp.route("/", methods=["GET", "POST"])
@login_required
def view_profile():
    form = ProfileForm(obj=current_user)
    password_form = ChangePasswordForm()

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

    return render_template("profile/view.html", form=form, password_form=password_form)
