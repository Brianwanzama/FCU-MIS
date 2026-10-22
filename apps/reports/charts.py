"""
Numbers for the charts. Every function returns plain JSON-safe data (whole shillings as
integers), worked out from the same records as the reports and statements, so a chart can
never disagree with a table.
"""

from datetime import date
from decimal import Decimal

from django.db.models import Sum
from django.urls import reverse

from apps.contributions.models import Contribution
from apps.cycles.models import _add_months

# Status colours are fixed (never reused as a series) and always shown with an icon and a label.
STATUS_COLORS = {"met": "#0ca30c", "part": "#fab219", "none": "#d03b3b"}


def _int(value):
    return int(Decimal(value or 0).quantize(Decimal("1")))


def cycle_months(cycle):
    first = cycle.start_date.replace(day=1)
    return [_add_months(first, i) for i in range(cycle.duration_months)]


def monthly_deposits(cycle, member=None):
    """
    Deposits collected for each month of the cycle (by the month they were paid FOR),
    against the monthly target. For one member the target is their monthly savings; for the
    unit it is the unit target spread over the months. Reversed payments are left out.
    """

    months = cycle_months(cycle)

    payments = Contribution.objects.filter(
        cycle=cycle, settles_cycle__isnull=True, reversed_at__isnull=True,
        contribution_type=Contribution.ContributionType.MONTHLY,
    )

    if member is not None:
        payments = payments.filter(member=member)

    totals = {
        row["period_month"]: row["total"]
        for row in payments.values("period_month").annotate(total=Sum("amount"))
    }

    per_month_target = (
        cycle.monthly_savings_amount if member is not None
        else cycle.unit_deposit_target / cycle.duration_months
    )

    return {
        "labels": [m.strftime("%b %Y") for m in months],
        "collected": [_int(totals.get(m)) for m in months],
        "target": [_int(per_month_target)] * len(months),
        "label": "Deposits paid" if member is not None else "Deposits collected",
        "targetLabel": "Monthly target",
    }


def member_status(rows):
    """How many members have met the deposit target, part paid, or paid nothing."""

    met = sum(1 for r in rows if r["target_met"])
    nothing = sum(1 for r in rows if not r["target_met"] and r["deposits_paid"] <= 0)
    part = len(rows) - met - nothing

    return {
        "labels": ["✓ Target met", "! Part paid", "✕ Nothing paid"],
        "values": [met, part, nothing],
        "colors": [STATUS_COLORS["met"], STATUS_COLORS["part"], STATUS_COLORS["none"]],
        "measure": "Members",
    }


def member_progress(rows, link=True):
    """Each member's deposits paid and what is still to pay, largest payers first."""

    ordered = sorted(rows, key=lambda r: (-r["deposits_paid"], r["member"].member_code))

    data = {
        "labels": [f"{r['member'].member_code} {r['member'].full_name.title()}" for r in ordered],
        "paid": [_int(min(r["deposits_paid"], r["deposit_target"])) for r in ordered],
        "outstanding": [_int(max(r["deposit_target"] - r["deposits_paid"], 0)) for r in ordered],
    }

    if link:
        data["links"] = [reverse("members:detail", args=[r["member"].member_code]) for r in ordered]

    return data


def portfolio_allocation(book, keep=3):
    """Share of the portfolio's worth by investment: the largest few by name, the rest as Other."""

    held = sorted(
        (p for p in book["positions"] if p["market_value"] > 0),
        key=lambda p: -p["market_value"],
    )

    labels = [p["investment"].name for p in held[:keep]]
    values = [_int(p["market_value"]) for p in held[:keep]]

    rest = sum((p["market_value"] for p in held[keep:]), Decimal("0"))

    if rest > 0:
        labels.append("Other")
        values.append(_int(rest))

    return {"labels": labels, "values": values}


def investment_returns(book):
    """Total return, in percent, for each investment that has had money put in."""

    rows = [p for p in book["positions"] if p["invested"] > 0]

    return {
        "labels": [p["investment"].name for p in rows],
        "values": [float(p["return_percent"]) for p in rows],
    }
