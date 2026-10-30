"""
Investment and profit-distribution rules (Manual v3.0, sections 7 and 8).
"""

from decimal import ROUND_DOWN, Decimal

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from apps.auditlog.models import AuditLog
from apps.auditlog.utils import log_action
from apps.contributions.services import contribution_arrears_to_date
from apps.cycles.services import get_active_cycle
from apps.members.models import Member
from apps.members.services import member_available_savings

from .models import DistributionLine, Investment, InvestmentTransaction, ProfitDistribution

T = InvestmentTransaction.Kind


class InvestmentError(ValidationError):
    """Raised when an investment or distribution operation breaks an FCU rule."""


def _require(actor, permission, action):
    if actor is None or not getattr(actor, permission, False):
        raise PermissionDenied(f"You do not have permission to {action}.")


# ============================================================
# Investments and their transactions
# ============================================================

@transaction.atomic
def create_investment(actor, **fields):
    _require(actor, "has_financial_write_access", "record investments")

    investment = Investment(created_by=actor, **fields)
    investment.full_clean(exclude=["created_by"])
    investment.save()

    log_action(
        AuditLog.Action.CREATE,
        instance=investment,
        actor=actor,
        new_value={
            "event": "INVESTMENT_CREATED",
            "kind": investment.kind,
            "provider": investment.provider,
            "registered_in_fcu_name": investment.registered_in_fcu_name,
        },
    )

    return investment


def position(investment):
    """
    Where an investment stands, from its live (not reversed) transactions.

    market_value is the latest valuation, or the money still in it (invested
    less returned) when no valuation has been recorded. total_return counts
    everything: what is there now, plus what came back and was earned, less
    what went in and what it cost.
    """

    live = [t for t in investment.transactions.all() if t.reversed_at is None]

    def total(kind):
        return sum((t.amount for t in live if t.kind == kind), Decimal("0"))

    invested, returned = total(T.INVEST), total(T.REDEEM)
    income, fees = total(T.INCOME), total(T.FEE)

    valuations = sorted(
        (t for t in live if t.kind == T.VALUATION), key=lambda t: (t.transaction_date, t.id)
    )
    latest = valuations[-1] if valuations else None

    market_value = latest.amount if latest else max(invested - returned, Decimal("0"))

    if investment.status == Investment.Status.CLOSED:
        market_value = Decimal("0")

    total_return = market_value + returned + income - invested - fees

    return {
        "investment": investment,
        "invested": invested,
        "returned": returned,
        "income": income,
        "fees": fees,
        "market_value": market_value,
        "valued_on": latest.transaction_date if latest else None,
        "total_return": total_return,
        "return_percent": (total_return / invested * 100).quantize(Decimal("0.01")) if invested else Decimal("0"),
    }


def portfolio(include_closed=True):
    investments = Investment.objects.prefetch_related("transactions")

    if not include_closed:
        investments = investments.filter(status=Investment.Status.ACTIVE)

    positions = [position(i) for i in investments]

    return {
        "positions": positions,
        "invested": sum((p["invested"] for p in positions), Decimal("0")),
        "market_value": sum((p["market_value"] for p in positions), Decimal("0")),
        "income": sum((p["income"] for p in positions), Decimal("0")),
        "total_return": sum((p["total_return"] for p in positions), Decimal("0")),
    }


@transaction.atomic
def record_transaction(investment, kind, amount, transaction_date, actor, reference="", notes=""):
    _require(actor, "has_financial_write_access", "record investment transactions")

    if kind not in InvestmentTransaction.Kind.values:
        raise InvestmentError("Choose what kind of transaction this is.")

    if investment.status == Investment.Status.CLOSED:
        raise InvestmentError("This investment is closed.")

    amount = Decimal(amount)

    if amount < 0 or (amount == 0 and kind != T.VALUATION):
        raise InvestmentError("The amount must be greater than zero.")

    if transaction_date > timezone.localdate():
        raise InvestmentError("A transaction cannot be dated in the future.")

    if transaction_date < investment.opened_on:
        raise InvestmentError("A transaction cannot be dated before the investment was opened.")

    cycle = get_active_cycle()

    if cycle is None:
        raise InvestmentError("There is no active cycle to record this in.")

    if kind == T.REDEEM:
        held = position(investment)["market_value"]
        if amount > held and held > 0:
            raise InvestmentError(f"Only UGX {held:,.0f} is held in this investment.")

    entry = InvestmentTransaction.objects.create(
        investment=investment, kind=kind, amount=amount, transaction_date=transaction_date,
        reference=(reference or "").strip(), notes=(notes or "").strip(),
        cycle=cycle, recorded_by=actor,
    )

    log_action(
        AuditLog.Action.CREATE,
        instance=entry,
        actor=actor,
        new_value={
            "event": "INVESTMENT_TRANSACTION_RECORDED",
            "investment": investment.name,
            "kind": kind,
            "amount": str(amount),
            "date": transaction_date.isoformat(),
            "reference": entry.reference,
        },
    )

    return entry


