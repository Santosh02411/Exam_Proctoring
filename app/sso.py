"""Single Sign-On — Google and Microsoft OAuth2/OIDC login, built on
Authlib (the standard OAuth client library for Flask). Both providers are
entirely optional and independent: whichever ones have a client id/secret
configured (see config.py) show up as a "Sign in with..." button on the
login page; the other one — or both — simply doesn't appear if unset, and
password login is completely unaffected either way.

Account linking rules (see find_or_create_sso_user):
  1. A previous SSO login from this exact provider+subject -> that account.
  2. No SSO link yet, but a password-based account already exists with
     this verified email -> link SSO to it (either method works from
     here on).
  3. No existing account at all, but this email's domain matches an
     Organization.sso_domain claim -> self-service create a new student
     account under that org.
  4. None of the above -> rejected with a clear message. SSO is a login
     *method*, not a bypass for "anyone with a Google account can create
     an admin account" — a brand-new admin/examiner/proctor account still
     has to go through normal registration once; SSO can then be linked
     to it via rule 2 on their next login.

Deliberately NOT included: SAML. Every SAML integration is realistically
institution-specific (their IdP's metadata, attribute mapping, signing
cert exchange) in a way that OAuth2 with a well-known provider isn't —
there's no generic "just works" SAML setup to ship the way there is for
"click Sign in with Google." If a specific institution needs SAML, that's
a follow-up scoped to their actual IdP, not something to guess at generically here.
"""
import secrets

from flask import Blueprint, redirect, url_for, flash, current_app, session
from flask_login import login_user

from app import db
from app.models import User, Organization, gen_user_id
from app import security

bp = Blueprint("sso", __name__, url_prefix="/auth/sso")

_oauth = None  # set by init_oauth(app) — an Authlib OAuth registry


def init_oauth(app):
    """Call once from create_app(). Registers whichever providers have
    credentials configured; does nothing (leaves both disabled) if
    neither does, so an app with no SSO configured pays zero cost for
    this module beyond the import."""
    global _oauth
    from authlib.integrations.flask_client import OAuth
    _oauth = OAuth(app)

    if app.config.get("GOOGLE_CLIENT_ID") and app.config.get("GOOGLE_CLIENT_SECRET"):
        _oauth.register(
            name="google",
            client_id=app.config["GOOGLE_CLIENT_ID"],
            client_secret=app.config["GOOGLE_CLIENT_SECRET"],
            server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
            client_kwargs={"scope": "openid email profile"},
        )

    if app.config.get("MICROSOFT_CLIENT_ID") and app.config.get("MICROSOFT_CLIENT_SECRET"):
        tenant = app.config.get("MICROSOFT_TENANT", "common")
        _oauth.register(
            name="microsoft",
            client_id=app.config["MICROSOFT_CLIENT_ID"],
            client_secret=app.config["MICROSOFT_CLIENT_SECRET"],
            server_metadata_url=f"https://login.microsoftonline.com/{tenant}/v2.0/.well-known/openid-configuration",
            client_kwargs={"scope": "openid email profile"},
        )


def google_enabled():
    return bool(current_app.config.get("GOOGLE_CLIENT_ID") and current_app.config.get("GOOGLE_CLIENT_SECRET"))


def microsoft_enabled():
    return bool(current_app.config.get("MICROSOFT_CLIENT_ID") and current_app.config.get("MICROSOFT_CLIENT_SECRET"))


def find_or_create_sso_user(provider, subject, email, name):
    """Implements the account-linking rules described in the module
    docstring. Returns (user, error) — error is a user-facing string on
    failure, None on success, following the same (result, error) shape
    app.proctoring._build_question_from_form and friends already use
    elsewhere in this codebase."""
    email = (email or "").strip().lower()
    if not email:
        return None, "Your account with this provider doesn't have a verified email address to sign in with."

    existing = User.query.filter_by(sso_provider=provider, sso_subject=subject).first()
    if existing:
        return existing, None

    by_email = User.query.filter_by(email=email).first()
    if by_email:
        by_email.sso_provider = provider
        by_email.sso_subject = subject
        db.session.commit()
        return by_email, None

    domain = email.rsplit("@", 1)[-1]
    org = Organization.query.filter_by(sso_domain=domain).first()
    if not org:
        return None, (
            f"No account found for {email}, and '{domain}' isn't registered for self-service sign-in. "
            "Please register normally, or ask your administrator to enable SSO for your organization's domain."
        )

    user = User(
        user_id=gen_user_id("student"), name=name or email.split("@")[0], email=email,
        phone="", role="student", status="active", org_id=org.id,
        email_verified=True,  # trusting the IdP's own verified-email claim
        sso_provider=provider, sso_subject=subject,
    )
    # A real (if never-shown-to-the-user) password hash: password_hash is
    # NOT NULL, and this account is meant to always sign in via SSO — but
    # "forgot password" still needs to work later if the user wants a
    # fallback, and set_password/check_password require the column to
    # actually be a valid hash rather than blank.
    user.set_password(secrets.token_urlsafe(32))
    db.session.add(user)
    db.session.commit()
    return user, None


def _complete_sso_login(provider, userinfo):
    email = userinfo.get("email")
    subject = userinfo.get("sub") or userinfo.get("oid")
    name = userinfo.get("name")

    user, error = find_or_create_sso_user(provider, subject, email, name)
    if error:
        flash(error, "error")
        return redirect(url_for("auth.login"))

    if user.locked_until:
        from datetime import datetime
        if user.locked_until > datetime.utcnow():
            flash("This account is temporarily locked. Please try again later.", "error")
            return redirect(url_for("auth.login"))

    from app import twofa
    if twofa.requires_2fa(user):
        session[twofa.PENDING_2FA_SESSION_KEY] = user.id
        return redirect(url_for("auth.login_verify_2fa"))

    login_user(user)
    security.register_login(user)
    flash(f"Welcome, {user.name}!", "success")
    return redirect(url_for("index"))


@bp.route("/google/login")
def google_login():
    if not google_enabled():
        flash("Google sign-in isn't configured for this site.", "error")
        return redirect(url_for("auth.login"))
    redirect_uri = url_for("sso.google_callback", _external=True)
    return _oauth.google.authorize_redirect(redirect_uri)


@bp.route("/google/callback")
def google_callback():
    if not google_enabled():
        return redirect(url_for("auth.login"))
    token = _oauth.google.authorize_access_token()
    userinfo = token.get("userinfo") or _oauth.google.userinfo(token=token)
    return _complete_sso_login("google", userinfo)


@bp.route("/microsoft/login")
def microsoft_login():
    if not microsoft_enabled():
        flash("Microsoft sign-in isn't configured for this site.", "error")
        return redirect(url_for("auth.login"))
    redirect_uri = url_for("sso.microsoft_callback", _external=True)
    return _oauth.microsoft.authorize_redirect(redirect_uri)


@bp.route("/microsoft/callback")
def microsoft_callback():
    if not microsoft_enabled():
        return redirect(url_for("auth.login"))
    token = _oauth.microsoft.authorize_access_token()
    userinfo = token.get("userinfo") or _oauth.microsoft.userinfo(token=token)
    return _complete_sso_login("microsoft", userinfo)
