"""
Cycle state transitions.

Every status change goes through here and nowhere else. Two reasons:

1. Audit. Manual 10.0 (Record Keeping and Transparency) is not satisfied by a
   status column that changed at some point for some reason. Routing every
   transition through one place means the AuditLog entry cannot be forgotten.

2. The rules. "Only one ACTIVE cycle" and "CLOSED means locked" are business
   rules, not view logic. A future management command, data import, or admin
   action must obey them too, so they live here rather than in a view.

Note deliberately: activating a cycle does NOT auto-close the previous one.
See SRS v1.2 C-2 / A-002. Silently locking a period of financial history as a
side effect of a click aimed at a different object is exactly the kind of thing
that goes unnoticed until it is expensive.
"""

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from apps.auditlog.models import AuditLog
from apps.auditlog.utils import log_action

from .models import Cycle


class CycleTransitionError(ValidationError):
    """Raised when a requested status change is not legal from the current state."""


def get_active_cycle():
    """
    The one currently-open cycle, or None.

    Every later module should call this rather than querying Cycle directly, so
    that if the definition of "the cycle to write into" ever changes, it changes
    once.
    """
    return Cycle.objects.filter(status=Cycle.Status.ACTIVE).first()


@transaction.atomic
def create_cycle(cycle, actor=None):
    cycle.full_clean()
    cycle.save()
    log_action(
        AuditLog.Action.CREATE,
        instance=cycle,
        actor=actor,
        new_value=_snapshot(cycle),
    )
    return cycle


@transaction.atomic
def update_cycle(cycle, old_snapshot, actor=None):
    """
    `old_snapshot` must be taken with _snapshot() BEFORE the form mutated the
    instance, so the audit entry records a genuine before/after pair rather than
    two copies of the new state.
    """
    if cycle.is_locked:
        raise CycleTransitionError(
            f"{cycle} is closed and locked. Reopen it before editing."
        )
    cycle.full_clean()
    cycle.save()

    new_snapshot = _snapshot(cycle)
    log_action(
        AuditLog.Action.UPDATE,
        instance=cycle,
        actor=actor,
        old_value=old_snapshot,
        new_value=new_snapshot,
    )

    # Manual 3.1 fixes the contribution amounts; 11.4 allows the General Meeting
    # to amend them. Changing them is therefore a policy amendment, not routine
    # data entry, and gets its own audit entry so it is findable as one.
    amount_fields = ("monthly_savings_amount", "monthly_emergency_amount")
    changed = {
        f: {"from": old_snapshot.get(f), "to": new_snapshot.get(f)}
        for f in amount_fields
        if old_snapshot.get(f) != new_snapshot.get(f)
    }
    if changed:
        log_action(
            AuditLog.Action.SETTINGS_CHANGE,
            instance=cycle,
            actor=actor,
            old_value={"note": "Contribution amounts amended (Manual 3.1 / 11.4)"},
            new_value=changed,
        )
    return cycle


@transaction.atomic
def activate_cycle(cycle, actor=None):
    """
    Make this the one cycle financial records are written into.

    Refuses if another cycle is still ACTIVE. The Administrator must close that
    one deliberately first, so they see its pre-close warnings.
    """
    if cycle.status == Cycle.Status.ACTIVE:
        raise CycleTransitionError(f"{cycle} is already active.")
    if cycle.status == Cycle.Status.CLOSED:
        raise CycleTransitionError(
            f"{cycle} is closed. Reopen it first if it really needs to be active again."
        )

    current = get_active_cycle()
    if current is not None:
        raise CycleTransitionError(
            f"{current} is still active. Close it before activating {cycle} - "
            f"closing locks its records, so it is not something we do to you by "
            f"surprise as a side effect of activating another cycle."
        )

    old = cycle.status
    cycle.status = Cycle.Status.ACTIVE
    cycle.activated_at = timezone.now()
    cycle.activated_by = actor
    cycle.save()

    log_action(
        AuditLog.Action.STATUS_CHANGE,
        instance=cycle,
        actor=actor,
        old_value={"status": old},
        new_value={"status": cycle.status, "event": "CYCLE_ACTIVATED"},
    )
    return cycle


