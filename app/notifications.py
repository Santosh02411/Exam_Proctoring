"""Notifications & Reminders — a single place that renders an email template,
sends it (or logs it, via app.email_utils.send_email's SMTP fallback), and
records it in NotificationLog so there's a queryable history regardless of
whether the send actually reached an inbox. Route/model code should call
notify() (or one of the two bulk/conditional helpers below) rather than
building emails inline, so every notification type has one template and one
history entry format.
"""

from datetime import datetime, timedelta

from flask import render_template, current_app, url_for

from app import db
from app.models import NotificationLog, Test, Attempt, User
from app.email_utils import send_email

# Kept in sync with the .txt templates under app/templates/email/ and with
# NotificationLog.notif_type's comment in app.models.
NOTIFICATION_SUBJECTS = {
    "exam_scheduled": "New test assigned: {test_title}",
    "exam_starting_soon": "Starting soon: {test_title}",
    "exam_completed": "Submitted: {test_title}",
    "result_published": "Your result is ready: {test_title}",
    "high_risk_alert": "High-risk activity flagged — {student_name} on {test_title}",
}


def notify(user, notif_type, context, test=None, attempt=None):
    """Render email/<notif_type>.txt with `context`, send it to `user`, and
    log the attempt regardless of outcome. Never raises — a notification
    failure shouldn't break the request that triggered it (a submission, a
    grading save, an assignment) — logging the failed send_status is enough
    for someone to notice and retry later."""
    subject = NOTIFICATION_SUBJECTS[notif_type].format(**context)
    body = render_template(f"email/{notif_type}.txt", **context)

    status = "sent"
    try:
        mode = send_email(user.email, subject, body)
        status = "sent" if mode == "smtp" else "logged"
    except Exception:
        current_app.logger.exception("notification send failed: type=%s to=%s", notif_type, user.email)
        status = "failed"

    try:
        db.session.add(NotificationLog(
            user_id=user.id, notif_type=notif_type, subject=subject, body_preview=body[:1000],
            send_status=status, test_id=test.id if test else None, attempt_id=attempt.id if attempt else None,
        ))
        db.session.commit()
    except Exception:
        current_app.logger.exception("failed to record notification history: type=%s to=%s", notif_type, user.email)
        db.session.rollback()

    return status


def notify_exam_scheduled(student, test):
    notify(student, "exam_scheduled", {
        "student_name": student.name, "test_title": test.title,
        "duration_minutes": test.duration_minutes,
        "start_time": test.start_time.strftime("%Y-%m-%d %H:%M UTC") if test.start_time else None,
        "dashboard_url": url_for("student.dashboard", _external=True),
    }, test=test)


