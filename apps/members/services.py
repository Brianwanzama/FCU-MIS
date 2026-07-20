"""
Member business services.

This module owns all business rules concerning a Member's lifecycle.

Models store data.

Services decide status.

Other applications (Contributions, Loans, Investments, etc.)
must never update Member.status directly.
"""

from django.db import transaction

from apps.auditlog.models import AuditLog
from apps.auditlog.utils import log_action


@transaction.atomic
def evaluate_member_status(member, cycle):
    """
    Recalculate the member's status for the given cycle.

    A member is ACTIVE only when all required monthly savings
    and emergency contributions for the cycle have been cleared.

    This function is called whenever a contribution is added,
    updated or deleted.
    """

    from apps.contributions.services import member_balance
    from .models import Member

    previous_status = member.status

    balance = member_balance(member, cycle)

    if balance["eligible"]:
        new_status = Member.Status.ACTIVE
        inactive_reason = ""
    else:
        new_status = Member.Status.INACTIVE
        inactive_reason = Member.InactiveReason.CONTRIBUTION_ARREARS

    if (
        member.status != new_status
        or member.inactive_reason != inactive_reason
    ):
        old_snapshot = {
            "status": member.status,
            "inactive_reason": member.inactive_reason,
        }

        member.status = new_status
        member.inactive_reason = inactive_reason
        member.save(
            update_fields=[
                "status",
                "inactive_reason",
            ]
        )

        log_action(
            AuditLog.Action.STATUS_CHANGE,
            instance=member,
            old_value=old_snapshot,
            new_value={
                "status": member.status,
                "inactive_reason": member.inactive_reason,
            },
        )

    return member