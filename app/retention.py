"""Data retention/deletion policies. Each category (recordings, snapshots,
ended login sessions, login security events, activity log, notification
log, error log) has a retention window in days, resolved in this order for
any org-scoped category:

  1. that organization's own RetentionPolicy row (org_id = the org), if set
  2. the platform-wide RetentionPolicy row (org_id NULL), if set
  3. the *_RETENTION_DAYS default in config.py

Error log is platform-only (errors aren't tied to a tenant), so it only
ever resolves steps 2 and 3.

Recordings, snapshots, and everything else here are always deleted from
scratch each run against whatever's currently past its effective cutoff —
apply_retention_policies() is safe to call repeatedly. Recordings/snapshots
also delete their underlying file on disk (best-effort — a missing file
never blocks the row deletion). Login sessions are only ever purged once
*ended* (is_active == False); a still-active session is never deleted by a
retention sweep regardless of age, since that would silently log someone
out.
"""

import os
from datetime import datetime, timedelta

from flask import current_app

from app import db
from app.models import (
    Organization, User, Test, Attempt, Recording, Snapshot, LoginSession, LoginSecurityEvent,
    AdminActivityLog, NotificationLog, ErrorLog, RetentionPolicy,
)

# Maps a RetentionPolicy column name to its config.py fallback key.
_CATEGORY_CONFIG_KEYS = {
    "recording_retention_days": "RECORDING_RETENTION_DAYS",
    "snapshot_retention_days": "SNAPSHOT_RETENTION_DAYS",
    "ended_login_session_retention_days": "ENDED_LOGIN_SESSION_RETENTION_DAYS",
    "login_security_event_retention_days": "LOGIN_SECURITY_EVENT_RETENTION_DAYS",
    "activity_log_retention_days": "ACTIVITY_LOG_RETENTION_DAYS",
    "notification_log_retention_days": "NOTIFICATION_LOG_RETENTION_DAYS",
    "error_log_retention_days": "ERROR_LOG_RETENTION_DAYS",
}

# Categories an organization's own admin is allowed to override. Error log
# is platform-only — see module + model docstrings.
ORG_EDITABLE_CATEGORIES = [c for c in _CATEGORY_CONFIG_KEYS if c != "error_log_retention_days"]
PLATFORM_EDITABLE_CATEGORIES = list(_CATEGORY_CONFIG_KEYS.keys())

CATEGORY_LABELS = {
    "recording_retention_days": "Exam recordings",
    "snapshot_retention_days": "Proctoring snapshots",
    "ended_login_session_retention_days": "Ended login sessions",
    "login_security_event_retention_days": "Login security events",
    "activity_log_retention_days": "Admin activity log",
    "notification_log_retention_days": "Notification log",
    "error_log_retention_days": "Error log",
}


def get_policy(org_id):
    """The RetentionPolicy row for this org_id (or the platform-wide row
    if org_id is None), or None if nothing's been set yet — resolving that
    None down to an actual number is effective_days()'s job, not this
    function's."""
    return RetentionPolicy.query.filter_by(org_id=org_id).first()


def get_or_create_policy(org_id):
    """Like get_policy(), but creates an empty (all-NULL) row if none
    exists yet — used when the UI is about to save an edit and needs a row
    to write into."""
    policy = get_policy(org_id)
    if policy is None:
        policy = RetentionPolicy(org_id=org_id)
        db.session.add(policy)
        db.session.flush()
    return policy


def effective_days(category, org=None):
    """Resolve one category to an actual number of days for one
    organization (or platform-wide if org is None), per the three-level
    fallback described in this module's docstring."""
    config_key = _CATEGORY_CONFIG_KEYS[category]
    if org is not None:
        org_policy = get_policy(org.id)
        if org_policy is not None and getattr(org_policy, category) is not None:
            return getattr(org_policy, category)
    platform_policy = get_policy(None)
    if platform_policy is not None and getattr(platform_policy, category) is not None:
        return getattr(platform_policy, category)
    return current_app.config[config_key]


def effective_policy_for_org(org):
    """{category: (days, source)} for one organization — source is 'org',
    'platform', or 'default', so the UI can show an admin which of their
    numbers are their own override vs. inherited. org=None gives the
    platform-wide view (source is only ever 'platform' or 'default')."""
    result = {}
    org_policy = get_policy(org.id) if org is not None else None
    platform_policy = get_policy(None)
    for category in _CATEGORY_CONFIG_KEYS:
        if org is not None and org_policy is not None and getattr(org_policy, category) is not None:
            result[category] = (getattr(org_policy, category), "org")
        elif platform_policy is not None and getattr(platform_policy, category) is not None:
            result[category] = (getattr(platform_policy, category), "platform")
        else:
            result[category] = (current_app.config[_CATEGORY_CONFIG_KEYS[category]], "default")
    return result


def _cutoff(days):
    return datetime.utcnow() - timedelta(days=days)


