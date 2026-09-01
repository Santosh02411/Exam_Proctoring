"""Login session tracking, single-session enforcement, and login anomaly
detection ("Exam Integrity Controls" — device/session tracking, IP
tracking, prevent simultaneous sessions, detect unusual login/session
changes, optional VPN/proxy detection).

Flow:
  - app.auth.login() calls register_login() right after login_user()
    succeeds. That creates the LoginSession row, optionally ends any other
    active session for the account (single-session enforcement), and
    checks the new login against the account's recent session history for
    anomalies (new device / new location), writing a LoginSecurityEvent
    for anything notable.
  - app.auth.logout() calls end_session().
  - app.__init__'s before_request hook calls enforce_single_session() on
    every authenticated request, so a browser tab whose LoginSession was
    ended by a newer login elsewhere gets logged out on its very next
    request rather than continuing to work until it happens to hit a
    route that checks.
"""

import secrets
import urllib.request
import urllib.error
import json as _json
from datetime import datetime

from flask import session, request, current_app, flash
from flask_login import current_user, logout_user

from app import db
from app.models import LoginSession, LoginSecurityEvent
from app.utils import get_client_ip

SESSION_TOKEN_KEY = "login_session_token"

# How many of a user's past sessions to look at when deciding whether a new
# login's IP/device is "new". Small and recent on purpose — this is meant to
# flag "this doesn't look like your usual pattern", not maintain a full
# history comparison.
ANOMALY_LOOKBACK = 10


def _device_fingerprint():
    return (request.headers.get("User-Agent") or "unknown")[:300]


def register_login(user):
    """Create the LoginSession for a just-completed login, enforce the
    single-active-session policy if enabled, and check for login anomalies.
    Call this immediately after flask_login.login_user() succeeds."""
    ip = get_client_ip()
    device = _device_fingerprint()

    _check_anomalies(user, ip, device)

    if current_app.config.get("SINGLE_SESSION_PER_ACCOUNT", True):
        _end_other_sessions(user, reason="replaced_by_new_login")

    token = secrets.token_hex(32)
    login_session = LoginSession(
        user_id=user.id, session_token=token, ip_address=ip, user_agent=device,
    )
    db.session.add(login_session)
    db.session.commit()
    session[SESSION_TOKEN_KEY] = token
    return login_session


def _end_other_sessions(user, reason):
    others = LoginSession.query.filter_by(user_id=user.id, is_active=True).all()
    if not others:
        return
    for s in others:
        s.is_active = False
        s.ended_at = datetime.utcnow()
        s.end_reason = reason
    db.session.add(LoginSecurityEvent(
        user_id=user.id, event_type="concurrent_session_replaced",
        details=f"Ended {len(others)} prior active session(s) on new login.",
    ))


def _check_anomalies(user, ip, device):
    """Compare a fresh login's IP/device against the account's recent
    session history and log a LoginSecurityEvent for anything that looks
    new. First-ever login for an account is never flagged — there's no
    history yet to be unusual relative to."""
    recent = LoginSession.query.filter_by(user_id=user.id).order_by(
        LoginSession.created_at.desc()
    ).limit(ANOMALY_LOOKBACK).all()
    if not recent:
        return

    known_ips = {s.ip_address for s in recent}
    known_devices = {s.user_agent for s in recent}

    if ip not in known_ips:
        db.session.add(LoginSecurityEvent(
            user_id=user.id, event_type="new_location",
            details=f"Login from a new IP address ({ip}) not seen in the last {len(recent)} session(s).",
        ))
    if device not in known_devices:
        db.session.add(LoginSecurityEvent(
            user_id=user.id, event_type="new_device",
            details="Login from a browser/device not seen in recent sessions.",
        ))

    vpn_reason = check_vpn_or_proxy(ip)
    if vpn_reason:
        db.session.add(LoginSecurityEvent(
            user_id=user.id, event_type="vpn_or_proxy_suspected", details=vpn_reason,
        ))


def check_vpn_or_proxy(ip):
    """Optional VPN/proxy heuristic. Disabled by default (VPN_DETECTION_ENABLED)
    since it depends on a third-party IP-reputation API the deployer has to
    configure (VPN_DETECTION_API_URL/KEY) — there's no reliable way to
    detect this from the request alone. Returns a human-readable reason
    string if the configured API flags the IP, else None. Any failure
    (missing config, network error, unexpected response) is swallowed and
    treated as "can't tell" rather than "suspicious", so a misconfigured or
    unreachable API never blocks or falsely flags a login."""
    if not current_app.config.get("VPN_DETECTION_ENABLED", False):
        return None
    api_url = current_app.config.get("VPN_DETECTION_API_URL")
    api_key = current_app.config.get("VPN_DETECTION_API_KEY")
    if not api_url or not api_key or ip in ("unknown", "127.0.0.1", "::1"):
        return None
    try:
        url = f"{api_url}?ip={ip}&key={api_key}"
        with urllib.request.urlopen(url, timeout=current_app.config.get("VPN_DETECTION_TIMEOUT_SECONDS", 3)) as resp:
            data = _json.loads(resp.read().decode("utf-8"))
        if data.get("vpn") or data.get("proxy") or data.get("tor"):
            return f"IP flagged by VPN/proxy detection provider (raw: {data})"
    except (urllib.error.URLError, ValueError, TimeoutError, OSError):
        return None
    return None


def end_session(reason="logout"):
    """Mark the current browser session's LoginSession as ended. Call this
    from app.auth.logout(). Best-effort — a missing/already-ended session
    row (e.g. it was already replaced by a newer login elsewhere) is fine
    and shouldn't block logging out."""
    token = session.pop(SESSION_TOKEN_KEY, None)
    if not token:
        return
    login_session = LoginSession.query.filter_by(session_token=token, is_active=True).first()
    if login_session:
        login_session.is_active = False
        login_session.ended_at = datetime.utcnow()
        login_session.end_reason = reason
        db.session.commit()


def enforce_single_session():
    """before_request hook: if the current browser session's token no
    longer matches an active LoginSession (because a newer login elsewhere
    ended it), log this session out. No-op for anonymous requests, and
    no-op when single-session enforcement is off or the session predates
    this feature (no token stored) — the latter just means "not tracked",
    not "invalid"."""
    if not current_user.is_authenticated:
        return
    if not current_app.config.get("SINGLE_SESSION_PER_ACCOUNT", True):
        return
    token = session.get(SESSION_TOKEN_KEY)
    if not token:
        return
    login_session = LoginSession.query.filter_by(session_token=token).first()
    if login_session and not login_session.is_active:
        logout_user()
        session.pop(SESSION_TOKEN_KEY, None)
        flash("You have been logged out because your account was signed in from another device or location.", "warning")
    elif login_session:
        login_session.last_seen_at = datetime.utcnow()
        db.session.commit()
