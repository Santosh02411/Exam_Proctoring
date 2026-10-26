"""Staff invitations — the controlled way admin / examiner / proctor accounts
come into existence.

Public registration only ever creates students. Everything privileged goes
through here:

  * An existing admin (for their own organization) or a super_admin (for any
    organization) creates an invitation for ONE email address and ONE role.
  * The invitee follows the emailed single-use, expiring link and sets their
    own name/phone/password. Role and organization come from the invitation
    row, never from anything the invitee submits.
  * super_admin accounts are never invitable — they exist only via the
    `flask create-super-admin` CLI.

Only a SHA-256 hash of the token is stored.
"""
import hashlib
import secrets
from datetime import datetime, timedelta

from flask import current_app, url_for

from app import db
from app.models import Organization, StaffInvitation, User

STAFF_ROLES = ("admin", "examiner", "proctor")
ROLE_LABELS = {"admin": "Admin", "examiner": "Examiner", "proctor": "Proctor"}


class InvitationError(ValueError):
    """A user-presentable reason an invitation couldn't be created/accepted."""


def hash_token(raw):
    return hashlib.sha256((raw or "").encode()).hexdigest()


def roles_grantable_by(user):
    """Roles `user` may invite people into. Examiners/proctors/students: none."""
    if user.role in ("admin", "super_admin"):
        return STAFF_ROLES
    return ()


def create_invitation(inviter, org, email, role):
    """Create (and return) (invitation, raw_token). Raises InvitationError."""
    email = (email or "").lower().strip()
    if role not in STAFF_ROLES:
        raise InvitationError("That role cannot be granted by invitation.")
    if role not in roles_grantable_by(inviter):
        raise InvitationError("You are not allowed to invite people into that role.")
    if inviter.role != "super_admin" and inviter.org_id != org.id:
        raise InvitationError("You can only invite people into your own organization.")
    if org.status != "active":
        raise InvitationError("That organization is not active.")
    if not email or "@" not in email:
        raise InvitationError("Enter a valid email address.")
    if User.query.filter_by(email=email).first():
        raise InvitationError("An account with that email already exists.")

    now = datetime.utcnow()
    # A newer invitation supersedes any earlier still-open one for the same
    # address+org, so there's never more than one live link per person.
    for old in StaffInvitation.query.filter_by(org_id=org.id, email=email, accepted_at=None, revoked_at=None).all():
        old.revoked_at = now

    raw = secrets.token_urlsafe(32)
    invitation = StaffInvitation(
        org_id=org.id, email=email, role=role, token_hash=hash_token(raw), invited_by_id=inviter.id,
        created_at=now, expires_at=now + timedelta(hours=int(current_app.config.get("INVITATION_TTL_HOURS", 72))),
    )
    db.session.add(invitation)
    db.session.commit()
    return invitation, raw


def accept_url(raw_token):
    return url_for("auth.accept_invitation", token=raw_token, _external=True)


def invitation_email_body(invitation, raw_token):
    hours = int(current_app.config.get("INVITATION_TTL_HOURS", 72))
    return (
        f"You've been invited to join {invitation.organization.name} on Exam Proctoring "
        f"as {ROLE_LABELS[invitation.role]}.\n\n"
        f"Set up your account here (single-use link, valid for {hours} hours):\n{accept_url(raw_token)}\n\n"
        "If you weren't expecting this, ignore this email — no account is created unless you open the link."
    )


def find_open(raw_token):
    """The still-usable invitation for this token, or None (unknown, expired,
    revoked, already accepted, or its organization no longer active)."""
    if not raw_token:
        return None
    inv = StaffInvitation.query.filter_by(token_hash=hash_token(raw_token)).first()
    if not inv or inv.status != "pending":
        return None
    if not inv.organization or inv.organization.status != "active":
        return None
    return inv


def accept(invitation, name, phone, password, terms_accepted=False):
    """Atomically consume the invitation and create the account. The claim is
    a single conditional UPDATE, so two simultaneous submissions of the same
    link cannot both create an account. Returns the new User."""
    from app.models import gen_user_id

    now = datetime.utcnow()
    claimed = (
        db.session.query(StaffInvitation)
        .filter(
            StaffInvitation.id == invitation.id,
            StaffInvitation.accepted_at.is_(None),
            StaffInvitation.revoked_at.is_(None),
            StaffInvitation.expires_at > now,
        )
        .update({"accepted_at": now}, synchronize_session=False)
    )
    if claimed != 1:
        db.session.rollback()
        raise InvitationError("This invitation is no longer valid.")
    if User.query.filter_by(email=invitation.email).first():
        db.session.rollback()
        raise InvitationError("An account with that email already exists.")

    user = User(
        user_id=gen_user_id(invitation.role),
        name=name.strip(),
        email=invitation.email,
        phone=phone.strip(),
        role=invitation.role,           # from the invitation, never from the form
        status="active",
        email_verified=True,            # they proved control of the address by receiving the link
        org_id=invitation.org_id,       # likewise
    )
    user.set_password(password)
    if terms_accepted:
        user.terms_accepted_at = now
        user.terms_version_accepted = current_app.config.get("TERMS_VERSION")
    db.session.add(user)
    db.session.flush()
    db.session.query(StaffInvitation).filter(StaffInvitation.id == invitation.id).update(
        {"accepted_user_id": user.id}, synchronize_session=False
    )
    db.session.commit()
    return user


def revoke(invitation):
    if invitation.status == "pending":
        invitation.revoked_at = datetime.utcnow()
        db.session.commit()
        return True
    return False


def deliver(invitation, raw_token):
    """Email the invitation. Returns (sent: bool, dev_link_or_None). A mail
    failure never loses the invitation (it exists and can be re-sent); the raw
    link is only ever handed back for on-screen display in development when
    no SMTP server is configured."""
    from app.config_validation import dev_mail_fallback_allowed
    from app.email_utils import send_email

    try:
        mode = send_email(
            invitation.email,
            f"You're invited to {invitation.organization.name} on Exam Proctoring",
            invitation_email_body(invitation, raw_token),
        )
    except Exception:
        current_app.logger.exception("failed to send staff invitation email")
        return False, None
    dev_link = accept_url(raw_token) if (mode == "logged" and dev_mail_fallback_allowed(current_app.config)) else None
    return True, dev_link
