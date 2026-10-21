"""
Contribution business services.

Every financial transaction passes through this module.

Responsibilities
----------------
1. Validate business rules before money is recorded.
2. Ensure contributions belong to the active cycle.
3. Prevent modifications to locked cycles.
4. Maintain a complete audit trail.
5. Leave member status alone: it is assessed once, when the cycle closes.
6. Provide reusable summaries for dashboards, reports and loans.
"""

from decimal import Decimal
from apps.cycles.services import get_active_cycle
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q, Sum

from apps.auditlog.models import AuditLog
from apps.auditlog.utils import log_action


from .models import Contribution


class ContributionError(ValidationError):
    """Raised when a contribution operation violates FCU business rules."""


# ============================================================
# CREATE
# ============================================================

@transaction.atomic
def record_contribution(contribution, actor=None):
    """
    Record a new contribution.

    All new contributions must go through this service.
    """

    _validate_active_cycle(contribution)
    contribution.full_clean()
    _validate_arrears_amount(contribution)
    contribution.save()

    log_action(
        AuditLog.Action.CREATE,
        instance=contribution,
        actor=actor,
        new_value=_snapshot(contribution),
    )

    _reassess_arrears(contribution.member, contribution.settles_cycle, actor)

    return contribution


# ============================================================
# UPDATE
# ============================================================

@transaction.atomic
def update_contribution(contribution, old_snapshot, actor=None):
    """
    Update an existing contribution.
    """

    if contribution.cycle.is_locked:
        raise ContributionError(
            "Contributions belonging to a closed cycle cannot be edited."
        )

    contribution.full_clean()
    _validate_arrears_amount(contribution)
    contribution.save()

    log_action(
        AuditLog.Action.UPDATE,
        instance=contribution,
        actor=actor,
        old_value=old_snapshot,
        new_value=_snapshot(contribution),
    )

    _reassess_arrears(contribution.member, contribution.settles_cycle, actor)

    old_settled_id = (old_snapshot or {}).get("settles_cycle_id")

    if old_settled_id and old_settled_id != contribution.settles_cycle_id:
        from apps.cycles.models import Cycle

        _reassess_arrears(
            contribution.member, Cycle.objects.filter(pk=old_settled_id).first(), actor
        )

    return contribution


# ============================================================
# DELETE
# ============================================================

@transaction.atomic
def delete_contribution(contribution, actor=None):
    """
    Delete a contribution.
    """

    if contribution.cycle.is_locked:
        raise ContributionError(
            "Contributions belonging to a closed cycle cannot be deleted."
        )

    snapshot = _snapshot(contribution)
    repr_ = str(contribution)
    pk = contribution.pk
    member = contribution.member
    settled_cycle = contribution.settles_cycle

    contribution.delete()

    log_action(
        AuditLog.Action.DELETE,
        actor=actor,
        model_name="Contribution",
        object_id=str(pk),
        object_repr=repr_,
        old_value=snapshot,
        new_value={"event": "CONTRIBUTION_DELETED"},
    )

    _reassess_arrears(member, settled_cycle, actor)

    return repr_


# ============================================================
# MEMBER SUMMARY
# ============================================================

def member_contribution_summary(member, cycle):
    """
    Returns a complete financial summary for one member in one cycle.
    """

    monthly_paid = (
        cycle_payments(cycle)
        .filter(
            member=member,
            contribution_type=Contribution.ContributionType.MONTHLY,
        ).aggregate(total=Sum("amount"))["total"]
        or Decimal("0")
    )

    emergency_paid = (
        cycle_payments(cycle)
        .filter(
            member=member,
            contribution_type=Contribution.ContributionType.EMERGENCY,
        ).aggregate(total=Sum("amount"))["total"]
        or Decimal("0")
    )

    monthly_due = cycle.member_deposit_target
    emergency_due = cycle.member_emergency_target

    return {
        "monthly_due": monthly_due,
        "monthly_paid": monthly_paid,
        "monthly_balance": monthly_due - monthly_paid,

        "emergency_due": emergency_due,
        "emergency_paid": emergency_paid,
        "emergency_balance": emergency_due - emergency_paid,

        "overall_due": monthly_due + emergency_due,
        "overall_paid": monthly_paid + emergency_paid,
        "overall_balance":
            (monthly_due + emergency_due)
            -
            (monthly_paid + emergency_paid),
    }

# ============================================================
# MEMBER BALANCE
# ============================================================

def member_balance(member, cycle):
    """
    Returns the member's current contribution position.

    Used by:
    - Member status evaluation
    - Member dashboard
    - Reports
    """

    summary = member_contribution_summary(
        member,
        cycle,
    )

    return {
        "monthly_due": summary["monthly_due"],
        "monthly_paid": summary["monthly_paid"],
        "monthly_balance": summary["monthly_balance"],

        "emergency_due": summary["emergency_due"],
        "emergency_paid": summary["emergency_paid"],
        "emergency_balance": summary["emergency_balance"],

        "overall_due": summary["overall_due"],
        "overall_paid": summary["overall_paid"],
        "overall_balance": summary["overall_balance"],

        # The cycle target is the DEPOSIT target. Emergency contributions are a
        # separate obligation and do not decide membership status, and an
        # emergency overpayment can never cover a deposit shortfall.
        "eligible": summary["monthly_balance"] <= Decimal("0"),
    }
