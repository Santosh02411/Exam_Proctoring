"""Exam Session Device Management — records which device/browser is taking
each attempt, and prevents the same account from running that exam in two
places at once.

This is a different, finer-grained control than app.security's account-
level SINGLE_SESSION_PER_ACCOUNT: that one stops a whole *login* from
being active in two places; this one stops a single *exam attempt* from
being actively driven from two tabs/devices at once, which matters even
when the account-level control is off (or the two tabs share one login
session, which account-level session tracking can't see at all — two tabs
in the same browser look identical to it).

How ownership works:
  - Every GET on the exam page (a fresh start, or resuming after a
    refresh/crash) calls claim_session(). Ownership is tracked at two
    levels: session_owner_key identifies the *browser* (a random id tied
    to the Flask login-session cookie — see get_browser_id, not a
    per-tab identifier), and session_token identifies the current claim
    itself. A claim from the same browser is always allowed straight
    through, whether that's an ordinary page refresh or a second tab
    opened in that same browser — neither is "a different device running
    the exam". A claim from a *different* browser is only allowed when
    the existing claim isn't actively live anymore (see is_session_stale);
    otherwise it's refused. This is deliberately a browser-level
    boundary, not a tab-level one: distinguishing "the same tab reloaded"
    from "a second tab was opened" server-side, without any client-side
    coordination, isn't reliably possible, and treating every refresh as
    a new device would make ordinary use painful. In the same-browser,
    second-tab case, this still gets you a working single-active-session
    outcome in practice — the newer tab's claim supersedes the older
    tab's token, so the older tab's next autosave/heartbeat is rejected
    as superseded (see validate_session_token) even though both tabs were
    allowed to *load*.
  - If another tab IS actively holding the attempt right now, the claim is
    refused: app.student.start_test shows a blocking page instead of the
    exam, and a "concurrent_session_blocked" violation is logged via
    app.proctoring.record_violation — same bookkeeping (violation count,
    suspicion score, auto-termination threshold, high-risk alert) as any
    other violation, since a deliberate second concurrent session is a
    genuine integrity concern, not just noise.
  - Once a tab holds the session, every autosave/submit/heartbeat call
    from it must present the matching session_token (validate_session_token,
    called from app.student) or it's rejected as superseded — this is what
    stops an older, since-replaced tab from continuing to save answers
    after a newer one has taken over.
  - On a normal refresh or tab close, proctor.js fires a best-effort
    navigator.sendBeacon to /api/proctor/session/release on pagehide,
    which clears session_token immediately — so a plain F5 reload claims
    again right away instead of waiting out the staleness window below.
    A real crash (no unload event fires) instead relies on that window.

Caveat worth being upfront about: validate_session_token trusts a request
that omits a token entirely (treats it as "nothing to compare against"),
so a determined student could bypass the per-request check by tampering
with the page's own JS to stop sending it. That's the same trust boundary
every other client-reported proctoring signal in this app already has
(nothing stops a tampered client from just not calling reportEvent
either) — the real, tamper-proof enforcement is claim_session()'s
browser-identity and staleness checks at start_test, which depend only on
the login-session cookie and server-side timing, not on anything the
client chooses to send.
"""

import json
import secrets
from datetime import datetime

from flask import request, session as flask_session

from app.utils import get_client_ip

BROWSER_ID_KEY = "exam_browser_id"


def get_browser_id():
    """A random id tied to this browser's Flask login-session cookie —
    stable across page refreshes and across multiple tabs of the same
    browser (they share one cookie), but different for a different
    browser/device or a fresh login elsewhere. Created lazily on first
    use and then persisted in the session cookie like any other
    session value."""
    browser_id = flask_session.get(BROWSER_ID_KEY)
    if not browser_id:
        browser_id = secrets.token_hex(16)
        flask_session[BROWSER_ID_KEY] = browser_id
    return browser_id


def _device_fingerprint():
    return (request.headers.get("User-Agent") or "unknown")[:300]


