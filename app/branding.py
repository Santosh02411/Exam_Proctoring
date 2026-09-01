"""Org-level branding/theming: each organization can upload its own logo
and set a primary accent color, applied to its users' UI via a small CSS
override injected by base.html (see current_org_branding(), wired up as a
Flask context processor in app.__init__ so every template has it without
each route needing to pass it explicitly).

There's no per-org subdomain or custom domain in this app, so branding
only ever applies *after* login (there's no way to know which
organization an anonymous visitor belongs to before they authenticate) —
the login/register pages always show the platform's default look.
"""

import os
import re
import uuid

from flask import current_app
from flask_login import current_user

from app import db
from app.utils import is_impersonating, impersonated_org_id

HEX_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")


def validate_color(value):
    """Return the normalized (lowercase) hex color, or raise ValueError
    with a message suitable for flashing directly if it's not a valid
    "#rrggbb" string."""
    value = (value or "").strip()
    if not HEX_COLOR_RE.match(value):
        raise ValueError("Enter a color as a 6-digit hex code, e.g. #0b6ef6.")
    return value.lower()


def _branding_dir():
    d = current_app.config["ORG_BRANDING_DIR"]
    os.makedirs(d, exist_ok=True)
    return d


def save_logo(org, file_storage):
    """Save an uploaded logo for `org`, replacing any previous one (a new
    upload gets a fresh generated filename, and the old file — if any —
    is removed, so stale logos never pile up on disk). Raises ValueError
    on an empty/oversized/wrong-extension upload; the caller should catch
    it and flash the message. Returns nothing — updates and commits
    org.logo_filename itself."""
    filename = (file_storage.filename or "").strip()
    if not filename:
        raise ValueError("Choose a logo file to upload.")
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext not in current_app.config["ORG_LOGO_ALLOWED_EXTS"]:
        allowed = ", ".join(sorted(current_app.config["ORG_LOGO_ALLOWED_EXTS"]))
        raise ValueError(f"Unsupported file type — allowed: {allowed}.")

    file_storage.seek(0, os.SEEK_END)
    size = file_storage.tell()
    file_storage.seek(0)
    if size > current_app.config["ORG_LOGO_MAX_BYTES"]:
        max_mb = current_app.config["ORG_LOGO_MAX_BYTES"] / (1024 * 1024)
        raise ValueError(f"Logo is too large — max {max_mb:.0f} MB.")

    old_filename = org.logo_filename
    new_filename = f"org{org.id}_{uuid.uuid4().hex[:8]}.{ext}"
    file_storage.save(os.path.join(_branding_dir(), new_filename))

    org.logo_filename = new_filename
    db.session.commit()

    if old_filename:
        old_path = os.path.join(_branding_dir(), old_filename)
        try:
            if os.path.exists(old_path):
                os.remove(old_path)
        except OSError:
            pass


def remove_logo(org):
    """Clear this org's logo (falls back to the platform default look for
    that slot) and delete the file on disk."""
    if not org.logo_filename:
        return
    old_path = os.path.join(_branding_dir(), org.logo_filename)
    org.logo_filename = None
    db.session.commit()
    try:
        if os.path.exists(old_path):
            os.remove(old_path)
    except OSError:
        pass


def _effective_org_for_branding():
    """The organization whose branding should apply to the current
    request: the org an authenticated non-super_admin user belongs to, or
    the org a super_admin is currently impersonating. None otherwise
    (anonymous visitor, or a super_admin not impersonating — the
    Organizations/Operations areas always show the platform's own look)."""
    if not current_user.is_authenticated:
        return None
    if current_user.role == "super_admin":
        if is_impersonating():
            from app.models import Organization

            return Organization.query.get(impersonated_org_id())
        return None
    return current_user.organization


def current_org_branding():
    """{'name', 'logo_url', 'primary_color'} for the current request's
    effective organization, or None if no org-specific branding should
    apply. Registered as a Flask context processor (see app.__init__), so
    templates just reference `org_branding` directly."""
    org = _effective_org_for_branding()
    if org is None:
        return None
    if not org.logo_filename and not org.primary_color:
        return None
    from flask import url_for

    return {
        "name": org.name,
        "logo_url": url_for("branding_logo", org_id=org.id) if org.logo_filename else None,
        "primary_color": org.primary_color,
    }
