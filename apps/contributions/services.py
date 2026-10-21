"""
Contribution business services.

Every financial transaction passes through this module. Financial records are
never deleted: mistakes are corrected by audited edits or approved reversals.

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
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Q, Sum
from django.utils import timezone

from apps.auditlog.models import AuditLog
from apps.auditlog.utils import log_action


from .models import Contribution, ContributionReversal


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
def update_contribution(contribution, old_snapshot, actor=None, reason=""):
    """
    Correct an existing contribution.

    Needs a written reason. The audit log keeps the old value, the new value,
    who changed it, when, and why. Reversed payments, payments with a reversal
    pending, and payments in a closed cycle cannot be edited.
    """

    _require(actor, "can_edit_contributions", "edit contributions")
    _require_editable(contribution)

    reason = (reason or "").strip()

    if not reason:
        raise ContributionError("A reason is required to change a contribution.")

    contribution.full_clean()
    _validate_arrears_amount(contribution)
    contribution.save()

    log_action(
        AuditLog.Action.UPDATE,
        instance=contribution,
        actor=actor,
        old_value=old_snapshot,
        new_value={**_snapshot(contribution), "reason": reason},
    )

    _reassess_arrears(contribution.member, contribution.settles_cycle, actor)

    return contribution


# ============================================================
# REVERSAL (replaces deletion)
# ============================================================

@transaction.atomic
def request_reversal(contribution, actor, reason):
    """
    Ask for a contribution to be reversed. Nothing changes until it is approved.
    """

    _require(actor, "can_request_reversal", "request a reversal")
    _require_editable(contribution, action="reversed")

    reason = (reason or "").strip()

    if not reason:
        raise ContributionError("A reason is required to request a reversal.")

    reversal = ContributionReversal.objects.create(
        contribution=contribution,
        reason=reason,
        requested_by=actor,
    )

    log_action(
        AuditLog.Action.UPDATE,
        instance=contribution,
        actor=actor,
        new_value={
            "event": "REVERSAL_REQUESTED",
            "reversal_id": reversal.pk,
            "reason": reason,
        },
    )

    return reversal


@transaction.atomic
def approve_reversal(reversal, actor, note=""):
    """
    Approve a reversal. The payment stops counting towards any balance but is
    kept, with its full history, in the register and the audit log.
    """

    _require(actor, "can_approve_reversal", "approve reversals")
    _require_pending(reversal)

    if reversal.requested_by_id == actor.pk:
        raise ContributionError(
            "A reversal must be approved by someone other than the person who "
            "requested it."
        )

    contribution = reversal.contribution
    now = timezone.now()

    snapshot = _snapshot(contribution)

    Contribution.objects.filter(pk=contribution.pk).update(reversed_at=now)
    contribution.reversed_at = now

    reversal.status = ContributionReversal.Status.APPROVED
    reversal.decided_by = actor
    reversal.decided_at = now
    reversal.decision_note = (note or "").strip()
    reversal.save()

    log_action(
        AuditLog.Action.DELETE,
        instance=contribution,
        actor=actor,
        old_value=snapshot,
        new_value={
            "event": "CONTRIBUTION_REVERSED",
            "reversal_id": reversal.pk,
            "requested_by": reversal.requested_by.get_username(),
            "reason": reversal.reason,
            "note": reversal.decision_note,
        },
    )

    _reassess_arrears(contribution.member, contribution.settles_cycle, actor)

    return reversal


@transaction.atomic
def reject_reversal(reversal, actor, note):
    """Reject a reversal request. The payment stays exactly as it was."""

    _require(actor, "can_approve_reversal", "reject reversals")
    _require_pending(reversal)

    note = (note or "").strip()

    if not note:
        raise ContributionError("A note is required to reject a reversal request.")

    reversal.status = ContributionReversal.Status.REJECTED
    reversal.decided_by = actor
    reversal.decided_at = timezone.now()
    reversal.decision_note = note
    reversal.save()

    log_action(
        AuditLog.Action.UPDATE,
        instance=reversal.contribution,
        actor=actor,
        new_value={
            "event": "REVERSAL_REJECTED",
            "reversal_id": reversal.pk,
            "note": note,
        },
    )

    return reversal


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
                reversed_at__isnull=True,
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

def _require(actor, permission, action):
    if actor is None or not getattr(actor, permission, False):
        raise PermissionDenied(f"You do not have permission to {action}.")


def _require_editable(contribution, action="edited"):
    if contribution.is_reversed:
        raise ContributionError("This contribution has been reversed.")

    if contribution.pending_reversal:
        raise ContributionError(
            f"This contribution has a reversal pending, so it cannot be {action} "
            f"again until that request is decided."
        )

    if contribution.cycle.is_locked:
        raise ContributionError(
            "Contributions belonging to a closed cycle cannot be changed. "
            "An Administrator must reopen the cycle first."
        )


def _require_pending(reversal):
    if reversal.status != ContributionReversal.Status.PENDING:
        raise ContributionError("This reversal request has already been decided.")


def cycle_payments(cycle):
    """
    Every payment that counts towards `cycle`: those recorded in it, except
    arrears payments it made for an older cycle, plus later arrears payments
    recorded elsewhere that settle it.
    """

    return Contribution.objects.filter(
        Q(cycle=cycle, settles_cycle__isnull=True)
        | Q(settles_cycle=cycle),
        reversed_at__isnull=True,
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
        previous = Contribution.objects.filter(
            pk=contribution.pk, reversed_at__isnull=True
        ).values_list("amount", "settles_cycle_id").first()
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
        "amount": str(Decimal(contribution.amount).quantize(Decimal("0.01"))),
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