def is_session_stale(attempt, stale_after_seconds):
    """True if nobody's actively holding this attempt right now — either
    it was never claimed, was explicitly released (session_token cleared
    by the pagehide beacon), or its last heartbeat/autosave is older than
    stale_after_seconds (crashed/closed without a chance to release)."""
    if not attempt.session_token or not attempt.session_last_seen_at:
        return True
    age = (datetime.utcnow() - attempt.session_last_seen_at).total_seconds()
    return age > stale_after_seconds


def claim_session(attempt, stale_after_seconds, enforce=True, extra_device_info=None):
    """Try to make the current request's browser the attempt's active
    session. Returns True (and commits the updated attempt) if this is
    the same browser that already held it, if nothing else currently
    holds it, or if `enforce` is False (device info is still recorded and
    the token still rotates — only the blocking behavior is skipped).
    Returns False, with nothing changed, if a *different* browser is
    actively holding it right now and enforcement is on — the caller
    (app.student.start_test) is responsible for showing a blocked page
    and logging the violation in that case (see
    record_blocked_concurrent_session)."""
    from app import db  # local import: avoids a circular import at module load time

    browser_id = get_browser_id()
    same_browser = attempt.session_owner_key == browser_id

    if enforce and attempt.session_token and not same_browser and not is_session_stale(attempt, stale_after_seconds):
        return False

    ip = get_client_ip()
    device = _device_fingerprint()
    # Only worth an audit note when a *different* browser is taking over —
    # the same browser reclaiming (an ordinary refresh, or a second tab)
    # happens constantly during normal use and isn't itself noteworthy.
    should_log_resume = attempt.session_token is not None and not same_browser

    attempt.session_token = secrets.token_hex(24)
    attempt.session_owner_key = browser_id
    attempt.session_started_at = datetime.utcnow()
    attempt.session_last_seen_at = datetime.utcnow()
    attempt.ip_address = ip

    info = {"user_agent": device, "ip_address": ip}
    if extra_device_info:
        # Only take the handful of keys we actually expect from the client
        # — this is untrusted input, and device_info is displayed as-is to
        # admins reviewing the attempt.
        for key in ("screen", "timezone", "language", "platform"):
            value = extra_device_info.get(key)
            if isinstance(value, str) and value:
                info[key] = value[:120]
    attempt.device_info = json.dumps(info)

    if should_log_resume:
        from app.proctoring import record_violation

        record_violation(
            attempt, "session_resumed", "warning",
            "Exam session resumed on a different device/browser after a disconnect or crash.",
        )
    else:
        db.session.commit()
    return True


def record_blocked_concurrent_session(attempt):
    """Log the violation for a claim that was refused because another tab
    was actively holding the attempt — see claim_session's False return."""
    from app.proctoring import record_violation

    ip = get_client_ip()
    record_violation(
        attempt, "concurrent_session_blocked", "violation",
        f"A second session tried to open this exam from {ip} while another was already active.",
    )


def validate_session_token(attempt, token):
    """True if `token` matches the attempt's current active session — and,
    as a side effect, refreshes session_last_seen_at so is_session_stale()
    keeps seeing this session as live. A request that supplies no token at
    all is trusted (see the module docstring's caveat about this being a
    best-effort check, not the actual enforcement mechanism); an attempt
    with no active claim at all (session_token is None — predates this
    feature, or was released) is also always valid, since there's nothing
    to have been superseded by."""
    if not attempt.session_token or not token:
        return True
    if token != attempt.session_token:
        return False
    attempt.session_last_seen_at = datetime.utcnow()
    return True


def release_session(attempt, token):
    """Give up this tab's claim on the attempt, if it's the one currently
    holding it — called from the /api/proctor/session/release beacon on
    page unload/refresh. Clearing session_token immediately (rather than
    waiting for it to go stale) is what makes an ordinary refresh feel
    instant instead of imposing the staleness delay meant for actual
    crashes. A mismatched/missing token is a no-op, not an error — the
    beacon is fire-and-forget and arriving late/out of order is normal."""
    if attempt.session_token and token and attempt.session_token == token:
        attempt.session_token = None
