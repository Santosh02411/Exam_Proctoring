"""Error monitoring — records genuinely unexpected exceptions so a
super_admin has something to look at besides server logs. Wired up as a
generic Exception handler in app.__init__; see that module for why
HTTPException (404, 403, validation aborts, etc.) is deliberately excluded.
"""

import traceback as tb_module

from flask import request
from flask_login import current_user

from app import db
from app.models import ErrorLog


def log_error(exc):
    """Best-effort: logging a failure while already handling a request
    failure must never itself crash the response, so any error here is
    swallowed rather than propagated."""
    try:
        user_id = current_user.id if current_user.is_authenticated else None
    except Exception:
        user_id = None

    try:
        entry = ErrorLog(
            endpoint=request.endpoint,
            method=request.method,
            path=request.path,
            error_type=type(exc).__name__,
            error_message=str(exc)[:1000],
            traceback="".join(tb_module.format_exception(type(exc), exc, exc.__traceback__))[:8000],
            user_id=user_id,
        )
        db.session.add(entry)
        db.session.commit()
    except Exception:
        db.session.rollback()
        return

    # Real-time alerting: check whether this error tips the recent error
    # count over the spike threshold. Kept in its own try/except and run
    # after the log entry's own commit, so a failure here (or the alerting
    # module itself misbehaving) can never prevent the error from being
    # recorded — which is the one thing this function absolutely must do.
    try:
        from app import alerting

        alerting.check_error_rate()
    except Exception:
        db.session.rollback()
