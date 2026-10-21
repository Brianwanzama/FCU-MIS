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
    Members who have not met the cycle's deposit target (emergency contributions
    are not part of it). Withdrawn members are not assessed.

    A member who joined mid-cycle owes the full cycle amount.
    """

    from apps.contributions.services import member_balance
    from .models import Member

    failing = []

    # Only members who were eligible in this cycle owed its target.
    members = Member.objects.filter(
        cycle_memberships__cycle=cycle,
        cycle_memberships__left_on__isnull=True,
    ).exclude(status=Member.Status.WITHDRAWN)

    for member in members:
        if not member_balance(member, cycle)["eligible"]:
            failing.append(member)

    return failing


def _set_status_from_cycle(member, cycle, actor, event):
    """
    Set a member ACTIVE or INACTIVE according to whether they met `cycle`'s
    deposit target. Never touches a WITHDRAWN member. Returns the new status
    if it changed, otherwise None.
    """

    from apps.contributions.services import member_balance
    from .models import Member

    # The caller's instance may be stale (status changes when cycles close).
    member.refresh_from_db(fields=["status", "inactive_reason"])

    if member.status == Member.Status.WITHDRAWN:
        return None

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
        return None

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
            "event": event,
            "cycle": str(cycle),
        },
    )

    return new_status


@transaction.atomic
def apply_cycle_completion_statuses(cycle, actor=None):
    """
    Set every member's status for the NEXT cycle from how this cycle ended.

    FCU rule: a member who fails to meet the cycle's target (deposits only,
    emergency contributions are not part of it) becomes INACTIVE, and is ACTIVE
    in the next cycle only if the previous cycle was completed. So status is
    decided when a cycle closes. It is deliberately NOT recalculated on each
    ordinary payment: a half-paid member in the middle of a cycle is still on
    course, and flipping them to Inactive would also move the unit-wide targets
    (which count Active members) under everyone's feet.

    A WITHDRAWN member is never touched, so a payment can never reactivate them.

    Returns {"activated": [...], "deactivated": [...]} of member codes.
    """

    from .models import Member

    changed = {"activated": [], "deactivated": []}

    for member in Member.objects.exclude(status=Member.Status.WITHDRAWN):
        new_status = _set_status_from_cycle(
            member, cycle, actor, "CYCLE_COMPLETION_ASSESSED"
        )

        if new_status == Member.Status.ACTIVE:
            changed["activated"].append(member.member_code)
        elif new_status == Member.Status.INACTIVE:
            changed["deactivated"].append(member.member_code)

    return changed


@transaction.atomic
def reassess_after_arrears_payment(member, settled_cycle, actor=None):
    """
    Update a member's status after a payment towards (or a reversal of a
    payment towards) a CLOSED cycle's arrears.

    Only the most recent closed cycle decides status ("active only if you
    completed the previous cycle"), so settling an older cycle changes nothing.
    Clearing the arrears makes the member ACTIVE automatically; reversing that
    payment puts them back to INACTIVE.
    """

    from apps.cycles.models import Cycle

    latest_closed = (
        Cycle.objects.filter(status=Cycle.Status.CLOSED)
        .order_by("-end_date")
        .first()
    )

    if settled_cycle is None or latest_closed is None or settled_cycle != latest_closed:
        return None

    return _set_status_from_cycle(
        member, settled_cycle, actor, "ARREARS_PAYMENT_ASSESSED"
    )


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

    from apps.loans.services import member_loan_summary

    loan = member_loan_summary(member)

    from apps.unittrust.services import member_investment_summary as investment_summary

    investment = investment_summary(member)

    # Deposits and emergency contributions are separate funds. Only deposits
    # count as the member's savings; the emergency fund is reported on its own.
    total_deposits = contribution["monthly_paid"]
    total_emergency = contribution["emergency_paid"]

    # Deposits plus bonuses received, less what is owed. A share of FCU's
    # investments is NOT added: it is funded by these same deposits.
    net_position = (
        total_deposits
        +
        investment["distributions_paid"]
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
    """The member's share of FCU's investments and their bonuses."""

    from apps.unittrust.services import member_investment_summary as summary

    return summary(member)

# ============================================================
# MEMBER STATEMENT
# ============================================================

def member_available_savings(member):
    """
    Deposits the member has paid across ALL cycles (reversed payments excluded),
    less any already recovered to repay a loan.

    Emergency contributions are not included: they are ring-fenced and never
    available for loan recovery.
    """

    from django.db.models import Sum

    from apps.contributions.models import Contribution
    from apps.loans.models import LoanRepayment

    deposits = (
        Contribution.objects.filter(
            member=member,
            contribution_type=Contribution.ContributionType.MONTHLY,
            reversed_at__isnull=True,
        ).aggregate(total=Sum("amount"))["total"]
        or Decimal("0")
    )

    # Savings already used to repay a loan are no longer available.
    offsets = (
        LoanRepayment.objects.filter(
            loan__member=member,
            kind=LoanRepayment.Kind.SAVINGS_OFFSET,
            reversed_at__isnull=True,
        ).aggregate(total=Sum("amount"))["total"]
        or Decimal("0")
    )

    return deposits - offsets


def statement_cycles(member=None):
    """Cycles a statement can be produced for: every cycle that has begun."""

    from apps.cycles.models import Cycle

    return Cycle.objects.exclude(status=Cycle.Status.UPCOMING).order_by("-start_date")


def member_statement(member, cycle):
    """
    Everything a statement (page or PDF) needs, for one member and one cycle.

    The ledger lists every payment that belongs to the cycle in date order with
    running deposit and emergency totals. Reversed payments stay visible but are
    marked and never counted. A payment recorded in this cycle that settles an
    OLDER cycle's arrears is listed too, but does not count here; a later payment
    that settles THIS cycle's arrears does.
    """

    from django.db.models import Q
    from django.utils import timezone

    from apps.contributions.models import Contribution

    summary = member_financial_summary(member, cycle)
    contributions = summary["contributions"]

    payments = (
        Contribution.objects.filter(Q(cycle=cycle) | Q(settles_cycle=cycle), member=member)
        .select_related("cycle", "settles_cycle")
        .order_by("payment_date", "id")
    )

    rows = []
    running_deposits = Decimal("0")
    running_emergency = Decimal("0")

    for p in payments:
        counts_here = (
            p.reversed_at is None
            and (p.settles_cycle_id is None or p.settles_cycle_id == cycle.pk)
        )
        is_deposit = p.contribution_type == Contribution.ContributionType.MONTHLY

        notes = []
        if p.reversed_at is not None:
            notes.append("Reversed")
        if p.settles_cycle_id and p.settles_cycle_id != cycle.pk:
            notes.append(f"Counted towards Cycle {p.settles_cycle.cycle_number} arrears")
        elif p.settles_cycle_id == cycle.pk and p.cycle_id != cycle.pk:
            notes.append(f"Arrears payment made in Cycle {p.cycle.cycle_number}")

        if counts_here:
            if is_deposit:
                running_deposits += p.amount
            else:
                running_emergency += p.amount

        rows.append(
            {
                "contribution": p,
                "date": p.payment_date,
                "description": f"{p.get_contribution_type_display()} for {p.period_month:%b %Y}",
                "method": p.get_payment_method_display(),
                "reference": p.reference,
                "amount": p.amount,
                "is_deposit": is_deposit,
                "counts_here": counts_here,
                "reversed": p.reversed_at is not None,
                "note": "; ".join(notes),
                "running_deposits": running_deposits,
                "running_emergency": running_emergency,
            }
        )

    from apps.loans.models import LoanRepayment

    offsets = list(
        LoanRepayment.objects.filter(
            loan__member=member,
            cycle=cycle,
            kind=LoanRepayment.Kind.SAVINGS_OFFSET,
            reversed_at__isnull=True,
        ).select_related("loan__application").order_by("payment_date", "id")
    )

    return {
        "member": member,
        "cycle": cycle,
        "generated_at": timezone.localtime(),
        "deposits_offsets": contributions["monthly_offsets"],
        "offsets": offsets,
        "deposit_target": contributions["monthly_due"],
        "deposits_paid": contributions["monthly_paid"],
        "deposits_outstanding": max(contributions["monthly_balance"], Decimal("0")),
        "emergency_target": contributions["emergency_due"],
        "emergency_paid": contributions["emergency_paid"],
        "emergency_outstanding": max(contributions["emergency_balance"], Decimal("0")),
        "loan_outstanding": summary["loan"]["balance"],
        "available_savings": member_available_savings(member),
        "target_met": contributions["monthly_balance"] <= 0,
        "rows": rows,
    }


# ============================================================
# STATUS AFTER LOAN RECOVERY (Manual 4.6(d))
# ============================================================

@transaction.atomic
def reassess_after_loan_recovery(member, actor=None):
    """
    Manual 4.6(d): when recovery takes a member's contributions below what the
    current cycle requires, the member becomes Inactive until the contribution
    position is restored. This runs after a recovery and after every later
    contribution, so the member returns to Active automatically once they have
    paid back up. A Withdrawn member is never touched, and an Inactive member
    whose reason is something else (arrears from a closed cycle) is left alone.
    """

    from apps.contributions.services import contribution_arrears_to_date
    from apps.cycles.services import get_active_cycle

    from .models import Member

    member.refresh_from_db(fields=["status", "inactive_reason"])
    cycle = get_active_cycle()

    if cycle is None or member.status == Member.Status.WITHDRAWN:
        return None

    short = contribution_arrears_to_date(member, cycle)["deposit_arrears"] > 0

    if member.status == Member.Status.ACTIVE and short and _has_live_recovery(member, cycle):
        new_status, reason = Member.Status.INACTIVE, Member.InactiveReason.LOAN_RECOVERY
    elif (
        member.status == Member.Status.INACTIVE
        and member.inactive_reason == Member.InactiveReason.LOAN_RECOVERY
        and not short
    ):
        new_status, reason = Member.Status.ACTIVE, ""
    else:
        return None

    old = {"status": member.status, "inactive_reason": member.inactive_reason}
    member.status = new_status
    member.inactive_reason = reason
    member.save(update_fields=["status", "inactive_reason"])

    log_action(
        AuditLog.Action.STATUS_CHANGE,
        instance=member,
        actor=actor,
        old_value=old,
        new_value={
            "status": new_status,
            "inactive_reason": reason,
            "event": "LOAN_RECOVERY_STATUS_ASSESSED",
            "cycle": str(cycle),
        },
    )

    return new_status


def _has_live_recovery(member, cycle):
    from apps.loans.models import LoanRepayment

    return LoanRepayment.objects.filter(
        loan__member=member,
        cycle=cycle,
        kind=LoanRepayment.Kind.SAVINGS_OFFSET,
        reversed_at__isnull=True,
    ).exists()
