"""Public Terms of Service and Privacy Policy pages, plus the consent
capture at registration (see app.auth.register / app.forms.RegisterForm's
accepted_terms field). These routes are intentionally unauthenticated —
anyone should be able to read them before creating an account, not just
after logging in.

IMPORTANT: the page content itself is a starting draft, not a substitute
for legal review. It's written to accurately describe what this specific
codebase actually does (biometric face descriptors, webcam/mic recording,
ID-document OCR, retention windows, multi-tenant data handling) rather
than generic boilerplate, but the applicable regulations for a real
deployment (GDPR, COPPA, state-level biometric privacy laws like Illinois'
BIPA, etc.) depend on where the deployment operates and who its students
are — see the "Known limitations" section of the README.
"""

from flask import Blueprint, render_template, current_app

bp = Blueprint("legal", __name__)


@bp.route("/terms")
def terms():
    return render_template("legal/terms.html", version=current_app.config["TERMS_VERSION"])


@bp.route("/privacy")
def privacy():
    return render_template("legal/privacy.html", version=current_app.config["TERMS_VERSION"])
