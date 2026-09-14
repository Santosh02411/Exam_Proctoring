"""Two-Factor Authentication — TOTP (Time-based One-Time Password, RFC 6238)
second factor for login, the same mechanism as Google Authenticator/Authy/
1Password. Deliberately opt-in per account rather than mandatory: forcing it
on every existing account with no migration path would just lock people out,
so this is a security feature a user (most importantly an admin or proctor,
who this matters most for) turns on for themselves from their profile page.

Flow:
  1. setup_totp() generates a fresh secret + a QR code the user scans with
     an authenticator app.
  2. The user enters the 6-digit code the app is now showing to prove the
     scan worked — confirm_totp() checks it and only THEN sets
     User.totp_confirmed = True. A secret that was generated but never
     confirmed can't gate login (see requires_2fa below), so an
     interrupted setup can't accidentally lock the account out.
  3. From then on, app.auth.login's normal password check succeeds into a
     *pending* second step (see PENDING_2FA_SESSION_KEY) rather than
     calling login_user() directly — verify_login_code() completes it.
  4. Ten single-use backup codes are issued at confirmation time, for the
     "lost my phone" case — each one is individually hashed (never stored
     or shown again after generation) and removed from the list the
     moment it's used.
"""
import secrets
from datetime import datetime

import pyotp
import qrcode
import qrcode.image.svg
from io import BytesIO
from werkzeug.security import generate_password_hash, check_password_hash

from app import db

PENDING_2FA_SESSION_KEY = "pending_2fa_user_id"
BACKUP_CODE_COUNT = 10


def generate_secret():
    return pyotp.random_base32()


def provisioning_uri(user, secret):
    return pyotp.totp.TOTP(secret).provisioning_uri(name=user.email, issuer_name="Exam Proctoring")


def qr_code_data_uri(uri):
    """Render the otpauth:// URI as an inline SVG data URI — no temp file,
    no extra route to serve an image from, just an <img src="..."> the
    setup template can drop in directly."""
    factory = qrcode.image.svg.SvgImage
    img = qrcode.make(uri, image_factory=factory, box_size=8)
    buf = BytesIO()
    img.save(buf)
    import base64
    encoded = base64.b64encode(buf.getvalue()).decode("ascii")
    return f"data:image/svg+xml;base64,{encoded}"


def verify_code(secret, code):
    if not secret or not code:
        return False
    code = code.strip().replace(" ", "")
    try:
        return pyotp.totp.TOTP(secret).verify(code, valid_window=1)
    except Exception:
        return False


def generate_backup_codes():
    """Returns (plain_codes, hashed_json) — plain_codes is shown to the
    user exactly once (at generation time); hashed_json is what actually
    gets stored, following the same never-store-a-usable-secret-in-
    plaintext principle as password_hash."""
    plain_codes = [secrets.token_hex(5) for _ in range(BACKUP_CODE_COUNT)]
    hashed = [generate_password_hash(c) for c in plain_codes]
    import json
    return plain_codes, json.dumps(hashed)


def consume_backup_code(user, code):
    """Check `code` against user's remaining backup codes; if it matches,
    remove that one (single-use) and return True. Never raises on a
    malformed/missing backup_codes column — just means no valid codes."""
    if not user.backup_codes or not code:
        return False
    import json
    try:
        hashed_codes = json.loads(user.backup_codes)
    except (TypeError, ValueError):
        return False

    code = code.strip().replace(" ", "").lower()
    for hashed in hashed_codes:
        if check_password_hash(hashed, code):
            hashed_codes.remove(hashed)
            user.backup_codes = json.dumps(hashed_codes)
            db.session.commit()
            return True
    return False


def requires_2fa(user):
    return bool(user.totp_secret and user.totp_confirmed)