@transaction.atomic
def reverse_transaction(entry, actor, reason):
    """Administrator or Chairperson only, never the person who recorded it."""

    _require(actor, "has_chairperson_access", "reverse investment transactions")

    entry = InvestmentTransaction.objects.select_for_update().get(pk=entry.pk)

    if entry.reversed_at is not None:
        raise InvestmentError("This transaction has already been reversed.")

    if entry.recorded_by_id == actor.pk:
        raise InvestmentError("A transaction must be reversed by someone other than the person who recorded it.")

    reason = (reason or "").strip()

    if not reason:
        raise InvestmentError("Give a reason for the reversal.")

    entry.reversed_at = timezone.now()
    entry.reversed_by = actor
    entry.reversal_reason = reason
    entry.save()

    log_action(
        AuditLog.Action.DELETE,
        instance=entry,
        actor=actor,
        new_value={
            "event": "INVESTMENT_TRANSACTION_REVERSED",
            "investment": entry.investment.name,
            "kind": entry.kind,
            "amount": str(entry.amount),
            "reason": reason,
        },
    )

    return entry


def _delete_reason(reason):
    reason = (reason or "").strip()
    if not reason:
        raise InvestmentError("Give a reason for the deletion.")
    return reason


@transaction.atomic
def delete_transaction(entry, actor, reason):
    """
    Delete a wrongly entered investment transaction. A copy (who, when, why, the
    figures) stays in the audit log; the investment's position is recalculated.
    """

    _require(actor, "can_delete_records", "delete investment transactions")
    reason = _delete_reason(reason)

    entry = InvestmentTransaction.objects.select_related("investment").get(pk=entry.pk)

    log_action(
        AuditLog.Action.DELETE,
        instance=entry,
        actor=actor,
        old_value={
            "investment": entry.investment.name,
            "kind": entry.kind,
            "amount": str(entry.amount),
            "transaction_date": entry.transaction_date.isoformat(),
            "reference": entry.reference,
            "notes": entry.notes,
            "reversed": entry.reversed_at is not None,
        },
        new_value={"event": "INVESTMENT_TRANSACTION_DELETED", "reason": reason},
    )
    entry.delete()


@transaction.atomic
def delete_investment(investment, actor, reason):
    """Delete an investment together with its transactions (kept in the audit log)."""

    _require(actor, "can_delete_records", "delete investments")
    reason = _delete_reason(reason)

    entries = list(investment.transactions.all())

    log_action(
        AuditLog.Action.DELETE,
        instance=investment,
        actor=actor,
        old_value={
            "name": investment.name,
            "kind": investment.kind,
            "provider": investment.provider,
            "account_reference": investment.account_reference,
            "opened_on": investment.opened_on.isoformat(),
            "status": investment.status,
            "transactions": [
                {
                    "kind": t.kind,
                    "amount": str(t.amount),
                    "transaction_date": t.transaction_date.isoformat(),
                    "reversed": t.reversed_at is not None,
                }
                for t in entries
            ],
        },
        new_value={"event": "INVESTMENT_DELETED", "reason": reason},
    )

    investment.transactions.all().delete()
    investment.delete()


@transaction.atomic
def delete_distribution(distribution, actor, reason):
    """Delete a profit distribution and its member shares (kept in the audit log)."""

    _require(actor, "can_delete_records", "delete profit distributions")
    reason = _delete_reason(reason)

    distribution = ProfitDistribution.objects.get(pk=distribution.pk)

    log_action(
        AuditLog.Action.DELETE,
        instance=distribution,
        actor=actor,
        old_value={
            "description": distribution.description,
            "total_amount": str(distribution.total_amount),
            "status": distribution.status,
            "lines": [
                {"member": l.member.member_code, "amount": str(getattr(l, "amount", ""))}
                for l in distribution.lines.select_related("member")
            ],
        },
        new_value={"event": "DISTRIBUTION_DELETED", "reason": reason},
    )
    distribution.delete()


