"""System Monitoring & Operations — platform-level operational visibility,
reachable only by super_admin (see app.organizations for why that role is
kept separate from any one organization's own admin surface). Everything
here is intentionally cross-org: a health dashboard, active sessions,
storage usage, failed proctoring sessions, recording-storage management,
error monitoring, backups, and data retention are all platform-operator
concerns, not something any single tenant's admin should need or be able
to see about other tenants.
"""

import os
import shutil
import time
from datetime import datetime, timedelta

from flask import Blueprint, render_template, redirect, url_for, flash, request, current_app, send_from_directory
from flask_login import current_user
from sqlalchemy import text

from app import db
from app.models import (
    Organization, User, Test, Attempt, Recording, Snapshot, IdentityDocument,
    LoginSession, ErrorLog, SystemAlert,
)
from app.forms import PlatformRetentionPolicyForm
from app.utils import super_admin_required
from app.activity_log import log_activity
from app import backup as backup_module
from app import retention as retention_module
from app import alerting

bp = Blueprint("system_ops", __name__, url_prefix="/ops")

PER_PAGE = 30


def _dir_size(path):
    """Total size in bytes of every file under `path`, or 0 if it doesn't
    exist yet (a fresh install may not have created snapshots/recordings/
    id_documents directories until the first upload)."""
    if not os.path.isdir(path):
        return 0
    total = 0
    for root, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += os.path.getsize(os.path.join(root, name))
            except OSError:
                pass
    return total


def _human_size(num_bytes):
    size = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.1f} {unit}"
        size /= 1024


def _process_memory_bytes():
    """Best-effort resident-set-size of this process. Uses the stdlib
    `resource` module (POSIX only — this app targets Linux deployment);
    returns None rather than raising on any platform/permission issue, so
    the health dashboard degrades gracefully instead of erroring."""
    try:
        import resource
        usage = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        # ru_maxrss is KB on Linux, bytes on macOS — this app's deployment
        # target is Linux, so treat it as KB.
        return usage * 1024
    except Exception:
        return None


@bp.route("/")
@super_admin_required
def health_dashboard():
    started_at = current_app.config.get("APP_STARTED_AT")
    uptime_seconds = (time.time() - started_at) if started_at else None

    db_ok = True
    db_error = None
    try:
        db.session.execute(text("SELECT 1"))
    except Exception as exc:
        db_ok = False
        db_error = str(exc)[:300]

    total_disk, used_disk, free_disk = shutil.disk_usage(current_app.instance_path)

    # Real-time-ish disk check: evaluated every time the dashboard loads,
    # not just via the periodic CLI sweep — so a super_admin who checks in
    # gets an alert raised (and notified about) immediately if usage is
    # already over threshold, rather than waiting for the next cron run.
    try:
        alerting.check_disk_usage()
    except Exception:
        pass

    counts = {
        "organizations": Organization.query.count(),
        "users": User.query.count(),
        "tests": Test.query.count(),
        "attempts": Attempt.query.count(),
        "in_progress_attempts": Attempt.query.filter_by(status="in_progress").count(),
        "active_sessions": LoginSession.query.filter_by(is_active=True).count(),
    }

    recent_errors = ErrorLog.query.filter(
        ErrorLog.occurred_at >= datetime.utcnow() - timedelta(hours=24), ErrorLog.resolved.is_(False)
    ).count()
    open_alerts = SystemAlert.query.filter_by(resolved=False).count()

    return render_template(
        "system_ops/health.html",
        uptime_seconds=uptime_seconds, db_ok=db_ok, db_error=db_error,
        total_disk=_human_size(total_disk), used_disk=_human_size(used_disk), free_disk=_human_size(free_disk),
        disk_pct_used=round(100 * used_disk / total_disk, 1) if total_disk else None,
        process_memory=_human_size(_process_memory_bytes()) if _process_memory_bytes() else "Unavailable",
        counts=counts, recent_errors=recent_errors, open_alerts=open_alerts,
    )


@bp.route("/sessions")
@super_admin_required
def active_sessions():
    """Every currently-active login session across every organization —
    the cross-tenant counterpart to an org admin's own (org-scoped)
    /admin/security-log."""
    page = request.args.get("page", 1, type=int)
    pagination = LoginSession.query.filter_by(is_active=True).order_by(
        LoginSession.last_seen_at.desc()
    ).paginate(page=page, per_page=PER_PAGE, error_out=False)
    return render_template("system_ops/sessions.html", pagination=pagination, sessions=pagination.items)


