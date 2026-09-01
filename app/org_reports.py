"""Organization-level report: headcounts by role, test/attempt volume, and
a pass-rate figure — used both by the super_admin's org detail page
(app.organizations) and by an org's own self-service report page
(app.admin.org_report), so the two always show the same numbers computed
the same way. Also home to the CSV/PDF export renderers for that report,
shared by both blueprints' export routes.
"""

import csv
import io
from datetime import datetime

from app.models import User, Test, Attempt


def build_org_summary(org):
    """Lightweight org-level report: headcounts by role, test/attempt
    volume, and a pass-rate style figure — enough to see how active a
    tenant is without reaching into per-test detail that belongs to that
    org's own admins."""
    users = User.query.filter_by(org_id=org.id).all()
    role_counts = {}
    for u in users:
        role_counts[u.role] = role_counts.get(u.role, 0) + 1

    tests = Test.query.filter_by(org_id=org.id).all()
    test_ids = [t.id for t in tests]
    attempts = Attempt.query.filter(Attempt.test_id.in_(test_ids)).all() if test_ids else []
    finished = [a for a in attempts if a.status in ("submitted", "terminated")]
    passed = [a for a in finished if a.test.passing_marks and (a.score or 0) >= a.test.passing_marks]

    return {
        "org": org,
        "role_counts": role_counts,
        "total_users": len(users),
        "total_tests": len(tests),
        "published_tests": sum(1 for t in tests if t.status == "published"),
        "total_attempts": len(attempts),
        "finished_attempts": len(finished),
        "pass_rate": round(100 * len(passed) / len(finished), 1) if finished else None,
    }


def render_summary_csv(summary):
    """A single-sheet CSV of the org report — one metric per row, plus a
    breakdown of user counts by role. Returns the CSV text (str), ready to
    hand to a Response with a .csv Content-Disposition."""
    buf = io.StringIO()
    writer = csv.writer(buf)
    org = summary["org"]
    writer.writerow(["Organization Report"])
    writer.writerow(["Organization", org.name])
    writer.writerow(["Status", org.status])
    writer.writerow(["Generated", datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")])
    writer.writerow([])
    writer.writerow(["Metric", "Value"])
    writer.writerow(["Total users", summary["total_users"]])
    writer.writerow(["Total tests", summary["total_tests"]])
    writer.writerow(["Published tests", summary["published_tests"]])
    writer.writerow(["Total attempts", summary["total_attempts"]])
    writer.writerow(["Finished attempts", summary["finished_attempts"]])
    writer.writerow(["Pass rate (%)", summary["pass_rate"] if summary["pass_rate"] is not None else "N/A"])
    writer.writerow([])
    writer.writerow(["Users by Role", "Count"])
    for role, count in summary["role_counts"].items():
        writer.writerow([role, count])
    return buf.getvalue()


def render_summary_pdf(summary):
    """A one-page PDF of the org report, via reportlab. Returns raw PDF
    bytes, ready to hand to a Response with a .pdf Content-Disposition."""
    from reportlab.lib.pagesizes import letter
    from reportlab.lib import colors
    from reportlab.lib.units import inch
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
    from reportlab.lib.styles import getSampleStyleSheet

    org = summary["org"]
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=letter, title=f"{org.name} — Organization Report")
    styles = getSampleStyleSheet()
    story = []

    story.append(Paragraph(f"Organization Report — {org.name}", styles["Title"]))
    story.append(Paragraph(
        f"Status: {org.status} &nbsp;&nbsp;|&nbsp;&nbsp; Generated: {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')}",
        styles["Normal"],
    ))
    story.append(Spacer(1, 0.3 * inch))

    metrics_data = [
        ["Metric", "Value"],
        ["Total users", summary["total_users"]],
        ["Total tests", summary["total_tests"]],
        ["Published tests", summary["published_tests"]],
        ["Total attempts", summary["total_attempts"]],
        ["Finished attempts", summary["finished_attempts"]],
        ["Pass rate", f"{summary['pass_rate']}%" if summary["pass_rate"] is not None else "N/A"],
    ]
    metrics_table = Table(metrics_data, colWidths=[2.5 * inch, 2.5 * inch])
    metrics_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2d2d2d")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f5f5f5")]),
        ("PADDING", (0, 0), (-1, -1), 6),
    ]))
    story.append(metrics_table)
    story.append(Spacer(1, 0.3 * inch))

    story.append(Paragraph("Users by Role", styles["Heading2"]))
    role_data = [["Role", "Count"]] + [[role, count] for role, count in summary["role_counts"].items()]
    if len(role_data) == 1:
        role_data.append(["(no users)", ""])
    role_table = Table(role_data, colWidths=[2.5 * inch, 2.5 * inch])
    role_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2d2d2d")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f5f5f5")]),
        ("PADDING", (0, 0), (-1, -1), 6),
    ]))
    story.append(role_table)

    doc.build(story)
    return buf.getvalue()