def _delete_recordings_for_org(org_id, cutoff):
    rows = Recording.query.join(Attempt, Recording.attempt_id == Attempt.id).join(
        Test, Attempt.test_id == Test.id
    ).filter(Test.org_id == org_id, Recording.created_at < cutoff).all()
    for r in rows:
        path = os.path.join(current_app.config["RECORDINGS_DIR"], r.filename)
        try:
            if os.path.exists(path):
                os.remove(path)
        except OSError:
            pass
        db.session.delete(r)
    return len(rows)


def _delete_snapshots_for_org(org_id, cutoff):
    rows = Snapshot.query.join(Attempt, Snapshot.attempt_id == Attempt.id).join(
        Test, Attempt.test_id == Test.id
    ).filter(Test.org_id == org_id, Snapshot.created_at < cutoff).all()
    for s in rows:
        path = os.path.join(current_app.config["SNAPSHOT_UPLOAD_DIR"], s.filename)
        try:
            if os.path.exists(path):
                os.remove(path)
        except OSError:
            pass
        db.session.delete(s)
    return len(rows)


def apply_retention_policies():
    """Run every configured retention rule once, per organization (plus a
    final platform-wide error-log sweep), and return a dict of
    {category: rows_deleted} totaled across every org. Each org's
    effective_days() is resolved independently, so two orgs with different
    overrides each get cleaned up on their own schedule in the same run."""
    totals = {
        "recordings": 0, "snapshots": 0, "ended_login_sessions": 0,
        "login_security_events": 0, "activity_log_entries": 0, "notification_log_entries": 0,
        "error_log_entries": 0,
    }

    # Every org_id that actually has data attached to it: real Organizations,
    # plus None for super_admin accounts (which aren't tied to one).
    org_ids = [o.id for o in Organization.query.all()] + [None]

    for org_id in org_ids:
        org = Organization.query.get(org_id) if org_id is not None else None

        totals["recordings"] += _delete_recordings_for_org(
            org_id, _cutoff(effective_days("recording_retention_days", org))
        )
        totals["snapshots"] += _delete_snapshots_for_org(
            org_id, _cutoff(effective_days("snapshot_retention_days", org))
        )

        if org_id is not None:
            user_ids_subq = db.session.query(User.id).filter(User.org_id == org_id)
        else:
            user_ids_subq = db.session.query(User.id).filter(User.org_id.is_(None))

        totals["ended_login_sessions"] += LoginSession.query.filter(
            LoginSession.user_id.in_(user_ids_subq),
            LoginSession.is_active.is_(False),
            LoginSession.ended_at < _cutoff(effective_days("ended_login_session_retention_days", org)),
        ).delete(synchronize_session=False)

        totals["login_security_events"] += LoginSecurityEvent.query.filter(
            LoginSecurityEvent.user_id.in_(user_ids_subq),
            LoginSecurityEvent.created_at < _cutoff(effective_days("login_security_event_retention_days", org)),
        ).delete(synchronize_session=False)

        totals["activity_log_entries"] += AdminActivityLog.query.filter(
            AdminActivityLog.admin_id.in_(user_ids_subq),
            AdminActivityLog.created_at < _cutoff(effective_days("activity_log_retention_days", org)),
        ).delete(synchronize_session=False)

        totals["notification_log_entries"] += NotificationLog.query.filter(
            NotificationLog.user_id.in_(user_ids_subq),
            NotificationLog.created_at < _cutoff(effective_days("notification_log_retention_days", org)),
        ).delete(synchronize_session=False)

    # Platform-only category — errors aren't tied to a tenant.
    totals["error_log_entries"] = ErrorLog.query.filter(
        ErrorLog.occurred_at < _cutoff(effective_days("error_log_retention_days"))
    ).delete(synchronize_session=False)

    db.session.commit()
    return totals


def current_policy_summary():
    """The platform-wide effective retention windows, for display
    wherever a plain (label, days) list is enough — the editable /ops
    and /admin pages use effective_policy_for_org() instead, which also
    reports where each number is coming from."""
    return [(CATEGORY_LABELS[c], effective_days(c)) for c in _CATEGORY_CONFIG_KEYS]


def populate_form_from_policy(form, org_id):
    """Fill a RetentionPolicyForm's fields from that org's (or the
    platform's, for org_id=None) own override row — leaving fields blank
    where no override is set, so the form only ever shows what's actually
    been overridden, not the inherited/default value it would otherwise
    fall back to."""
    policy = get_policy(org_id)
    for category in _CATEGORY_CONFIG_KEYS:
        if hasattr(form, category):
            getattr(form, category).data = getattr(policy, category) if policy else None


def save_form_to_policy(form, org_id, user_id, categories):
    """Persist the given categories from a validated RetentionPolicyForm
    into that org's (or the platform's) override row, creating the row if
    this is its first override. `categories` scopes which fields get
    written — ORG_EDITABLE_CATEGORIES for an org admin's own form,
    PLATFORM_EDITABLE_CATEGORIES (which also includes error log) for the
    platform-wide form — so an org admin's POST can never accidentally
    touch a category their form doesn't even render."""
    policy = get_or_create_policy(org_id)
    for category in categories:
        setattr(policy, category, getattr(form, category).data)
    policy.updated_by_id = user_id
    db.session.commit()
    return policy
