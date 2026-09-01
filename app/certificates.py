"""Certificate Generation — a downloadable PDF certificate for a student
who passes a test that has certificates turned on (see
Test.certificate_enabled). The PDF is rendered fresh on every download
with reportlab rather than generated once and stored on disk: it's cheap
to produce and this avoids adding another category of per-attempt file
that retention/cleanup would need to know about. What *does* persist is
the Certificate row (see app.models.Certificate) — just the issuance
record and a verification code, not the rendered bytes — so a re-download
doesn't mint a new code and a third party can verify a certificate later
without needing the file itself (see app.student.verify_certificate).
"""

import io
import os
import secrets
from datetime import datetime

from reportlab.lib.pagesizes import landscape, letter
from reportlab.lib.units import inch
from reportlab.lib.colors import HexColor
from reportlab.pdfgen import canvas
from reportlab.lib.utils import ImageReader

from app import db
from app.models import Certificate
from app.notifications import pending_grading

DEFAULT_ACCENT = "#0b6ef6"


def is_eligible(attempt):
    """A certificate can be issued when the test has certificates turned
    on, the attempt actually finished with a final (not still-pending-
    manual-grading) score, and that score cleared the passing mark. A
    terminated attempt is never eligible, regardless of score — matching
    how app.student.result already treats a termination as an automatic
    fail."""
    test = attempt.test
    if not test.certificate_enabled:
        return False
    if attempt.status != "submitted":
        return False
    if attempt.score is None:
        return False
    if pending_grading(attempt):
        return False
    return attempt.score >= test.passing_marks


def get_or_create(attempt):
    """The attempt's Certificate row, creating one (with a fresh
    verification code) the first time it's requested. Does not check
    is_eligible — callers (the download route, and the eligibility check
    it already ran) are responsible for that; this just persists the
    issuance."""
    if attempt.certificate:
        return attempt.certificate
    cert = Certificate(
        attempt_id=attempt.id,
        certificate_code="CERT-" + secrets.token_hex(8).upper(),
    )
    db.session.add(cert)
    db.session.commit()
    return cert


def _org_logo_path(org):
    if not org.logo_filename:
        return None
    from flask import current_app

    path = os.path.join(current_app.config["ORG_BRANDING_DIR"], org.logo_filename)
    return path if os.path.exists(path) else None


def render_pdf(attempt, certificate):
    """Returns the certificate as PDF bytes. Landscape letter, a simple
    bordered layout — org logo/name if the org has branding set up (see
    app.branding), student name, test title, score, issue date, the
    verification code, and a signatory line if the org has one configured
    (Organization.certificate_signatory_name/_title)."""
    test = attempt.test
    org = test.organization
    student = attempt.student
    total_marks = attempt.max_marks()
    score_pct = round(100 * attempt.score / total_marks, 1) if total_marks else None
    accent = HexColor(org.primary_color or DEFAULT_ACCENT)

    buf = io.BytesIO()
    page_size = landscape(letter)
    c = canvas.Canvas(buf, pagesize=page_size)
    width, height = page_size

    # Border
    c.setStrokeColor(accent)
    c.setLineWidth(3)
    c.rect(0.5 * inch, 0.5 * inch, width - 1 * inch, height - 1 * inch)
    c.setLineWidth(0.75)
    c.rect(0.62 * inch, 0.62 * inch, width - 1.24 * inch, height - 1.24 * inch)

    y = height - 1.3 * inch

    logo_path = _org_logo_path(org)
    if logo_path:
        try:
            img = ImageReader(logo_path)
            iw, ih = img.getSize()
            display_h = 0.6 * inch
            display_w = display_h * (iw / ih)
            c.drawImage(
                img, (width - display_w) / 2, y - display_h + 0.1 * inch,
                width=display_w, height=display_h, mask="auto", preserveAspectRatio=True,
            )
            y -= display_h + 0.15 * inch
        except Exception:
            pass  # A corrupt/unreadable logo file should never block certificate generation.

    c.setFont("Helvetica-Bold", 14)
    c.setFillColor(HexColor("#555555"))
    c.drawCentredString(width / 2, y, org.name)
    y -= 0.5 * inch

    c.setFont("Helvetica-Bold", 30)
    c.setFillColor(HexColor("#1a1a1a"))
    c.drawCentredString(width / 2, y, "Certificate of Achievement")
    y -= 0.55 * inch

    c.setFont("Helvetica", 13)
    c.setFillColor(HexColor("#444444"))
    c.drawCentredString(width / 2, y, "This certifies that")
    y -= 0.55 * inch

    c.setFont("Helvetica-Bold", 26)
    c.setFillColor(accent)
    c.drawCentredString(width / 2, y, student.name)
    y -= 0.5 * inch

    c.setFont("Helvetica", 13)
    c.setFillColor(HexColor("#444444"))
    c.drawCentredString(width / 2, y, "has successfully completed")
    y -= 0.4 * inch

    c.setFont("Helvetica-Bold", 18)
    c.setFillColor(HexColor("#1a1a1a"))
    c.drawCentredString(width / 2, y, test.title)
    y -= 0.4 * inch

    c.setFont("Helvetica", 12)
    c.setFillColor(HexColor("#444444"))
    score_line = f"Score: {attempt.score:g} / {total_marks:g}"
    if score_pct is not None:
        score_line += f"  ({score_pct}%)"
    c.drawCentredString(width / 2, y, score_line)
    y -= 0.6 * inch

    # Footer row: issue date (left), signatory (right)
    footer_y = 1.05 * inch
    issue_date = (certificate.issued_at or datetime.utcnow()).strftime("%B %d, %Y")
    c.setFont("Helvetica", 10)
    c.setFillColor(HexColor("#444444"))
    c.drawString(1.1 * inch, footer_y, f"Issued: {issue_date}")
    c.drawString(1.1 * inch, footer_y - 0.2 * inch, f"Certificate ID: {certificate.certificate_code}")

    if org.certificate_signatory_name:
        c.setFont("Helvetica-Bold", 12)
        c.setFillColor(HexColor("#1a1a1a"))
        c.drawRightString(width - 1.1 * inch, footer_y, org.certificate_signatory_name)
        if org.certificate_signatory_title:
            c.setFont("Helvetica", 10)
            c.setFillColor(HexColor("#444444"))
            c.drawRightString(width - 1.1 * inch, footer_y - 0.2 * inch, org.certificate_signatory_title)

    c.showPage()
    c.save()
    buf.seek(0)
    return buf.getvalue()