@transaction.atomic
def close_cycle(cycle, actor=None, acknowledged_warnings=None):
    """
    Close and LOCK a cycle. After this, no financial record may be written
    against it until an Administrator reopens it with a reason.

    `acknowledged_warnings` is the list of issues the Administrator was shown and
    accepted. It is stored in the audit entry, so the record shows not just that
    the cycle was closed early but that whoever closed it was told why that was
    questionable and did it anyway.
    """
    if cycle.status == Cycle.Status.CLOSED:
        raise CycleTransitionError(f"{cycle} is already closed.")
    if cycle.status == Cycle.Status.UPCOMING:
        raise CycleTransitionError(
            f"{cycle} has never been active, so there is nothing to close. "
            f"Delete it instead if it was created in error."
        )

    old = cycle.status
    cycle.status = Cycle.Status.CLOSED
    cycle.closed_at = timezone.now()
    cycle.closed_by = actor
    cycle.save()

    log_action(
        AuditLog.Action.STATUS_CHANGE,
        instance=cycle,
        actor=actor,
        old_value={"status": old},
        new_value={
            "status": cycle.status,
            "event": "CYCLE_CLOSED",
            "warnings_acknowledged": acknowledged_warnings or [],
        },
    )
    return cycle


@transaction.atomic
def reopen_cycle(cycle, actor, reason):
    """
    Administrator-only. Unlocks a closed cycle and returns it to ACTIVE.

    A reason is mandatory and is not a formality: reopening a locked financial
    period is the single most dangerous operation in this module, and the reason
    is what an auditor reads first.
    """
    if cycle.status != Cycle.Status.CLOSED:
        raise CycleTransitionError(f"{cycle} is not closed, so it cannot be reopened.")

    reason = (reason or "").strip()
    if not reason:
        raise CycleTransitionError("A written reason is required to reopen a closed cycle.")

    other = get_active_cycle()
    if other is not None:
        raise CycleTransitionError(
            f"{other} is currently active. Close it before reopening {cycle} - "
            f"only one cycle may be open at a time."
        )

    cycle.status = Cycle.Status.ACTIVE
    cycle.closed_at = None
    cycle.closed_by = None
    cycle.reopen_count += 1
    cycle.save()

    log_action(
        AuditLog.Action.STATUS_CHANGE,
        instance=cycle,
        actor=actor,
        old_value={"status": Cycle.Status.CLOSED},
        new_value={
            "status": cycle.status,
            "event": "CYCLE_REOPENED",
            "reason": reason,
            "reopen_count": cycle.reopen_count,
        },
    )
    return cycle


@transaction.atomic
def delete_cycle(cycle, actor=None):
    deletable, reason = cycle.is_deletable()
    if not deletable:
        raise CycleTransitionError(reason)

    snapshot = _snapshot(cycle)
    repr_ = str(cycle)
    pk = cycle.pk
    cycle.delete()

    # instance is gone, so the identifying fields are passed explicitly - the
    # audit trail must outlive the row it describes.
    log_action(
        AuditLog.Action.DELETE,
        actor=actor,
        model_name="Cycle",
        object_id=str(pk),
        object_repr=repr_,
        old_value=snapshot,
        new_value={"event": "CYCLE_DELETED"},
    )
    return repr_


def _snapshot(cycle):
    """JSON-safe picture of a cycle, for audit old_value/new_value."""
    return {
        "cycle_number": cycle.cycle_number,
        "name": cycle.name,
        "financial_year": str(cycle.financial_year) if cycle.financial_year_id else None,
        "start_date": cycle.start_date.isoformat() if cycle.start_date else None,
        "end_date": cycle.end_date.isoformat() if cycle.end_date else None,
        "duration_months": cycle.duration_months,
        "duration_override_reason": cycle.duration_override_reason,
        "monthly_savings_amount": str(cycle.monthly_savings_amount),
        "monthly_emergency_amount": str(cycle.monthly_emergency_amount),
        "per_member_target": str(cycle.per_member_target),
        "status": cycle.status,
    }
