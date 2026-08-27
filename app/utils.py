from functools import wraps
from datetime import datetime, timedelta

from flask import abort, request, current_app
from flask_login import current_user, login_required


def admin_required(f):
    @wraps(f)
    @login_required
    def wrapper(*args, **kwargs):
        if current_user.role != "admin":
            abort(403)
        return f(*args, **kwargs)

    return wrapper


def student_required(f):
    @wraps(f)
    @login_required
    def wrapper(*args, **kwargs):
        if current_user.role != "student":
            abort(403)
        return f(*args, **kwargs)

    return wrapper


def get_client_ip():
    """Best-effort client IP for rate limiting. Trusts X-Forwarded-For's
    first entry when present — fine behind a properly configured reverse
    proxy that sets/overwrites the header, but note that without such a
    proxy this header can be spoofed by the client. Falls back to the raw
    socket address otherwise."""
    forwarded = request.headers.get("X-Forwarded-For", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.remote_addr or "unknown"


def is_rate_limited(action, max_attempts, window_minutes):
    """Per-IP rate limit check for an abuse-prone unauthenticated endpoint.
    Records this attempt (so repeated invalid submissions still count) and
    returns True if the caller's IP has already hit max_attempts for this
    action within the trailing window_minutes, meaning the request should
    be rejected. Controlled by the RATE_LIMIT_ENABLED config flag so it can
    be disabled for local dev/testing."""
    if not current_app.config.get("RATE_LIMIT_ENABLED", True):
        return False

    from app import db
    from app.models import IpRateLimit

    ip = get_client_ip()
    window_start = datetime.utcnow() - timedelta(minutes=window_minutes)
    count = IpRateLimit.query.filter(
        IpRateLimit.ip_address == ip,
        IpRateLimit.action == action,
        IpRateLimit.created_at >= window_start,
    ).count()
    if count >= max_attempts:
        return True

    db.session.add(IpRateLimit(ip_address=ip, action=action))
    db.session.commit()
    return False
