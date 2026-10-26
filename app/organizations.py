"""Institution/Organization management — the multi-tenant admin surface.

This is deliberately separate from app.admin: app.admin is where an
organization's own admins/examiners/proctors manage that org's exams,
already scoped to their org_id (see app.utils.org_scope/ensure_same_org).
This module is the platform layer above all of that — creating tenants and
seeing a cross-org summary — reachable only by the super_admin role, which
isn't tied to any single organization (User.org_id is NULL for it) and
intentionally does NOT get content_access/review_access into any org's exam
data. A super_admin manages institutions; an org's own admin manages that
institution's exams. Keeping those separate means an outage or bug in one
role's surface can't touch the other's data model of what it's allowed to
see.
"""

from flask import Blueprint, render_template, redirect, url_for, flash, request, Response, session
from flask_login import current_user
import json
from datetime import datetime

from app import db
from app.forms import OrganizationForm, RetentionPolicyForm, BrandingForm
from app.models import Organization, User, Test, Attempt
from app.utils import super_admin_required
from app.activity_log import log_activity
from app import retention as retention_module
from app import org_export
from app import org_reports
from app import branding as branding_module

bp = Blueprint("organizations", __name__, url_prefix="/organizations")


def _org_summary(org):
    return org_reports.build_org_summary(org)


@bp.route("/")
@super_admin_required
def list_organizations():
    orgs = Organization.query.order_by(Organization.name).all()
    summaries = [_org_summary(o) for o in orgs]
    return render_template("organizations/list.html", summaries=summaries)


@bp.route("/create", methods=["GET", "POST"])
@super_admin_required
def create_organization():
    form = OrganizationForm(status="active")
    if form.validate_on_submit():
        name = form.name.data.strip()
        if Organization.query.filter_by(name=name).first():
            flash("An organization with that name already exists.", "error")
        else:
            from app.auth import _unique_slug

            org = Organization(name=name, slug=_unique_slug(name), status=form.status.data)
            db.session.add(org)
            db.session.commit()
            log_activity("created_organization", f"Created organization '{org.name}'")
            flash(f"Organization '{org.name}' created.", "success")
            return redirect(url_for("organizations.list_organizations"))
    return render_template("organizations/create.html", form=form)


@bp.route("/<int:org_id>")
@super_admin_required
def view_organization(org_id):
    org = Organization.query.get_or_404(org_id)
    summary = _org_summary(org)
    admins = User.query.filter_by(org_id=org.id, role="admin").order_by(User.name).all()
    recent_tests = Test.query.filter_by(org_id=org.id).order_by(Test.created_at.desc()).limit(10).all()
    return render_template("organizations/detail.html", summary=summary, admins=admins, recent_tests=recent_tests)


@bp.route("/<int:org_id>/edit", methods=["GET", "POST"])
@super_admin_required
def edit_organization(org_id):
    org = Organization.query.get_or_404(org_id)
    form = OrganizationForm(obj=org)
    if form.validate_on_submit():
        conflict = Organization.query.filter(Organization.name == form.name.data.strip(), Organization.id != org.id).first()
        if conflict:
            flash("An organization with that name already exists.", "error")
        else:
            org.name = form.name.data.strip()
            org.status = form.status.data
            db.session.commit()
            log_activity("updated_organization", f"Updated organization '{org.name}'")
            flash("Organization updated.", "success")
            return redirect(url_for("organizations.view_organization", org_id=org.id))
    return render_template("organizations/edit.html", form=form, org=org)


@bp.route("/<int:org_id>/toggle-status", methods=["POST"])
@super_admin_required
def toggle_organization_status(org_id):
    """Deactivating an organization blocks new registrations from joining
    it (see app.auth._org_choices, which only lists active orgs) but
    deliberately doesn't touch its existing users/tests/logins — this is a
    "stop onboarding more people into this tenant" switch, not a data
    lockout, since the latter would need its own explicit, harder-to-
    reverse confirmation flow."""
    org = Organization.query.get_or_404(org_id)
    org.status = "inactive" if org.status == "active" else "active"
    db.session.commit()
    log_activity("toggled_organization_status", f"Set organization '{org.name}' to {org.status}")
    flash(f"Organization '{org.name}' is now {org.status}.", "success")
    return redirect(url_for("organizations.view_organization", org_id=org.id))


@bp.route("/<int:org_id>/retention", methods=["GET", "POST"])
@super_admin_required
def org_retention(org_id):
    """Super_admin's view of one specific organization's retention
    overrides — the same six categories the org's own admin can set from
    /admin/retention, editable here too so a platform operator can help an
    org configure this (or override it) without needing that org's admin
    credentials. error_log doesn't apply per-org (see app.retention)."""
    org = Organization.query.get_or_404(org_id)
    form = RetentionPolicyForm()

    if form.validate_on_submit():
        retention_module.save_form_to_policy(
            form, org.id, current_user.id, retention_module.ORG_EDITABLE_CATEGORIES
        )
        log_activity("updated_org_retention_policy", f"Updated retention policy overrides for '{org.name}'")
        flash("Retention settings updated.", "success")
        return redirect(url_for("organizations.org_retention", org_id=org.id))

    if request.method == "GET":
        retention_module.populate_form_from_policy(form, org.id)

    effective = retention_module.effective_policy_for_org(org)
    return render_template(
        "organizations/retention.html", form=form, org=org,
        effective=effective, labels=retention_module.CATEGORY_LABELS,
    )