# ============================================================
# CYCLE SUMMARY
# ============================================================

def _progress(expected, collected):

    outstanding = expected - collected

    rate = (
        (collected / expected * Decimal("100"))
        if expected > 0
        else Decimal("0")
    )

    return outstanding, round(rate, 2)


def cycle_contribution_summary(cycle):
    """
    Returns contribution statistics for an entire cycle.

    Deposits and emergency contributions are reported separately and never
    mixed. Targets come from the Cycle's own configuration:

        deposit target   = eligible members x monthly savings x months
        emergency target = eligible members x monthly emergency x months

    Keys `expected`, `collected`, `outstanding` and `collection_rate` describe
    DEPOSITS only (emergency excluded); emergency has its own `emergency_*`
    keys.
    """

    def collected_for(contribution_type):
        return (
            Contribution.objects.filter(
                cycle=cycle,
                settles_cycle__isnull=True,
                contribution_type=contribution_type,
            ).aggregate(total=Sum("amount"))["total"]
            or Decimal("0")
        )

    members = cycle.eligible_member_count

    expected = cycle.unit_deposit_target
    collected = collected_for(Contribution.ContributionType.MONTHLY)
    outstanding, collection_rate = _progress(expected, collected)

    emergency_expected = cycle.unit_emergency_target
    emergency_collected = collected_for(Contribution.ContributionType.EMERGENCY)
    emergency_outstanding, emergency_rate = _progress(
        emergency_expected, emergency_collected
    )

    return {
        "eligible_members": members,

        "expected": expected,
        "collected": collected,
        "outstanding": outstanding,
        "collection_rate": collection_rate,

        "emergency_expected": emergency_expected,
        "emergency_collected": emergency_collected,
        "emergency_outstanding": emergency_outstanding,
        "emergency_collection_rate": emergency_rate,
    }


# ============================================================
# HELPERS
# ============================================================

def cycle_payments(cycle):
    """
    Every payment that counts towards `cycle`: those recorded in it, except
    arrears payments it made for an older cycle, plus later arrears payments
    recorded elsewhere that settle it.
    """

    return Contribution.objects.filter(
        Q(cycle=cycle, settles_cycle__isnull=True)
        | Q(settles_cycle=cycle)
    )


def _validate_arrears_amount(contribution):
    """
    An arrears payment may not exceed what the member still owes for the closed
    cycle; anything beyond that belongs in the active cycle.
    """

    settled = contribution.settles_cycle

    if settled is None:
        return

    balance = member_contribution_summary(contribution.member, settled)["monthly_balance"]

    if contribution.pk:
        previous = Contribution.objects.filter(pk=contribution.pk).values_list(
            "amount", "settles_cycle_id"
        ).first()
        if previous and previous[1] == settled.pk:
            balance += previous[0]

    if balance <= 0:
        raise ContributionError(
            f"{contribution.member.member_code} has no deposit arrears for {settled}."
        )

    if contribution.amount > balance:
        raise ContributionError(
            f"{contribution.member.member_code} owes only UGX {balance:,.0f} for "
            f"{settled}. Record any extra amount as an ordinary payment."
        )


def _reassess_arrears(member, settled_cycle, actor):
    """
    After an arrears payment is added, changed or removed, update the member's
    status automatically.
    """

    if settled_cycle is None:
        return

    from apps.members.services import reassess_after_arrears_payment

    reassess_after_arrears_payment(member, settled_cycle, actor=actor)


def _validate_active_cycle(contribution):
    """
    Contributions may only be recorded against the ACTIVE cycle.
    """

    active = get_active_cycle()

    if active is None:
        raise ContributionError(
            "There is no ACTIVE cycle."
        )

    if contribution.cycle != active:
        raise ContributionError(
            f"Contributions may only be recorded against the ACTIVE cycle. "
            f"{contribution.cycle} is {contribution.cycle.get_status_display()}."
        )


def _snapshot(contribution):
    """
    JSON-safe representation for the audit log.
    """

    return {
        "member": contribution.member.member_code,
        "cycle": str(contribution.cycle),
        "contribution_type": contribution.contribution_type,
        "settles_cycle": (
            str(contribution.settles_cycle) if contribution.settles_cycle_id else None
        ),
        "settles_cycle_id": contribution.settles_cycle_id,
        "amount": str(contribution.amount),
        "period_month": (
            contribution.period_month.isoformat()
            if contribution.period_month
            else None
        ),
        "payment_date": contribution.payment_date.isoformat(),
        "payment_method": contribution.payment_method,
        "reference": contribution.reference,
        "remarks": contribution.remarks,
    }