@transaction.atomic
def close_investment(investment, actor, note=""):
    _require(actor, "has_chairperson_access", "close investments")

    if investment.status == Investment.Status.CLOSED:
        raise InvestmentError("This investment is already closed.")

    held = position(investment)["market_value"]

    if held > 0:
        raise InvestmentError(
            f"UGX {held:,.0f} is still held in this investment. Record the redemption or sale first."
        )

    investment.status = Investment.Status.CLOSED
    investment.save(update_fields=["status"])

    log_action(
        AuditLog.Action.STATUS_CHANGE,
        instance=investment,
        actor=actor,
        new_value={"event": "INVESTMENT_CLOSED", "note": note},
    )

    return investment


# ============================================================
# Profit distribution (Manual 8.2, 8.3)
# ============================================================

def member_standing(member):
    """
    Is this member entitled to share profit? Returns (eligible, reason).

    Only Active members in Good Standing qualify (8.3): no overdue contributions,
    no overdue or defaulted loan. Withdrawn members and Inactive members do not.
    """

    if member.status == Member.Status.WITHDRAWN:
        return False, "Withdrawn member"

    if member.status != Member.Status.ACTIVE:
        return False, "Inactive member (Manual 2.1(c)(vi), 8.3)"

    cycle = get_active_cycle()

    if cycle is not None and contribution_arrears_to_date(member, cycle)["deposit_arrears"] > 0:
        return False, "Overdue contributions (not in Good Standing)"

    for loan in member.loans.exclude(status="SETTLED"):
        if loan.display_status() in ("Overdue", "Defaulted"):
            return False, "Overdue or defaulted loan (not in Good Standing)"

    return True, ""


def split_proportionally(total, bases):
    """
    Share `total` among members in proportion to `bases` ({key: base}) in whole
    shillings, so the shares add up to exactly `total`: each gets the rounded-down
    share and the leftover shillings go to those with the largest remainders.
    """

    total = Decimal(total)
    pool = sum(bases.values(), Decimal("0"))

    if pool <= 0:
        return {key: Decimal("0") for key in bases}

    exact = {key: total * base / pool for key, base in bases.items()}
    shares = {key: value.quantize(Decimal("1"), rounding=ROUND_DOWN) for key, value in exact.items()}
    leftover = int(total - sum(shares.values()))

    for key in sorted(exact, key=lambda k: (exact[k] - shares[k], k), reverse=True)[:leftover]:
        shares[key] += 1

    return shares


def _calculate_lines(distribution):
    """Work out every member's base, eligibility and share. Replaces existing lines."""

    distribution.lines.all().delete()

    rows = []

    for member in Member.objects.order_by("member_code"):
        eligible, reason = member_standing(member)
        base = max(member_available_savings(member), Decimal("0"))

        if eligible and base <= 0:
            eligible, reason = False, "No accumulated deposits"

        rows.append((member, base, eligible, reason))

    bases = {m.pk: base for m, base, eligible, _ in rows if eligible}
    shares = split_proportionally(distribution.total_amount, bases)
    pool = sum(bases.values(), Decimal("0"))

    DistributionLine.objects.bulk_create(
        DistributionLine(
            distribution=distribution,
            member=member,
            contribution_base=base,
            eligible=eligible,
            reason=reason,
            share_percent=(base / pool * 100).quantize(Decimal("0.0001")) if eligible and pool else Decimal("0"),
            amount=shares.get(member.pk, Decimal("0")),
        )
        for member, base, eligible, reason in rows
    )


@transaction.atomic
def draft_distribution(actor, total_amount, description):
    _require(actor, "has_financial_write_access", "draft a profit distribution")

    cycle = get_active_cycle()

    if cycle is None:
        raise InvestmentError("There is no active cycle.")

    total_amount = Decimal(total_amount)

    if total_amount <= 0:
        raise InvestmentError("The amount to share must be greater than zero.")

    description = (description or "").strip()

    if not description:
        raise InvestmentError("Say where the profit came from.")

    distribution = ProfitDistribution.objects.create(
        cycle=cycle, description=description, total_amount=total_amount, created_by=actor
    )
    _calculate_lines(distribution)

    log_action(
        AuditLog.Action.CREATE,
        instance=distribution,
        actor=actor,
        new_value={
            "event": "DISTRIBUTION_DRAFTED",
            "total_amount": str(total_amount),
            "description": description,
            "eligible_members": distribution.lines.filter(eligible=True).count(),
        },
    )

    return distribution


def _require_status(distribution, *statuses):
    if distribution.status not in statuses:
        raise InvestmentError(f"This distribution is {distribution.get_status_display().lower()}.")