def send_starting_soon_reminders(window_minutes=None, org_id=None):
    """Find published tests whose start_time falls within the next
    `window_minutes`, and email every eligible student who hasn't already
    started an attempt — deduped via NotificationLog so calling this
    repeatedly (an admin clicking a button, or a cron job hitting the CLI
    command) never sends the same student the same reminder twice for the
    same test. Meant to be run periodically (see the `send-reminders` CLI
    command in app/__init__.py); this app has no background scheduler of
    its own, so in production this needs an external cron/Task Scheduler
    entry running that command every so often. org_id restricts the sweep
    to one organization's tests — used by the admin-triggered manual
    button (app.admin.trigger_reminders), which should only ever touch the
    calling admin's own tenant; the CLI command leaves it unset to sweep
    every organization."""
    if window_minutes is None:
        window_minutes = current_app.config["EXAM_REMINDER_WINDOW_MINUTES"]

    now = datetime.utcnow()
    window_end = now + timedelta(minutes=window_minutes)
    query = Test.query.filter(
        Test.status == "published",
        Test.start_time.isnot(None),
        Test.start_time >= now,
        Test.start_time <= window_end,
    )
    if org_id is not None:
        query = query.filter(Test.org_id == org_id)
    tests = query.all()

    sent = 0
    for test in tests:
        for elig in test.eligibility:
            student = db.session.get(User, elig.student_id)
            if not student or student.status != "active":
                continue
            already_started = Attempt.query.filter_by(test_id=test.id, student_id=student.id).first()
            if already_started:
                continue
            already_notified = NotificationLog.query.filter_by(
                user_id=student.id, notif_type="exam_starting_soon", test_id=test.id,
            ).first()
            if already_notified:
                continue

            minutes_until = max(int((test.start_time - now).total_seconds() // 60), 0)
            notify(student, "exam_starting_soon", {
                "student_name": student.name, "test_title": test.title,
                "start_time": test.start_time.strftime("%Y-%m-%d %H:%M UTC"),
                "minutes_until": minutes_until,
                "dashboard_url": url_for("student.dashboard", _external=True),
            }, test=test)
            sent += 1
    return sent


def _pending_grading(attempt):
    return any(
        a.question.needs_manual_grading and a.selected_option and a.manual_score is None
        for a in attempt.answers
    )


# Public name for the same check — used by app.api_v1's results endpoint so
# an external LMS polling for grades can tell "finished, pending manual
# grading" apart from "finished, score is final" without duplicating this.
pending_grading = _pending_grading


def notify_exam_completed_and_maybe_published(attempt):
    """Called right after an attempt is finalized (normal submit, or the
    server-side auto-finalize when time ran out while offline — see
    student._finalize_attempt's call sites). Always sends the submission
    receipt; also sends the result-published notice immediately if nothing
    is left needing manual grading, since in that case the score really is
    final the moment the attempt is finalized."""
    test = attempt.test
    student = attempt.student
    pending = _pending_grading(attempt)
    result_url = url_for("student.result", attempt_id=attempt.id, _external=True)

    notify(student, "exam_completed", {
        "student_name": student.name, "test_title": test.title,
        "pending_grading": pending, "result_url": result_url,
    }, test=test, attempt=attempt)

    if not pending:
        _notify_result_published(attempt, result_url=result_url)


def notify_result_published_if_now_complete(attempt, was_pending_before):
    """Called after admin.grade_attempt saves manual scores. If grading was
    still incomplete before this save and is fully complete now, the
    student's result just became final — send the result-published notice
    (the completion receipt was already sent back at submission time)."""
    if was_pending_before and not _pending_grading(attempt):
        _notify_result_published(attempt)


def _notify_result_published(attempt, result_url=None):
    test = attempt.test
    student = attempt.student
    total_marks = test.total_marks()
    passed = (attempt.score or 0) >= test.passing_marks if attempt.status != "terminated" else False
    notify(student, "result_published", {
        "student_name": student.name, "test_title": test.title,
        "score": attempt.score, "total_marks": total_marks, "passed": passed,
        "result_url": result_url or url_for("student.result", attempt_id=attempt.id, _external=True),
    }, test=test, attempt=attempt)
    _maybe_send_lms_webhook(attempt, total_marks, passed)


def _maybe_send_lms_webhook(attempt, total_marks, passed):
    """LMS/API Integrations (see app.api_v1): if this org has configured an
    outbound webhook URL, push the just-published result to it — the same
    fields GET /api/v1/tests/<code>/results returns for this attempt, so a
    receiver doesn't need two different shapes depending on whether it's
    polling or being pushed to. Best-effort and inline with the publish
    flow it's attached to (same as the Slack webhook in app.alerting): a
    slow or unreachable receiver gets a short timeout and any failure is
    swallowed rather than surfaced to the student whose result triggered
    it."""
    import json as json_module
    import urllib.error
    import urllib.request

    webhook_url = attempt.test.organization.lms_webhook_url
    if not webhook_url:
        return

    payload = {
        "event": "result_published",
        "attempt_id": attempt.id,
        "test_code": attempt.test.test_code,
        "test_title": attempt.test.title,
        "student_email": attempt.student.email,
        "student_name": attempt.student.name,
        "status": attempt.status,
        "score": attempt.score,
        "total_marks": total_marks,
        "passed": passed,
        "submitted_at": attempt.submitted_at.isoformat() if attempt.submitted_at else None,
    }
    try:
        req = urllib.request.Request(
            webhook_url, data=json_module.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"}, method="POST",
        )
        urllib.request.urlopen(req, timeout=current_app.config["LMS_WEBHOOK_TIMEOUT_SECONDS"])
    except (urllib.error.URLError, OSError, ValueError) as e:
        current_app.logger.warning("LMS webhook delivery failed for org %s: %s", attempt.test.org_id, e)


def maybe_send_high_risk_alert(attempt, risk):
    """Fired from proctoring._record_violation right after a violation
    pushes the attempt's risk into "high" or "critical" — a proactive nudge
    for a proctor/admin to go look, well before an attempt necessarily hits
    the auto-termination threshold (which already has its own notification;
    see _notify_termination). Fires at most once per attempt, the moment it
    first crosses into high-risk territory, via the high_risk_alert_sent
    flag — a run of further violations on an already-flagged attempt
    doesn't generate another email per event."""
    if attempt.high_risk_alert_sent or risk["level"] not in ("high", "critical"):
        return
    attempt.high_risk_alert_sent = True

    test = attempt.test
    student = attempt.student
    view_url = url_for("admin.view_attempt", attempt_id=attempt.id, _external=True)
    context = {
        "student_name": student.name, "test_title": test.title,
        "suspicion_score": risk["score"], "risk_level": risk["level"],
        "violation_count": attempt.violation_count, "view_url": view_url,
    }

    recipients = []
    if test.creator and test.creator.email:
        recipients.append(test.creator)
    recipients += User.query.filter_by(role="proctor", status="active").all()
    # De-dupe in case the test's creator is themselves a proctor account.
    seen_ids = set()
    for recipient in recipients:
        if recipient.id in seen_ids:
            continue
        seen_ids.add(recipient.id)
        notify(recipient, "high_risk_alert", context, test=test, attempt=attempt)
