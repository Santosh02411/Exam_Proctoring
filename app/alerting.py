"""Real-time system health alerts — disk usage and error-rate spikes,
notified by email (every super_admin, or ALERT_EMAIL_OVERRIDE if set) and
an optional Slack webhook. See app.system_ops's health dashboard for the
pull-based view of the same underlying numbers; this module is what turns
"someone would have to go look" into "someone gets told".

Error-rate checks run inline, right after every error is logged (see
app.error_monitoring.log_error), so a spike is caught the moment it
crosses the threshold rather than waiting for a periodic job. Disk usage
can't be triggered by an app-level event the same way — nothing "happens"
when a disk fills up — so it's checked whenever the health dashboard loads
and via the `flask check-health-alerts` CLI command, meant to be run
periodically by an external cron/Task Scheduler entry (the same
"bring your own scheduler" pattern as backup-db/apply-retention/
send-reminders).

Each alert type is deduplicated by "is there already an unresolved alert
of this type": once one exists, no new one is created (or notified) until
a human resolves it from /ops/alerts — a sustained problem raises one
notification, not one per check.
"""

import json
import urllib.error
import urllib.request

from flask import current_app

from app import db
from app.models import SystemAlert, User, ErrorLog
from app.email_utils import send_email
from datetime import datetime, timedelta


def _alert_recipients():
    override = current_app.config.get("ALERT_EMAIL_OVERRIDE")
    if override:
        return [e.strip() for e in override.split(",") if e.strip()]
    return [u.email for u in User.query.filter_by(role="super_admin").all()]


def _send_slack(message):
    webhook = current_app.config.get("SLACK_WEBHOOK_URL")
    if not webhook:
        return
    try:
        body = json.dumps({"text": message}).encode("utf-8")
        req = urllib.request.Request(
            webhook, data=body, headers={"Content-Type": "application/json"}, method="POST"
        )
        urllib.request.urlopen(req, timeout=5)
    except (urllib.error.URLError, OSError, ValueError):
        pass  # Best-effort — a broken/misconfigured webhook must never block alert creation.


def _notify(alert):
    """Email every alert recipient and post to Slack if configured.
    Best-effort on both channels — a delivery failure still leaves the
    alert recorded in the DB for /ops/alerts to show, it just means
    nobody was proactively told."""
    subject = f"[Exam Proctoring] {alert.severity.upper()}: {alert.alert_type.replace('_', ' ')}"
    for email in _alert_recipients():
        try:
            send_email(email, subject, alert.message)
        except Exception:
            pass
    try:
        _send_slack(f"*{subject}*\n{alert.message}")
    except Exception:
        pass
    alert.notified_at = datetime.utcnow()
    db.session.commit()


def _create_alert_if_not_open(alert_type, severity, message):
    """Create + notify a new alert for this type, unless one is already
    open (unresolved) — the dedup rule described in the module docstring.
    Returns the alert if a new one was created, else None."""
    existing = SystemAlert.query.filter_by(alert_type=alert_type, resolved=False).first()
    if existing:
        return None
    alert = SystemAlert(alert_type=alert_type, severity=severity, message=message)
    db.session.add(alert)
    db.session.commit()
    _notify(alert)
    return alert


def check_disk_usage():
    """Alert if disk usage on the instance volume is at/above
    DISK_USAGE_ALERT_THRESHOLD_PCT. Returns the new alert, or None if
    usage is fine or an alert is already open."""
    import shutil

    total, used, _free = shutil.disk_usage(current_app.instance_path)
    if total == 0:
        return None
    pct_used = 100 * used / total
    threshold = current_app.config["DISK_USAGE_ALERT_THRESHOLD_PCT"]
    if pct_used < threshold:
        return None
    return _create_alert_if_not_open(
        "disk_usage_high", "critical" if pct_used >= 97 else "warning",
        f"Disk usage is at {pct_used:.1f}% (threshold: {threshold:.0f}%).",
    )


def check_error_rate():
    """Alert if the number of errors logged in the last
    ERROR_RATE_ALERT_WINDOW_MINUTES is at/above ERROR_RATE_ALERT_THRESHOLD.
    Returns the new alert, or None if the rate is fine or an alert is
    already open. Called inline from app.error_monitoring.log_error() for
    real-time detection, and also included in run_health_checks() for the
    periodic sweep."""
    window = current_app.config["ERROR_RATE_ALERT_WINDOW_MINUTES"]
    threshold = current_app.config["ERROR_RATE_ALERT_THRESHOLD"]
    cutoff = datetime.utcnow() - timedelta(minutes=window)
    count = ErrorLog.query.filter(ErrorLog.occurred_at >= cutoff).count()
    if count < threshold:
        return None
    return _create_alert_if_not_open(
        "error_rate_spike", "critical" if count >= threshold * 2 else "warning",
        f"{count} errors logged in the last {window} minute(s) (threshold: {threshold}).",
    )


def run_health_checks():
    """Run every periodic check once (disk usage; error rate is also
    checked here for completeness, though it's normally already caught
    inline). Meant for the `flask check-health-alerts` CLI command and the
    "Run Health Check Now" button on /ops/alerts. Returns the list of
    newly created alerts (empty if everything's within range or already
    has an open alert)."""
    alerts = [check_disk_usage(), check_error_rate()]
    return [a for a in alerts if a is not None]
