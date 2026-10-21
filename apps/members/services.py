"""
Member business services.

This module owns all business rules concerning a Member's lifecycle.

Models store data.

Services decide status.

Other applications (Contributions, Loans, Investments, etc.)
must never update Member.status directly.
"""

from django.db import transaction
from decimal import Decimal
from apps.auditlog.models import AuditLog
from apps.auditlog.utils import log_action


def members_failing_cycle_target(cycle):
    """
    Members who have not met the cycle's target: deposits AND emergency
    obligation both cleared. Withdrawn members are not assessed.

    A member who joined mid-cycle owes the full cycle amount.
    """

    from apps.contributions.services import member_balance
    from .models import Member

    failing = []

    for member in Member.objects.exclude(status=Member.Status.WITHDRAWN):
        if not member_balance(member, cycle)["eligible"]:
            failing.append(member)

    return failing


@transaction.atomic
def apply_cycle_completion_statuses(cycle, actor=None):
    """
    Set every member's status for the NEXT cycle from how this cycle ended.

    FCU rule: a member who fails to meet the cycle's target becomes INACTIVE,
    and is ACTIVE in the next cycle only if the previous cycle was completed.
    So status is decided once, when a cycle closes. It is deliberately NOT
    recalculated on each payment: a half-paid member in the middle of a cycle
    is still on course, and flipping them to Inactive would also move the
    unit-wide targets (which count Active members) under everyone's feet.

    A WITHDRAWN member is never touched, so a payment can never reactivate them.

    Returns {"activated": [...], "deactivated": [...]} of member codes.
    """

    from apps.contributions.services import member_balance
    from .models import Member

    changed = {"activated": [], "deactivated": []}

    for member in Member.objects.exclude(status=Member.Status.WITHDRAWN):
        if member_balance(member, cycle)["eligible"]:
            new_status = Member.Status.ACTIVE
            inactive_reason = ""
        else:
            new_status = Member.Status.INACTIVE
            inactive_reason = Member.InactiveReason.CONTRIBUTION_ARREARS

        if (
            member.status == new_status
            and member.inactive_reason == inactive_reason
        ):
            continue

        old_snapshot = {
            "status": member.status,
            "inactive_reason": member.inactive_reason,
        }

        member.status = new_status
        member.inactive_reason = inactive_reason
        member.save(update_fields=["status", "inactive_reason"])

        log_action(
            AuditLog.Action.STATUS_CHANGE,
            instance=member,
            actor=actor,
            old_value=old_snapshot,
            new_value={
                "status": member.status,
                "inactive_reason": member.inactive_reason,
                "event": "CYCLE_COMPLETION_ASSESSED",
                "cycle": str(cycle),
            },
        )

        key = "activated" if new_status == Member.Status.ACTIVE else "deactivated"
        changed[key].append(member.member_code)

    return changed


# ============================================================
# MEMBER FINANCIAL SUMMARY
# ============================================================

def member_financial_summary(member, cycle):
    """
    Returns the complete financial position of a member.

    This service feeds:

    - Member Dashboard
    - Member Statement
    - Reports
    - APIs
    """

    from apps.contributions.services import member_contribution_summary

    contribution = member_contribution_summary(
        member,
        cycle,
    )

    # Placeholder until the Loans module is built.
    loan = {
        "borrowed": Decimal("0"),
        "repaid": Decimal("0"),
        "balance": Decimal("0"),
    }

    # Placeholder until the Investments module is built.
    investment = {
        "unit_trust": Decimal("0"),
    }

    # Deposits and emergency contributions are separate funds. Only deposits
    # count as the member's savings; the emergency fund is reported on its own.
    total_deposits = contribution["monthly_paid"]
    total_emergency = contribution["emergency_paid"]

    net_position = (
        total_deposits
        +
        investment["unit_trust"]
        -
        loan["balance"]
    )

    return {
        "member": member,
        "cycle": cycle,
        "contributions": contribution,
        "loan": loan,
        "investment": investment,
        "total_deposits": total_deposits,
        "total_emergency": total_emergency,
        "net_position": net_position,
    }


# ============================================================
# MEMBER TRANSACTION HISTORY
# ============================================================

def member_transaction_history(member):
    """
    Returns every financial transaction belonging
    to the member.

    Initially this returns contributions only.

    Later it will include:

    - Loans
    - Loan Repayments
    - Savings Offset
    - Unit Trust
    - Dividends
    """

    from apps.contributions.models import Contribution

    return (
        Contribution.objects
        .filter(member=member)
        .select_related(
            "cycle",
            "recorded_by",
        )
        .order_by(
            "-payment_date",
            "-created_at",
        )
    )
def member_loan_summary(member):
    """
    Returns the member's loan position.

    Placeholder until the Loans module is built.
    """
    return {
        "borrowed": Decimal("0"),
        "repaid": Decimal("0"),
        "balance": Decimal("0"),
    }


def member_investment_summary(member):
    """
    Returns the member's investment position.

    Placeholder until the Investments module is built.
    """
    return {
        "unit_trust": Decimal("0"),
    }