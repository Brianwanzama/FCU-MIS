"""
Financial reports (Manual v3.0 10.2: the Treasurer presents bi-annual reports).

A FCU cycle is six months, so a cycle report IS the bi-annual report. Everything is
worked out from the live records, the same ones the rest of the system uses, so a
report can never disagree with a statement or a dashboard.
"""

from decimal import Decimal

from django.db.models import Q, Sum
from django.utils import timezone

from apps.contributions.models import Contribution
from apps.contributions.services import cycle_contribution_summary, member_contribution_summary
from apps.loans.models import Loan, LoanInstallment, LoanRepayment
from apps.expenses.services import expense_summary, total_expenses
from apps.members.models import Member
from apps.unittrust.models import (
    InterestWithdrawal,
    InvestmentTransaction,
    MemberInterest,
    OtherSavingsDeposit,
    ProfitDistribution,
)
from apps.unittrust.services import portfolio

ZERO = Decimal("0")


def _sum(queryset, field="amount"):
    return queryset.aggregate(total=Sum(field))["total"] or ZERO


def member_rows(cycle):
    """One row per member who took part in the cycle."""

    ids = set(cycle.memberships.values_list("member_id", flat=True))
    ids |= set(Contribution.objects.filter(cycle=cycle).values_list("member_id", flat=True))
    members = Member.objects.filter(pk__in=ids).order_by("member_code")

    memberships = {m.member_id: m for m in cycle.memberships.all()}
    rows = []

    for member in members:
        s = member_contribution_summary(member, cycle)
        membership = memberships.get(member.pk)

        rows.append(
            {
                "member": member,
                "status": member.get_status_display(),
                "left_on": membership.left_on if membership else None,
                "deposit_target": s["monthly_due"],
                "deposits_paid": s["monthly_paid"],
                "recovered": s["monthly_offsets"],
                "deposits_outstanding": max(s["monthly_balance"], ZERO),
                "emergency_target": s["emergency_due"],
                "emergency_paid": s["emergency_paid"],
                "emergency_outstanding": max(s["emergency_balance"], ZERO),
                "target_met": s["monthly_balance"] <= 0,
            }
        )

    return rows


def loan_section(cycle):
    loans = list(
        Loan.objects.filter(cycle=cycle)
        .select_related("member", "application")
        .prefetch_related("installments", "repayments")
    )

    rows = []

    for loan in loans:
        installments = list(loan.installments.all())
        # Within each instalment, what is paid counts as interest first.
        interest_paid = sum((min(i.amount_paid, i.interest_due) for i in installments), ZERO)
        rows.append(
            {
                "loan": loan,
                "principal": loan.principal,
                "total_due": loan.total_due,
                "paid": loan.total_paid,
                "outstanding": loan.outstanding,
                "interest_paid": interest_paid,
                "status": loan.display_status(),
            }
        )

    live = LoanRepayment.objects.filter(cycle=cycle, reversed_at__isnull=True)

    return {
        "rows": rows,
        "count": len(rows),
        "disbursed": sum((r["principal"] for r in rows), ZERO),
        "interest_earned": sum((r["interest_paid"] for r in rows), ZERO),
        "outstanding": sum((r["outstanding"] for r in rows), ZERO),
        "repayments_received": _sum(live.filter(kind=LoanRepayment.Kind.PAYMENT)),
        "recovered_from_savings": _sum(live.filter(kind=LoanRepayment.Kind.SAVINGS_OFFSET)),
    }


def investment_section(cycle):
    book = portfolio()
    live = InvestmentTransaction.objects.filter(cycle=cycle, reversed_at__isnull=True)
    K = InvestmentTransaction.Kind

    distributions = ProfitDistribution.objects.filter(
        cycle=cycle, status__in=[ProfitDistribution.Status.APPROVED, ProfitDistribution.Status.PAID]
    )

    return {
        "positions": book["positions"],
        "market_value": book["market_value"],
        "invested_total": book["invested"],
        "invested_in_cycle": _sum(live.filter(kind=K.INVEST)),
        "income_in_cycle": _sum(live.filter(kind=K.INCOME)),
        "fees_in_cycle": _sum(live.filter(kind=K.FEE)),
        "distributions": list(distributions),
        "distributed": _sum(distributions, "total_amount"),
    }


def financial_position():
    """
    What FCU holds and owes to members, as of now.

    The cash figure is DERIVED from every recorded movement of money, including
    expenses. It does not include membership fees, which the system does not record,
    so compare it with the bank and cash balances before relying on it.
    """

    K = InvestmentTransaction.Kind
    live_contributions = Contribution.objects.filter(reversed_at__isnull=True)
    deposits = _sum(live_contributions.filter(contribution_type="MONTHLY"))
    emergency = _sum(live_contributions.filter(contribution_type="EMERGENCY"))

    offsets = _sum(
        LoanRepayment.objects.filter(kind=LoanRepayment.Kind.SAVINGS_OFFSET, reversed_at__isnull=True)
    )
    live_inv = InvestmentTransaction.objects.filter(reversed_at__isnull=True)

    loans_out = _sum(Loan.objects.all(), "principal")
    cash_repaid = _sum(
        LoanRepayment.objects.filter(kind=LoanRepayment.Kind.PAYMENT, reversed_at__isnull=True)
    )
    paid_out_profit = _sum(
        ProfitDistribution.objects.filter(status=ProfitDistribution.Status.PAID), "total_amount"
    )

    interest_paid = _sum(InterestWithdrawal.objects.filter(status=InterestWithdrawal.Status.PAID))
    interest_earned = _sum(MemberInterest.objects.all())
    other_savings = _sum(OtherSavingsDeposit.objects.all())

    cash = (
        deposits + emergency + cash_repaid
        + _sum(live_inv.filter(kind=K.REDEEM)) + _sum(live_inv.filter(kind=K.INCOME))
        - loans_out
        - _sum(live_inv.filter(kind=K.INVEST)) - _sum(live_inv.filter(kind=K.FEE))
        - paid_out_profit
        - total_expenses()
        - interest_paid
        + other_savings
    )

    owed = sum(
        (loan.outstanding for loan in Loan.objects.exclude(status=Loan.Status.SETTLED)), ZERO
    )

    return {
        "members_deposits": deposits - offsets,
        "emergency_fund": emergency,
        "loans_receivable": owed,
        "investments": portfolio(include_closed=False)["market_value"],
        "other_savings_owed": other_savings + interest_earned - interest_paid,
        "derived_cash": cash,
    }


def cycle_report(cycle):
    summary = cycle_contribution_summary(cycle)
    rows = member_rows(cycle)

    return {
        "cycle": cycle,
        "generated_at": timezone.localtime(),
        "contributions": {
            **summary,
            "members_met_target": sum(1 for r in rows if r["target_met"]),
            "members": len(rows),
        },
        "members": rows,
        "loans": loan_section(cycle),
        "investments": investment_section(cycle),
        "expenses": expense_summary(cycle),
        "position": financial_position(),
    }
