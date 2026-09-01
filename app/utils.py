from functools import wraps
from datetime import datetime, timedelta

from flask import abort, request, current_app, session
from flask_login import current_user, login_required


def roles_required(*roles):
    """Generic RBAC decorator: allows only the given User.role values.
    admin_required / student_required below are the two single-role cases
    kept as named decorators since they're used everywhere; routes that
    need to be shared across several roles (e.g. admin + examiner) use
    this directly — see app.admin's content_access / review_access."""
    def decorator(f):
        @wraps(f)
        @login_required
        def wrapper(*args, **kwargs):
            if current_user.role not in roles:
                abort(403)
            return f(*args, **kwargs)

        return wrapper

    return decorator


def roles_required_or_impersonating(*roles):
    """Like roles_required, but also lets a super_admin through on a GET
    request while they're impersonating an organization (see
    app.organizations' impersonate/stop_impersonating routes and this
    module's is_impersonating()) — used for content_access/review_access
    only (browsing tests, questions, results, analytics, the proctor
    queue), so a platform admin can look at an org's exam content for
    support purposes without needing that org's own admin credentials.

    This deliberately does NOT extend to admin_required-gated routes
    (user management, retention settings, security/activity logs, ID
    verification) — those stay strictly limited to that org's own admin,
    impersonation or not. And even within content_access/review_access,
    only GET requests are allowed through this path: a super_admin who is
    impersonating can look, not act — every POST (create/edit/delete/
    grade/assign/...) still requires actually being that org's own admin
    or examiner. Regular admin/examiner/proctor users are unaffected by
    any of this; the impersonation branch is only ever consulted for the
    super_admin role."""
    def decorator(f):
        @wraps(f)
        @login_required
        def wrapper(*args, **kwargs):
            if current_user.role in roles:
                return f(*args, **kwargs)
            if current_user.role == "super_admin" and is_impersonating() and request.method == "GET":
                return f(*args, **kwargs)
            abort(403)

        return wrapper

    return decorator


admin_required = roles_required("admin")
student_required = roles_required("student")
# Platform-level role that manages Organizations themselves (tenants) rather
# than exam content within one — see app.organizations. Not reachable via
# public registration; created via the `flask create-super-admin` CLI command.
super_admin_required = roles_required("super_admin")


def is_super_admin():
    return current_user.is_authenticated and current_user.role == "super_admin"


def is_impersonating():
    """True when the current session has an active support-view
    impersonation of a specific organization (see
    app.organizations.impersonate_organization/stop_impersonating). Only
    ever meaningful for a super_admin — the session key is only ever set
    by a super_admin-only route, but this checks the role too so a
    lingering session value can never leak scope to a different account
    that happens to reuse the session (e.g. after logout/login as someone
    else in the same browser)."""
    return (
        current_user.is_authenticated
        and current_user.role == "super_admin"
        and session.get("impersonate_org_id") is not None
    )


def impersonated_org_id():
    return session.get("impersonate_org_id") if is_impersonating() else None


def current_org_id():
    """The organization the logged-in user is currently acting within: the
    impersonated organization for a super_admin mid-impersonation,
    otherwise the org the account itself belongs to (None for an
    unauthenticated request or a non-impersonating super_admin)."""
    if not current_user.is_authenticated:
        return None
    if is_impersonating():
        return impersonated_org_id()
    return current_user.org_id


def org_scope(query, model):
    """Filter a list query down to the current user's organization —
    the core of tenant data isolation. A non-impersonating super_admin
    bypasses this (the Organizations/Operations areas are the only place
    they operate outside impersonation, and cross-org visibility there is
    intentional); everyone else — including a super_admin who is
    currently impersonating a specific org — only ever sees that one
    org's rows. `model` is the mapped class being queried, used to find
    its org_id column (directly, or via model.test.has(...) for rows
    that hang off a Test rather than carrying their own org_id — pass
    the already-adjusted query with that filter applied yourself for
    those and skip this helper)."""
    if is_super_admin() and not is_impersonating():
        return query
    return query.filter(model.org_id == current_org_id())


def ensure_same_org(obj, org_id=None):
    """Abort 403 unless obj (or the given org_id) belongs to the
    organization the current user is acting within (see current_org_id()
    — the impersonated org, if any, otherwise the account's own). This is
    the tenant-isolation check for fetch-by-id routes (get_or_404 then
    act) where org_scope's queryset filtering doesn't apply — e.g.
    /admin/tests/<id>/edit. A non-impersonating super_admin bypasses this,
    same as org_scope; a super_admin who is impersonating does not — they
    only ever pass for the org they're impersonating, exactly like that
    org's own admin would. Pass org_id directly for objects that don't
    carry their own org_id but hang off one that does (e.g. an Attempt via
    its Test) rather than adding a redundant column."""
    if is_super_admin() and not is_impersonating():
        return
    target_org_id = org_id if org_id is not None else getattr(obj, "org_id", None)
    if target_org_id != current_org_id():
        abort(403)


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
