"""
PDF rendering of a member statement (reportlab).
"""

from io import BytesIO
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from apps.core.templatetags.currency import ugx

NAVY = colors.HexColor("#1f3a5f")
GRID = colors.HexColor("#c9ced6")


def _table(data, widths, header=True, align_right_from=None):
    table = Table(data, colWidths=widths, repeatRows=1 if header else 0)
    style = [
        ("FONTSIZE", (0, 0), (-1, -1), 8.5),
        ("GRID", (0, 0), (-1, -1), 0.4, GRID),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]
    if header:
        style += [
            ("BACKGROUND", (0, 0), (-1, 0), NAVY),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ]
    if align_right_from is not None:
        style.append(("ALIGN", (align_right_from, 0), (-1, -1), "RIGHT"))
    table.setStyle(TableStyle(style))
    return table


def render_statement_pdf(statement, organisation="Financial Cycle Unit (FCU)"):
    member = statement["member"]
    cycle = statement["cycle"]

    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=15 * mm,
        rightMargin=15 * mm,
        topMargin=15 * mm,
        bottomMargin=15 * mm,
        title=f"Statement {member.member_code} Cycle {cycle.cycle_number}",
        author=organisation,
    )

    styles = getSampleStyleSheet()
    title = ParagraphStyle("t", parent=styles["Title"], textColor=NAVY, fontSize=18, spaceAfter=2)
    small = ParagraphStyle("s", parent=styles["Normal"], fontSize=8.5, textColor=colors.grey)
    h2 = ParagraphStyle("h2", parent=styles["Heading2"], textColor=NAVY, fontSize=11, spaceBefore=10)
    cell = ParagraphStyle("c", parent=styles["Normal"], fontSize=8)

    story = [
        Paragraph(organisation, title),
        Paragraph("Member statement", styles["Heading3"]),
        Paragraph(
            f"<b>{member.member_code}</b> &middot; {escape(member.full_name)}<br/>"
            f"Cycle {cycle.cycle_number} &middot; {escape(cycle.name)} "
            f"({cycle.start_date:%d %b %Y} - {cycle.end_date:%d %b %Y})<br/>"
            f"Membership status: {member.get_status_display()}",
            styles["Normal"],
        ),
        Spacer(1, 6),
        Paragraph("Summary", h2),
        _table(
            [
                ["", "Target", "Paid", "Outstanding"],
                ["Deposits", ugx(statement["deposit_target"]), ugx(statement["deposits_paid"]),
                 ugx(statement["deposits_outstanding"])],
                ["Emergency", ugx(statement["emergency_target"]), ugx(statement["emergency_paid"]),
                 ugx(statement["emergency_outstanding"])],
            ],
            [40 * mm, 45 * mm, 45 * mm, 45 * mm],
            align_right_from=1,
        ),
        Spacer(1, 4),
        _table(
            [
                ["Cycle target", "Met" if statement["target_met"] else "Not yet met"],
                ["Available savings (all cycles, emergency excluded)", ugx(statement["available_savings"])],
                ["Loan outstanding", ugx(statement["loan_outstanding"])],
            ],
            [110 * mm, 65 * mm],
            header=False,
            align_right_from=1,
        ),
        Paragraph("Payment ledger", h2),
    ]

    data = [["Date", "Description", "Method / ref", "Deposit", "Emergency", "Deposits to date"]]
    for row in statement["rows"]:
        description = escape(row["description"])
        if row["note"]:
            description += f"<br/><i>{escape(row['note'])}</i>"
        reference = escape(row["method"]) + (
            f"<br/>{escape(row['reference'])}" if row["reference"] else ""
        )
        amount = ugx(row["amount"])
        data.append(
            [
                f"{row['date']:%d %b %Y}",
                Paragraph(description, cell),
                Paragraph(reference, cell),
                amount if row["is_deposit"] else "",
                "" if row["is_deposit"] else amount,
                ugx(row["running_deposits"]),
            ]
        )
    if len(data) == 1:
        data.append(["", "No payments recorded for this cycle.", "", "", "", ""])

    ledger = _table(
        data,
        [22 * mm, 55 * mm, 30 * mm, 24 * mm, 24 * mm, 26 * mm],
        align_right_from=3,
    )
    for index, row in enumerate(statement["rows"], start=1):
        if not row["counts_here"]:
            ledger.setStyle(TableStyle([("TEXTCOLOR", (0, index), (-1, index), colors.grey)]))
            if row["reversed"]:
                ledger.setStyle(TableStyle([("LINEBELOW", (0, index), (-1, index), 0.6, colors.grey)]))
    story += [
        ledger,
        Spacer(1, 8),
        Paragraph(
            "Reversed payments and payments that count towards another cycle are shown in grey "
            "and are not included in the totals.",
            small,
        ),
        Paragraph(f"Generated {statement['generated_at']:%d %b %Y %H:%M}", small),
    ]

    doc.build(story)
    return buffer.getvalue()
