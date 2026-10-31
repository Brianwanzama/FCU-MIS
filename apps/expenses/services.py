from decimal import Decimal

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Count, Sum
from django.utils import timezone

from apps.auditlog.models import AuditLog
from apps.auditlog.utils import log_action
from apps.cycles.models import Cycle

from .models import Expense

ZERO = Decimal("0")


class ExpenseError(ValidationError):
    """Raised when an expense operation breaks an FCU rule."""


def _require(actor, permission, action):
    if actor is None or not getattr(actor, permission, False):
        raise PermissionDenied(f"You do not have permission to {action}.")


def _snapshot(expense):
    return {
        "cycle": str(expense.cycle),
        "category": expense.category,
        "description": expense.description,
        "amount": str(expense.amount),
        "expense_date": expense.expense_date.isoformat(),
        "paid_to": expense.paid_to,
        "payment_method": expense.payment_method,
        "reference": expense.reference,
    }


def _check(expense):
    if expense.cycle.is_locked:
        raise ExpenseError("This cycle is closed. An Administrator must reopen it before expenses can change.")
    if expense.amount is None or expense.amount <= 0:
        raise ExpenseError("The amount must be greater than zero.")
    if expense.expense_date > timezone.localdate():
        raise ExpenseError("An expense cannot be dated in the future.")


@transaction.atomic
def record_expense(expense, actor):
    _require(actor, "has_financial_write_access", "record expenses")
    expense.recorded_by = actor
    _check(expense)
    expense.save()
    log_action(AuditLog.Action.CREATE, instance=expense, actor=actor, new_value=_snapshot(expense))
    return expense


@transaction.atomic
def update_expense(expense, old_snapshot, actor, reason):
    _require(actor, "has_financial_write_access", "change expenses")
    reason = (reason or "").strip()
    if not reason:
        raise ExpenseError("A reason is required to change an expense.")
    _check(expense)
    expense.save()
    log_action(
        AuditLog.Action.UPDATE, instance=expense, actor=actor,
        old_value=old_snapshot, new_value={**_snapshot(expense), "reason": reason},
    )
    return expense


@transaction.atomic
def delete_expense(expense, actor, reason):
    _require(actor, "can_delete_records", "delete expenses")
    reason = (reason or "").strip()
    if not reason:
        raise ExpenseError("A reason is required to delete an expense.")
    if expense.cycle.is_locked:
        raise ExpenseError("This cycle is closed. An Administrator must reopen it before expenses can be deleted.")
    log_action(
        AuditLog.Action.DELETE, instance=expense, actor=actor,
        old_value=_snapshot(expense), new_value={"event": "EXPENSE_DELETED", "reason": reason},
    )
    expense.delete()


def cycle_expenses(cycle):
    return Expense.objects.filter(cycle=cycle)


def expense_summary(cycle):
    """Total spent in the cycle, and the total by category (largest first)."""

    queryset = cycle_expenses(cycle)
    labels = dict(Expense.Category.choices)

    by_category = [
        {"category": row["category"], "label": labels.get(row["category"], row["category"]),
         "total": row["total"], "count": row["count"]}
        for row in queryset.values("category").annotate(total=Sum("amount"), count=Count("id")).order_by("-total")
    ]
    total = sum((r["total"] for r in by_category), ZERO)

    return {
        "total": total,
        "count": sum(r["count"] for r in by_category),
        "by_category": by_category,
        "rows": list(queryset.select_related("recorded_by")),
    }


def total_expenses():
    return Expense.objects.aggregate(total=Sum("amount"))["total"] or ZERO
