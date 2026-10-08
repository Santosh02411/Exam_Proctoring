"""Per-attempt incident report (PDF): the proctoring evidence and the human
decision on one attempt, in a single document a reviewer can attach to a
disciplinary case, an appeal, or a hand-off. Complements org_reports (which
is the organization-wide summary) — this is the one-attempt, evidence-level
counterpart.

Deliberately text-only: snapshots and recordings stay in the app behind
their access checks rather than being copied into a file that then travels
by email. Every piece of user-supplied text (names, event details, notes,
the student's appeal) goes through _esc() before it reaches reportlab's
Paragraph, since Paragraph parses an XML-like markup and would otherwise
either choke on a stray "<" or render markup a student typed into their
appeal.
"""

import io
from datetime import datetime
from xml.sax.saxutils import escape

from app.proctoring import EVENT_TYPE_LABELS


def _esc(value):
    return escape("" if value is None else str(value))


def _ts(dt):
    return dt.strftime("%Y-%m-%d %H:%M:%S") if dt else "—"


def render_attempt_report_pdf(attempt, events, risk, review=None, appeal=None):
    """Raw PDF bytes for one attempt. `risk` is proctoring.compute_suspicion_score's
    result; `review`/`appeal` are optional model rows."""
    from reportlab.lib.pagesizes import letter
    from reportlab.lib import colors
    from reportlab.lib.units import inch
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
    from reportlab.lib.styles import getSampleStyleSheet

    test = attempt.test
    student = attempt.student
    org = test.organization if hasattr(test, "organization") else None

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=letter, title=f"Incident report — {student.name} — {test.title}")
    styles = getSampleStyleSheet()
    small = styles["Normal"]
    story = []

    def grid(rows, col_widths, header=True):
        t = Table(rows, colWidths=col_widths, repeatRows=1 if header else 0)
        style = [
            ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("PADDING", (0, 0), (-1, -1), 5),
        ]
        if header:
            style += [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2d2d2d")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f5f5f5")]),
            ]
        t.setStyle(TableStyle(style))
        return t

    def kv(pairs):
        rows = [[Paragraph(f"<b>{_esc(k)}</b>", small), Paragraph(_esc(v), small)] for k, v in pairs]
        return grid(rows, [1.7 * inch, 4.8 * inch], header=False)

    story.append(Paragraph("Proctoring Incident Report", styles["Title"]))
    story.append(Paragraph(
        f"Generated {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')}"
        + (f" &nbsp;|&nbsp; {_esc(org.name)}" if org is not None and getattr(org, "name", None) else ""),
        small,
    ))
    story.append(Spacer(1, 0.2 * inch))

    story.append(Paragraph("Attempt", styles["Heading2"]))
    score = "—" if attempt.score is None else f"{attempt.score} / {attempt.max_marks()}"
    story.append(kv([
        ("Student", f"{student.name} ({student.email})"),
        ("Test", f"{test.title} ({test.test_code})"),
        ("Status", attempt.status.replace("_", " ").capitalize()),
        ("Started", _ts(attempt.started_at)),
        ("Ended", _ts(attempt.submitted_at)),
        ("Score", score),
        ("Violations recorded", attempt.violation_count),
        ("Warnings used", attempt.total_warning_count),
        ("Termination reason", attempt.termination_reason or "—"),
        ("IP address", attempt.ip_address or "—"),
    ]))
    story.append(Spacer(1, 0.2 * inch))

    story.append(Paragraph("Risk assessment", styles["Heading2"]))
    story.append(kv([
        ("Suspicion score", f"{risk['score']} / 100 ({risk['level'].capitalize()})"),
        ("Distinct violation types", risk.get("distinct_types", 0)),
    ]))
    for reason in risk.get("reasons", []):
        story.append(Paragraph(f"• {_esc(reason)}", small))
    for signal in risk.get("signals", []):
        story.append(Paragraph(f"• {_esc(signal)}", small))
    story.append(Spacer(1, 0.2 * inch))

    story.append(Paragraph("Reviewer decision", styles["Heading2"]))
    if review:
        story.append(kv([
            ("Decision", review.decision.capitalize()),
            ("Reviewed by", review.reviewer.name if review.reviewer else "—"),
            ("Reviewed at", _ts(review.reviewed_at)),
            ("Notes", review.notes or "—"),
        ]))
    else:
        story.append(Paragraph("Not yet reviewed.", small))
    story.append(Spacer(1, 0.2 * inch))

    if appeal:
        story.append(Paragraph("Student appeal", styles["Heading2"]))
        story.append(kv([
            ("Submitted", _ts(appeal.created_at)),
            ("Status", appeal.status.capitalize()),
            ("Student's reason", appeal.reason),
            ("Reviewer note", appeal.admin_note or "—"),
            ("Decided by", appeal.resolved_by.name if appeal.resolved_by else "—"),
        ]))
        story.append(Spacer(1, 0.2 * inch))

    story.append(Paragraph(f"Proctoring event log ({len(events)})", styles["Heading2"]))
    if events:
        rows = [["Time (UTC)", "Event", "Severity", "Conf.", "Details"]]
        for e in events:
            label = EVENT_TYPE_LABELS.get(e.event_type, e.event_type.replace("_", " "))
            rows.append([
                Paragraph(_esc(e.created_at.strftime("%H:%M:%S") if e.created_at else "—"), small),
                Paragraph(_esc(e.event_type.replace("_", " ")), small),
                Paragraph(_esc(e.severity), small),
                Paragraph("—" if e.confidence is None else f"{e.confidence:.2f}", small),
                Paragraph(_esc(e.details or label), small),
            ])
        story.append(grid(rows, [0.8 * inch, 1.5 * inch, 0.8 * inch, 0.5 * inch, 2.9 * inch]))
    else:
        story.append(Paragraph("No proctoring events were recorded.", small))

    doc.build(story)
    return buf.getvalue()
