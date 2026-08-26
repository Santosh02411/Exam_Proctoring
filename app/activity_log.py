from flask_login import current_user

from app import db
from app.models import AdminActivityLog


def log_activity(action, description):
    """Record an admin action for the audit trail. Best-effort — a logging
    failure should never break the action it's describing."""
    try:
        entry = AdminActivityLog(admin_id=current_user.id, action=action, description=description)
        db.session.add(entry)
        db.session.commit()
    except Exception:
        db.session.rollback()
