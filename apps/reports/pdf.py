"""
PDF of the bi-annual report (reportlab).
"""

from io import BytesIO
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from apps.core.templatetags.currency import ugx

NAVY = colors.HexColor("#1f3a5f")
GRID = colors.HexColor("#c9ced6")


def _table(data, widths, right_from=1, total_row=False):
    table = Table(data, colWidths=widths, repeatRows=1)
    style = [
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("GRID", (0, 0), (-1, -1), 0.4, GRID),
        ("BACKGROUND", (0, 0), (-1, 0), NAVY),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("ALIGN", (right_from, 0), (-1, -1), "RIGHT"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]
    if total_row:
        style.append(("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"))
    table.setStyle(TableStyle(style))
    return table


def render_cycle_pdf(report, organisation="Financial Cycle Unit (FCU)"):
    cycle = report["cycle"]
    c, loans, inv, pos = report["contributions"], report["loans"], report["investments"], report["position"]

    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=landscape(A4), leftMargin=14 * mm, rightMargin=14 * mm,
        topMargin=14 * mm, bottomMargin=14 * mm,
        title=f"Financial report Cycle {cycle.cycle_number}", author=organisation,
    )
    styles = getSampleStyleSheet()
    title = ParagraphStyle("t", parent=styles["Title"], textColor=NAVY, fontSize=18, spaceAfter=2)
    h2 = ParagraphStyle("h2", parent=styles["Heading2"], textColor=NAVY, fontSize=11, spaceBefore=10, spaceAfter=3)
    small = ParagraphStyle("s", parent=styles["Normal"], fontSize=8, textColor=colors.grey)
    cell = ParagraphStyle("c", parent=styles["Normal"], fontSize=8)

    story = [
        Paragraph(organisation, title),
        Paragraph("Financial report", styles["Heading3"]),
        Paragraph(
            f"Cycle {cycle.cycle_number} &middot; {escape(cycle.name)} "
            f"({cycle.start_date:%d %b %Y} - {cycle.end_date:%d %b %Y}) &middot; "
            f"{cycle.get_status_display()}<br/>Prepared {report['generated_at']:%d %b %Y %H:%M}",
            styles["Normal"],
        ),
        Paragraph("1. Contributions", h2),
        _table(
            [
                ["", "Target", "Collected", "Outstanding", "Collection rate"],
                ["Deposits", ugx(c["expected"]), ugx(c["collected"]), ugx(c["outstanding"]), f"{c['collection_rate']}%"],
                ["Emergency", ugx(c["emergency_expected"]), ugx(c["emergency_collected"]),
                 ugx(c["emergency_outstanding"]), f"{c['emergency_collection_rate']}%"],
            ],
            [50 * mm, 45 * mm, 45 * mm, 45 * mm, 40 * mm],
        ),
        Paragraph(
            f"{c['members_met_target']} of {c['members']} members met their deposit target. "
            f"Eligible members: {c['eligible_members']}.", small,
        ),
        Paragraph("2. Loans disbursed in this cycle", h2),
        _table(
            [
                ["Loans", "Disbursed", "Repayments received", "Recovered from savings", "Interest earned", "Outstanding"],
                [str(loans["count"]), ugx(loans["disbursed"]), ugx(loans["repayments_received"]),
                 ugx(loans["recovered_from_savings"]), ugx(loans["interest_earned"]), ugx(loans["outstanding"])],
            ],
            [25 * mm, 42 * mm, 45 * mm, 48 * mm, 40 * mm, 40 * mm],
            right_from=0,
        ),
        Paragraph("3. Investments", h2),
        _table(
            [["Investment", "Type", "Invested", "Earnings", "Worth now", "Total return"]]
            + [
                [Paragraph(escape(p["investment"].name), cell), p["investment"].get_kind_display(),
                 ugx(p["invested"]), ugx(p["income"]), ugx(p["market_value"]), ugx(p["total_return"])]
                for p in inv["positions"]
            ]
            + [["Total", "", ugx(inv["invested_total"]), "", ugx(inv["market_value"]), ""]],
            [70 * mm, 40 * mm, 42 * mm, 36 * mm, 42 * mm, 42 * mm],
            right_from=2, total_row=True,
        ),
        Paragraph(
            f"This cycle: invested {ugx(inv['invested_in_cycle'])}; earnings {ugx(inv['income_in_cycle'])}; "
            f"profit shared with members {ugx(inv['distributed'])}.", small,
        ),
        Paragraph("4. Expenses", h2),
        _table(
            [["Category", "Items", "Spent"]]
            + [[row["label"], str(row["count"]), ugx(row["total"])] for row in report["expenses"]["by_category"]]
            + [["Total", str(report["expenses"]["count"]), ugx(report["expenses"]["total"])]],
            [120 * mm, 40 * mm, 60 * mm],
            right_from=1, total_row=True,
        ),
        Paragraph("5. Financial position (as of now)", h2),
        _table(
            [
                ["Members' deposits", "Emergency fund", "Loans receivable", "Investments", "Derived cash*"],
                [ugx(pos["members_deposits"]), ugx(pos["emergency_fund"]), ugx(pos["loans_receivable"]),
                 ugx(pos["investments"]), ugx(pos["derived_cash"])],
            ],
            [52 * mm, 52 * mm, 52 * mm, 52 * mm, 52 * mm],
            right_from=0,
        ),
        Paragraph(f"Other savings owed to members (unit trust interest): {ugx(report['position']['other_savings_owed'])}.", small),
        Paragraph(
            "*Worked out from every recorded movement of money, including expenses. Membership fees are not "
            "recorded in the system, so check this against the bank and cash balances.", small,
        ),
        PageBreak(),
        Paragraph("6. Members", h2),
    ]

    rows = [["Member", "Name", "Status", "Deposit target", "Paid", "Recovered", "Outstanding",
             "Emergency", "Em. paid", "Em. outstanding", "Target"]]
    for r in report["members"]:
        rows.append(
            [r["member"].member_code, Paragraph(escape(r["member"].full_name), cell), r["status"],
             ugx(r["deposit_target"]), ugx(r["deposits_paid"]), ugx(r["recovered"]), ugx(r["deposits_outstanding"]),
             ugx(r["emergency_target"]), ugx(r["emergency_paid"]), ugx(r["emergency_outstanding"]),
             "Met" if r["target_met"] else "Not met"]
        )
    widths = [w * mm for w in (16, 44, 18, 26, 24, 22, 24, 22, 20, 24, 16)]
    story.append(_table(rows, widths, right_from=3))
    story += [
        Spacer(1, 6),
        Paragraph("Prepared for presentation to members under Manual 10.2.", small),
    ]

    doc.build(story)
    return buffer.getvalue()