@bp.route("/<int:org_id>/export")
@super_admin_required
def export_organization(org_id):
    """Download this organization's self-service data export — see
    app.org_export. Available to super_admin here for support purposes;
    the org's own admin can also pull the same export themselves from
    /admin/org-backup."""
    org = Organization.query.get_or_404(org_id)
    data = org_export.export_organization_data(org)
    payload = json.dumps(data, indent=2)
    filename = f"{org.slug}_export_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.json"
    log_activity("exported_org_data", f"Downloaded a data export for '{org.name}' (as super_admin)")
    return Response(
        payload, mimetype="application/json",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@bp.route("/<int:org_id>/report/export.csv")
@super_admin_required
def export_report_csv(org_id):
    org = Organization.query.get_or_404(org_id)
    summary = org_reports.build_org_summary(org)
    csv_text = org_reports.render_summary_csv(summary)
    filename = f"{org.slug}_report_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.csv"
    log_activity("exported_org_report", f"Exported CSV report for '{org.name}' (as super_admin)")
    return Response(
        csv_text, mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@bp.route("/<int:org_id>/report/export.pdf")
@super_admin_required
def export_report_pdf(org_id):
    org = Organization.query.get_or_404(org_id)
    summary = org_reports.build_org_summary(org)
    pdf_bytes = org_reports.render_summary_pdf(summary)
    filename = f"{org.slug}_report_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.pdf"
    log_activity("exported_org_report", f"Exported PDF report for '{org.name}' (as super_admin)")
    return Response(
        pdf_bytes, mimetype="application/pdf",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@bp.route("/<int:org_id>/impersonate", methods=["POST"])
@super_admin_required
def impersonate_organization(org_id):
    """Start a read-only support view of this organization's exam content
    (tests, questions, results, analytics, proctor queue — see
    app.utils.roles_required_or_impersonating). Deliberately does NOT grant
    access to that org's user management, retention settings, or security
    logs — those stay limited to the org's own admin account, impersonation
    or not. Every request made while impersonating is still logged as the
    super_admin's own account, and starting/stopping impersonation itself
    is audit-logged here."""
    org = Organization.query.get_or_404(org_id)
    session["impersonate_org_id"] = org.id
    log_activity("started_impersonation", f"Started a read-only support view of '{org.name}'")
    flash(f"Viewing {org.name}'s exam content in read-only support mode.", "warning")
    return redirect(url_for("admin.dashboard"))


@bp.route("/stop-impersonating", methods=["POST"])
@super_admin_required
def stop_impersonating():
    org_id = session.pop("impersonate_org_id", None)
    if org_id:
        org = Organization.query.get(org_id)
        log_activity("stopped_impersonation", f"Stopped the support view of '{org.name if org else org_id}'")
    flash("Stopped impersonating — you're back to the platform view.", "info")
    return redirect(url_for("organizations.list_organizations"))


@bp.route("/<int:org_id>/branding")
@super_admin_required
def org_branding(org_id):
    """Super_admin's view of one organization's branding — the same logo/
    color settings that org's own admin can set from /admin/branding,
    editable here too for support purposes. Logo upload and color are two
    independent forms/routes below — see app.admin.upload_branding_logo's
    docstring for why a combined submit would be a footgun with an HTML5
    type="color" input."""
    org = Organization.query.get_or_404(org_id)
    form = BrandingForm()
    return render_template("organizations/branding.html", form=form, org=org)


@bp.route("/<int:org_id>/branding/logo", methods=["POST"])
@super_admin_required
def upload_org_branding_logo(org_id):
    org = Organization.query.get_or_404(org_id)
    form = BrandingForm()
    if form.validate_on_submit() and form.logo.data:
        try:
            branding_module.save_logo(org, form.logo.data)
            log_activity("updated_org_branding", f"Updated logo for '{org.name}' (as super_admin)")
            flash("Logo updated.", "success")
        except ValueError as exc:
            flash(str(exc), "error")
    else:
        flash("Choose a logo file to upload.", "error")
    return redirect(url_for("organizations.org_branding", org_id=org.id))


@bp.route("/<int:org_id>/branding/color", methods=["POST"])
@super_admin_required
def set_org_branding_color(org_id):
    org = Organization.query.get_or_404(org_id)
    form = BrandingForm()
    if form.validate_on_submit() and form.primary_color.data:
        try:
            org.primary_color = branding_module.validate_color(form.primary_color.data)
            db.session.commit()
            log_activity("updated_org_branding", f"Updated primary color for '{org.name}' (as super_admin)")
            flash("Primary color updated.", "success")
        except ValueError as exc:
            flash(str(exc), "error")
    else:
        flash("Choose a color first.", "error")
    return redirect(url_for("organizations.org_branding", org_id=org.id))


@bp.route("/<int:org_id>/branding/remove-logo", methods=["POST"])
@super_admin_required
def remove_org_branding_logo(org_id):
    org = Organization.query.get_or_404(org_id)
    branding_module.remove_logo(org)
    log_activity("removed_org_logo", f"Removed logo for '{org.name}' (as super_admin)")
    flash("Logo removed.", "success")
    return redirect(url_for("organizations.org_branding", org_id=org.id))


@bp.route("/<int:org_id>/branding/remove-color", methods=["POST"])
@super_admin_required
def remove_org_branding_color(org_id):
    org = Organization.query.get_or_404(org_id)
    org.primary_color = None
    db.session.commit()
    log_activity("removed_org_color", f"Removed primary color for '{org.name}' (as super_admin)")
    flash("Primary color removed.", "success")
    return redirect(url_for("organizations.org_branding", org_id=org.id))