@transaction.atomic
def approve_distribution(distribution, actor, resolution_reference):
    """
    Approve sharing the profit. Needs the General Meeting resolution reference.
    The shares are worked out again at this moment, so they reflect each member's
    standing on the day of approval, and are then fixed.
    """

    _require(actor, "has_chairperson_access", "approve a profit distribution")

    distribution = ProfitDistribution.objects.select_for_update().get(pk=distribution.pk)
    _require_status(distribution, ProfitDistribution.Status.DRAFT)

    if distribution.created_by_id == actor.pk:
        raise InvestmentError("A distribution must be approved by someone other than the person who drafted it.")

    resolution_reference = (resolution_reference or "").strip()

    if not resolution_reference:
        raise InvestmentError("Enter the General Meeting resolution that approved this distribution.")

    _calculate_lines(distribution)

    if not distribution.lines.filter(eligible=True).exists():
        raise InvestmentError("No member is eligible to share this profit.")

    distribution.status = ProfitDistribution.Status.APPROVED
    distribution.approved_by = actor
    distribution.approved_at = timezone.now()
    distribution.resolution_reference = resolution_reference
    distribution.save()

    log_action(
        AuditLog.Action.STATUS_CHANGE,
        instance=distribution,
        actor=actor,
        new_value={
            "event": "DISTRIBUTION_APPROVED",
            "resolution": resolution_reference,
            "total_amount": str(distribution.total_amount),
            "eligible_members": distribution.lines.filter(eligible=True).count(),
        },
    )

    return distribution


@transaction.atomic
def mark_distribution_paid(distribution, actor, paid_on=None):
    _require(actor, "has_financial_write_access", "record a distribution as paid")

    distribution = ProfitDistribution.objects.select_for_update().get(pk=distribution.pk)
    _require_status(distribution, ProfitDistribution.Status.APPROVED)

    paid_on = paid_on or timezone.localdate()

    if paid_on > timezone.localdate():
        raise InvestmentError("The payment date cannot be in the future.")

    distribution.status = ProfitDistribution.Status.PAID
    distribution.paid_on = paid_on
    distribution.paid_by = actor
    distribution.save()

    log_action(
        AuditLog.Action.STATUS_CHANGE,
        instance=distribution,
        actor=actor,
        new_value={"event": "DISTRIBUTION_PAID", "paid_on": paid_on.isoformat()},
    )

    return distribution


@transaction.atomic
def cancel_distribution(distribution, actor, reason):
    _require(actor, "has_financial_view_access", "cancel a profit distribution")

    distribution = ProfitDistribution.objects.select_for_update().get(pk=distribution.pk)
    _require_status(distribution, ProfitDistribution.Status.DRAFT, ProfitDistribution.Status.APPROVED)

    reason = (reason or "").strip()

    if not reason:
        raise InvestmentError("Give a reason for cancelling.")

    distribution.status = ProfitDistribution.Status.CANCELLED
    distribution.cancel_reason = reason
    distribution.save()

    log_action(
        AuditLog.Action.STATUS_CHANGE,
        instance=distribution,
        actor=actor,
        new_value={"event": "DISTRIBUTION_CANCELLED", "reason": reason},
    )

    return distribution


# ============================================================
# A member's own picture
# ============================================================

def member_investment_summary(member):
    """
    The member's share of FCU's investments (indicative: their proportion of all
    members' accumulated deposits applied to the portfolio's value) and the
    bonuses they have received or are due.
    """

    deposits = {m.pk: max(member_available_savings(m), Decimal("0"))
                for m in Member.objects.exclude(status=Member.Status.WITHDRAWN)}
    total = sum(deposits.values(), Decimal("0"))
    mine = deposits.get(member.pk, Decimal("0"))
    share = (mine / total) if total else Decimal("0")

    book = portfolio(include_closed=False)
    unit_trust_value = sum(
        (p["market_value"] for p in book["positions"] if p["investment"].kind == Investment.Kind.UNIT_TRUST),
        Decimal("0"),
    )

    lines = DistributionLine.objects.filter(member=member, amount__gt=0).select_related("distribution")

    def total_for(status):
        return sum((l.amount for l in lines if l.distribution.status == status), Decimal("0"))

    return {
        "share_percent": (share * 100).quantize(Decimal("0.01")),
        "portfolio_value": book["market_value"],
        "portfolio_share": (book["market_value"] * share).quantize(Decimal("1")),
        "unit_trust": (unit_trust_value * share).quantize(Decimal("1")),
        "distributions_paid": total_for(ProfitDistribution.Status.PAID),
        "distributions_approved": total_for(ProfitDistribution.Status.APPROVED),
        "lines": [l for l in lines if l.distribution.status in (
            ProfitDistribution.Status.APPROVED, ProfitDistribution.Status.PAID)],
    }