@bp.route("/storage")
@super_admin_required
def storage_usage():
    cfg = current_app.config
    snapshot_bytes = _dir_size(cfg["SNAPSHOT_UPLOAD_DIR"])
    recordings_bytes = _dir_size(cfg["RECORDINGS_DIR"])
    id_docs_bytes = _dir_size(cfg["ID_DOCUMENT_UPLOAD_DIR"])
    backups_bytes = _dir_size(cfg["BACKUPS_DIR"])

    db_path = cfg["SQLALCHEMY_DATABASE_URI"]
    db_size = None
    if db_path.startswith("sqlite:///") and db_path != "sqlite:///:memory:":
        real_path = db_path[len("sqlite:///"):]
        if os.path.exists(real_path):
            db_size = os.path.getsize(real_path)

    breakdown = [
        ("Exam recordings", recordings_bytes, Recording.query.count()),
        ("Proctoring snapshots", snapshot_bytes, Snapshot.query.count()),
        ("Identity documents", id_docs_bytes, IdentityDocument.query.count()),
        ("Database backups", backups_bytes, len(backup_module.list_backups())),
    ]

    total_disk, used_disk, free_disk = shutil.disk_usage(current_app.instance_path)

    return render_template(
        "system_ops/storage.html",
        breakdown=[(label, _human_size(size), count) for label, size, count in breakdown],
        db_size=_human_size(db_size) if db_size is not None else "N/A (not sqlite, or in-memory)",
        total_disk=_human_size(total_disk), used_disk=_human_size(used_disk), free_disk=_human_size(free_disk),
        disk_pct_used=round(100 * used_disk / total_disk, 1) if total_disk else None,
    )


@bp.route("/failed-sessions")
@super_admin_required
def failed_sessions():
    """Every terminated (auto-ended for violations) attempt across every
    organization, newest first — the cross-tenant operational view; an
    org's own admin/proctor already has this scoped to their org via
    /admin/review-queue. Kept read-only here (no drill-into-attempt link)
    since attempt detail is org-admin territory, not a platform-operator
    concern."""
    page = request.args.get("page", 1, type=int)
    pagination = Attempt.query.filter_by(status="terminated").order_by(
        Attempt.started_at.desc()
    ).paginate(page=page, per_page=PER_PAGE, error_out=False)
    return render_template("system_ops/failed_sessions.html", pagination=pagination, attempts=pagination.items)


@bp.route("/recordings")
@super_admin_required
def recordings():
    page = request.args.get("page", 1, type=int)
    pagination = Recording.query.order_by(Recording.created_at.desc()).paginate(
        page=page, per_page=PER_PAGE, error_out=False
    )
    total_bytes = db.session.query(db.func.coalesce(db.func.sum(Recording.file_size), 0)).scalar()
    return render_template(
        "system_ops/recordings.html", pagination=pagination, recordings=pagination.items,
        total_size=_human_size(total_bytes), total_count=Recording.query.count(),
    )


@bp.route("/recordings/<int:recording_id>/delete", methods=["POST"])
@super_admin_required
def delete_recording(recording_id):
    rec = Recording.query.get_or_404(recording_id)
    path = os.path.join(current_app.config["RECORDINGS_DIR"], rec.filename)
    try:
        if os.path.exists(path):
            os.remove(path)
    except OSError:
        pass
    db.session.delete(rec)
    db.session.commit()
    log_activity("deleted_recording", f"Deleted recording #{recording_id} from storage management")
    flash("Recording deleted.", "success")
    return redirect(url_for("system_ops.recordings"))


@bp.route("/recordings/bulk-delete", methods=["POST"])
@super_admin_required
def bulk_delete_recordings():
    """Delete every recording older than the given number of days — the
    "recording-storage management" bulk cleanup action, distinct from the
    scheduled retention sweep (app.retention) in that this runs on-demand
    with an admin-chosen cutoff rather than the configured
    RECORDING_RETENTION_DAYS default."""
    try:
        days = int(request.form.get("older_than_days", 0))
    except (TypeError, ValueError):
        days = 0
    if days <= 0:
        flash("Enter a positive number of days.", "error")
        return redirect(url_for("system_ops.recordings"))

    cutoff = datetime.utcnow() - timedelta(days=days)
    rows = Recording.query.filter(Recording.created_at < cutoff).all()
    for rec in rows:
        path = os.path.join(current_app.config["RECORDINGS_DIR"], rec.filename)
        try:
            if os.path.exists(path):
                os.remove(path)
        except OSError:
            pass
        db.session.delete(rec)
    db.session.commit()
    log_activity("bulk_deleted_recordings", f"Deleted {len(rows)} recording(s) older than {days} day(s)")
    flash(f"Deleted {len(rows)} recording(s) older than {days} day(s).", "success")
    return redirect(url_for("system_ops.recordings"))


@bp.route("/errors")
@super_admin_required
def error_log():
    page = request.args.get("page", 1, type=int)
    status_filter = request.args.get("status", "unresolved")

    query = ErrorLog.query
    if status_filter == "unresolved":
        query = query.filter_by(resolved=False)
    elif status_filter == "resolved":
        query = query.filter_by(resolved=True)
    query = query.order_by(ErrorLog.occurred_at.desc())

    pagination = query.paginate(page=page, per_page=PER_PAGE, error_out=False)
    return render_template(
        "system_ops/errors.html", pagination=pagination, entries=pagination.items, status_filter=status_filter,
        unresolved_count=ErrorLog.query.filter_by(resolved=False).count(),
    )


