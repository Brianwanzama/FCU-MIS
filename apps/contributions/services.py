"""
Contribution business services.

Every financial transaction passes through this module.

Responsibilities
----------------
1. Validate business rules before money is recorded.
2. Ensure contributions belong to the active cycle.
3. Prevent modifications to locked cycles.
4. Maintain a complete audit trail.
5. Trigger member-status recalculation after every payment.
6. Provide reusable summaries for dashboards, reports and loans.
"""

from decimal import Decimal
from apps.cycles.services import get_active_cycle
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Sum

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
    contribution.save()

    log_action(
        AuditLog.Action.CREATE,
        instance=contribution,
        actor=actor,
        new_value=_snapshot(contribution),
    )

    _update_member_status(contribution)

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
    contribution.save()

    log_action(
        AuditLog.Action.UPDATE,
        instance=contribution,
        actor=actor,
        old_value=old_snapshot,
        new_value=_snapshot(contribution),
    )

    _update_member_status(contribution)

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
    cycle = contribution.cycle

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

    _update_member_status_by_objects(member, cycle)

    return repr_


# ============================================================
# MEMBER SUMMARY
# ============================================================

def member_contribution_summary(member, cycle):
    """
    Returns a complete financial summary for one member in one cycle.
    """

    monthly_paid = (
        Contribution.objects.filter(
            member=member,
            cycle=cycle,
            contribution_type=Contribution.ContributionType.MONTHLY,
        ).aggregate(total=Sum("amount"))["total"]
        or Decimal("0")
    )

    emergency_paid = (
        Contribution.objects.filter(
            member=member,
            cycle=cycle,
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

        # Each obligation must be cleared on its own: an emergency
        # overpayment never covers a deposit shortfall, or vice versa.
        "eligible": (
            summary["monthly_balance"] <= Decimal("0")
            and summary["emergency_balance"] <= Decimal("0")
        ),
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


def _update_member_status(contribution):
    """
    Trigger member-status recalculation.
    """

    from apps.members.services import evaluate_member_status

    evaluate_member_status(
        contribution.member,
        contribution.cycle,
    )


def _update_member_status_by_objects(member, cycle):
    """
    Trigger recalculation after delete().
    """

    from apps.members.services import evaluate_member_status

    evaluate_member_status(
        member,
        cycle,
    )


def _snapshot(contribution):
    """
    JSON-safe representation for the audit log.
    """

    return {
        "member": contribution.member.member_code,
        "cycle": str(contribution.cycle),
        "contribution_type": contribution.contribution_type,
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