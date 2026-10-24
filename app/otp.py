"""Emailed one-time codes (OTP) for password reset and email verification.

Replaces the old signed-link flow: the user is emailed a short numeric code
and types it into the page they already have open, instead of clicking a
link. Codes are single-use, expire (OTP_TTL_MINUTES), die after
OTP_MAX_ATTEMPTS wrong guesses, and only an HMAC of the code is stored.
"""
import hashlib
import hmac
import secrets
from datetime import datetime, timedelta

from flask import current_app

from app import db
from app.models import EmailOtp

PURPOSE_RESET = "password_reset"
PURPOSE_VERIFY = "email_verify"

OTP_LENGTH = 6

# verify_otp / mark results
OK = "ok"
INVALID = "invalid"
EXPIRED = "expired"
LOCKED = "locked"
NONE = "none"


def _cfg(name, default):
    return current_app.config.get(name, default)


def _hash(email, purpose, code):
    msg = f"{email}|{purpose}|{code}".encode()
    return hmac.new(current_app.config["SECRET_KEY"].encode(), msg, hashlib.sha256).hexdigest()


def _norm(email):
    return (email or "").lower().strip()


def seconds_until_resend(email, purpose):
    """0 if a new code may be issued now, else how many seconds to wait."""
    email = _norm(email)
    last = (
        EmailOtp.query.filter_by(email=email, purpose=purpose)
        .order_by(EmailOtp.created_at.desc())
        .first()
    )
    if not last:
        return 0
    wait = _cfg("OTP_RESEND_COOLDOWN_SECONDS", 30) - (datetime.utcnow() - last.created_at).total_seconds()
    return max(0, int(wait) + (1 if wait > 0 else 0))


def issue_otp(email, purpose):
    """Create a fresh code for (email, purpose), invalidating any earlier
    unused ones, and return the plaintext code to email out."""
    email = _norm(email)
    now = datetime.utcnow()
    for old in EmailOtp.query.filter_by(email=email, purpose=purpose, consumed_at=None).all():
        old.consumed_at = now
        old.redeemed = True  # superseded — can never unlock anything
    code = "".join(str(secrets.randbelow(10)) for _ in range(OTP_LENGTH))
    row = EmailOtp(
        email=email,
        purpose=purpose,
        code_hash=_hash(email, purpose, code),
        created_at=now,
        expires_at=now + timedelta(minutes=_cfg("OTP_TTL_MINUTES", 10)),
    )
    db.session.add(row)
    db.session.commit()
    return code


def verify_otp(email, purpose, code):
    """Check a submitted code. Returns (result, row). On OK the row is
    marked consumed (single use). Wrong guesses count against the row."""
    email = _norm(email)
    code = (code or "").strip().replace(" ", "")
    row = (
        EmailOtp.query.filter_by(email=email, purpose=purpose, consumed_at=None)
        .order_by(EmailOtp.created_at.desc())
        .first()
    )
    if not row:
        return NONE, None
    now = datetime.utcnow()
    if row.expires_at < now:
        return EXPIRED, row
    if row.attempts >= _cfg("OTP_MAX_ATTEMPTS", 5):
        return LOCKED, row
    if not (code.isdigit() and len(code) == OTP_LENGTH) or not hmac.compare_digest(
        row.code_hash, _hash(email, purpose, code)
    ):
        row.attempts += 1
        db.session.commit()
        if row.attempts >= _cfg("OTP_MAX_ATTEMPTS", 5):
            return LOCKED, row
        return INVALID, row
    row.consumed_at = now
    db.session.commit()
    return OK, row


def is_redeemable(otp_id, email, purpose, max_age_minutes=10):
    """A verified-but-not-yet-used code: lets the page that just verified
    the OTP go on to do the sensitive thing (set a new password) within a
    short window, exactly once."""
    row = EmailOtp.query.get(otp_id) if otp_id else None
    if not row or row.email != _norm(email) or row.purpose != purpose:
        return None
    if not row.consumed_at or row.redeemed:
        return None
    if datetime.utcnow() - row.consumed_at > timedelta(minutes=max_age_minutes):
        return None
    return row


def redeem(row):
    row.redeemed = True
    db.session.commit()


def failure_message(result):
    if result == EXPIRED:
        return "That code has expired. Please request a new one."
    if result == LOCKED:
        return "Too many incorrect attempts. Please request a new code."
    if result == NONE:
        return "No active code found. Please request a new one."
    return "Incorrect code. Please check the email and try again."


def otp_email_body(name, purpose, code):
    minutes = _cfg("OTP_TTL_MINUTES", 10)
    if purpose == PURPOSE_RESET:
        what = "reset your Exam Proctoring password"
    else:
        what = "verify your Exam Proctoring email"
    return (
        f"Hi {name},\n\n"
        f"Your one-time code to {what} is:\n\n    {code}\n\n"
        f"It expires in {minutes} minutes and can be used once. "
        "If you didn't request this, you can ignore this email — your account is unchanged."
    )