@bp.route("/errors/<int:error_id>/resolve", methods=["POST"])
@super_admin_required
def resolve_error(error_id):
    entry = ErrorLog.query.get_or_404(error_id)
    entry.resolved = True
    db.session.commit()
    return redirect(url_for("system_ops.error_log"))


@bp.route("/backups")
@super_admin_required
def backups():
    return render_template(
        "system_ops/backups.html", backups=backup_module.list_backups(),
        display_size=_human_size,
    )


@bp.route("/backups/create", methods=["POST"])
@super_admin_required
def create_backup():
    try:
        filename, size = backup_module.create_backup()
    except ValueError as exc:
        flash(str(exc), "error")
    else:
        log_activity("created_backup", f"Created database backup '{filename}' ({_human_size(size)})")
        flash(f"Backup created: {filename} ({_human_size(size)}).", "success")
    return redirect(url_for("system_ops.backups"))


@bp.route("/backups/<path:filename>/download")
@super_admin_required
def download_backup(filename):
    return send_from_directory(current_app.config["BACKUPS_DIR"], filename, as_attachment=True)


@bp.route("/backups/<path:filename>/delete", methods=["POST"])
@super_admin_required
def delete_backup(filename):
    try:
        backup_module.delete_backup(filename)
    except ValueError as exc:
        flash(str(exc), "error")
    else:
        log_activity("deleted_backup", f"Deleted database backup '{filename}'")
        flash("Backup deleted.", "success")
    return redirect(url_for("system_ops.backups"))


@bp.route("/retention", methods=["GET", "POST"])
@super_admin_required
def retention_policy():
    """Edit the platform-wide retention defaults (org_id NULL) — these are
    what every organization inherits unless it sets its own override (see
    /admin/retention, or edit a specific org's override from its detail
    page at /organizations/<id>/retention). Blank fields here fall back to
    the *_RETENTION_DAYS settings in config.py."""
    form = PlatformRetentionPolicyForm()

    if form.validate_on_submit():
        retention_module.save_form_to_policy(
            form, None, current_user.id, retention_module.PLATFORM_EDITABLE_CATEGORIES
        )
        log_activity("updated_platform_retention_policy", "Updated platform-wide retention policy defaults")
        flash("Platform retention defaults updated.", "success")
        return redirect(url_for("system_ops.retention_policy"))

    if request.method == "GET":
        retention_module.populate_form_from_policy(form, None)

    effective = retention_module.effective_policy_for_org(None)
    orgs = Organization.query.order_by(Organization.name).all()
    return render_template(
        "system_ops/retention.html", form=form, effective=effective,
        labels=retention_module.CATEGORY_LABELS, orgs=orgs,
    )


@bp.route("/retention/run", methods=["POST"])
@super_admin_required
def run_retention():
    results = retention_module.apply_retention_policies()
    log_activity("ran_retention_cleanup", f"Ran data retention cleanup: {results}")
    total = sum(results.values())
    if total:
        summary = ", ".join(f"{v} {k.replace('_', ' ')}" for k, v in results.items() if v)
        flash(f"Retention cleanup complete — deleted {total} record(s): {summary}.", "success")
    else:
        flash("Retention cleanup complete — nothing was past its retention window.", "success")
    return redirect(url_for("system_ops.retention_policy"))


@bp.route("/alerts")
@super_admin_required
def alerts():
    page = request.args.get("page", 1, type=int)
    status_filter = request.args.get("status", "unresolved")

    query = SystemAlert.query
    if status_filter == "unresolved":
        query = query.filter_by(resolved=False)
    elif status_filter == "resolved":
        query = query.filter_by(resolved=True)
    query = query.order_by(SystemAlert.created_at.desc())

    pagination = query.paginate(page=page, per_page=PER_PAGE, error_out=False)
    return render_template(
        "system_ops/alerts.html", pagination=pagination, entries=pagination.items, status_filter=status_filter,
        unresolved_count=SystemAlert.query.filter_by(resolved=False).count(),
    )


@bp.route("/alerts/run-check", methods=["POST"])
@super_admin_required
def run_health_check_now():
    new_alerts = alerting.run_health_checks()
    if new_alerts:
        flash(f"{len(new_alerts)} new alert(s) raised and notifications sent.", "warning")
    else:
        flash("Health check complete — nothing new to report.", "success")
    return redirect(url_for("system_ops.alerts"))


@bp.route("/alerts/<int:alert_id>/resolve", methods=["POST"])
@super_admin_required
def resolve_alert(alert_id):
    alert = SystemAlert.query.get_or_404(alert_id)
    alert.resolved = True
    alert.resolved_at = datetime.utcnow()
    db.session.commit()
    log_activity("resolved_system_alert", f"Resolved system alert #{alert.id} ({alert.alert_type})")
    flash("Alert resolved.", "success")
    return redirect(url_for("system_ops.alerts"))
