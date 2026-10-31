"""
Investment and profit-distribution rules (Manual v3.0, sections 7 and 8).
"""

from datetime import timedelta
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

from .models import (
    DistributionLine,
    Investment,
    InvestmentTransaction,
    ProfitDistribution,
    ProfitShare,
    ProfitStatement,
    InterestCredit,
    InterestWithdrawal,
    MemberInterest,
    OtherSavingsDeposit,
)

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

def contribution_shares(as_of=None):
    """
    Each member's share of the pool: their total deposits across ALL cycles (reversed
    payments left out, emergency contributions never counted) as a percentage of the
    total deposits of all members in all cycles.

    Returns {"total": Decimal, "rows": [{"member", "total", "percent"}]}, one row per member
    who has paid deposits, in member order. With `as_of`, only deposits paid on or before
    that date count (used for monthly interest, so later deposits do not earn past interest).
    """

    from django.db.models import Sum

    from apps.contributions.models import Contribution

    deposits = Contribution.objects.filter(
        contribution_type=Contribution.ContributionType.MONTHLY, reversed_at__isnull=True
    )
    if as_of is not None:
        deposits = deposits.filter(payment_date__lte=as_of)

    totals = dict(
        deposits
        .values_list("member_id")
        .annotate(total=Sum("amount"))
        .values_list("member_id", "total")
    )
    pool = sum(totals.values(), Decimal("0"))

    rows = [
        {
            "member": member,
            "total": totals[member.pk],
            "percent": (totals[member.pk] / pool * 100).quantize(Decimal("0.01")) if pool else Decimal("0"),
            "ratio": totals[member.pk] / pool if pool else Decimal("0"),
        }
        for member in Member.objects.filter(pk__in=totals).order_by("member_code")
    ]

    return {"total": pool, "rows": rows}


def member_investment_summary(member):
    """
    The member's share of FCU's investments (indicative: their total deposits over all
    cycles as a proportion of every member's total deposits over all cycles, applied to
    the portfolio's value) and the bonuses they have received or are due.
    """

    shares = contribution_shares()
    mine_row = next((r for r in shares["rows"] if r["member"].pk == member.pk), None)
    mine = mine_row["total"] if mine_row else Decimal("0")
    share = mine_row["ratio"] if mine_row else Decimal("0")
    total = shares["total"]

    book = portfolio(include_closed=False)
    unit_trust_value = sum(
        (p["market_value"] for p in book["positions"] if p["investment"].kind == Investment.Kind.UNIT_TRUST),
        Decimal("0"),
    )

    lines = DistributionLine.objects.filter(member=member, amount__gt=0).select_related("distribution")

    def total_for(status):
        return sum((l.amount for l in lines if l.distribution.status == status), Decimal("0"))

    return {
        "my_deposits": mine,
        "all_deposits": total,
        "share_percent": (share * 100).quantize(Decimal("0.01")),
        "portfolio_value": book["market_value"],
        "portfolio_share": (book["market_value"] * share).quantize(Decimal("1")),
        "unit_trust": (unit_trust_value * share).quantize(Decimal("1")),
        "distributions_paid": total_for(ProfitDistribution.Status.PAID),
        "distributions_approved": total_for(ProfitDistribution.Status.APPROVED),
        "lines": [l for l in lines if l.distribution.status in (
            ProfitDistribution.Status.APPROVED, ProfitDistribution.Status.PAID)],
    }



# ============================================================
# Profit statement: interest on loans, unit trust, other investments
# ============================================================

ZERO = Decimal("0")


def profit_figures(cycle):
    """
    What FCU earned in `cycle`, from the live records, by source:

    * interest on loans (the interest actually repaid on loans of the cycle),
    * unit trust: earnings received less fees,
    * other investments (microfinance, real estate): earnings received less fees,

    and the expenses of the cycle. The unit trust figure is the GROUP's usual unit trust income:
    the part of the earnings that belongs to members' Other savings (see credit_unit_trust_interest)
    is taken out, because it is credited to those members instead. Net profit is the earnings less
    the expenses.
    """

    from django.db.models import Sum

    from apps.expenses.services import expense_summary
    from apps.reports.services import loan_section

    live = InvestmentTransaction.objects.filter(cycle=cycle, reversed_at__isnull=True)

    def net(kind_filter):
        rows = live.filter(kind_filter)
        income = rows.filter(kind=T.INCOME).aggregate(t=Sum("amount"))["t"] or ZERO
        fees = rows.filter(kind=T.FEE).aggregate(t=Sum("amount"))["t"] or ZERO
        return income - fees

    from django.db.models import Q

    unit_trust_total = net(Q(investment__kind=Investment.Kind.UNIT_TRUST))

    first_month = cycle.start_date.replace(day=1)
    credits = InterestCredit.objects.filter(month__gte=first_month, month__lte=cycle.end_date)
    to_other_savings = credits.aggregate(t=Sum("others_income"))["t"] or ZERO
    unit_trust = unit_trust_total - to_other_savings

    # Months with unit trust earnings while Other savings existed, but not credited yet: until they
    # are, the whole month's earnings still look like the group's.
    credited = set(credits.values_list("month", flat=True))
    uncredited = []
    month = first_month
    while month <= cycle.end_date:
        if (
            month not in credited
            and unit_trust_income_for_month(month) > 0
            and OtherSavingsDeposit.objects.filter(deposit_date__lte=_month_end(month)).exists()
        ):
            uncredited.append(month)
        month = (_month_end(month) + timedelta(days=1))
    other_rows = []
    for kind, label in Investment.Kind.choices:
        if kind == Investment.Kind.UNIT_TRUST:
            continue
        other_rows.append({"kind": kind, "label": label, "amount": net(Q(investment__kind=kind))})

    loan_interest = loan_section(cycle)["interest_earned"]
    other = sum((r["amount"] for r in other_rows), ZERO)
    expenses = expense_summary(cycle)["total"]

    return {
        "loan_interest": loan_interest,
        "unit_trust_income": unit_trust,
        "unit_trust_total": unit_trust_total,
        "unit_trust_to_other_savings": to_other_savings,
        "uncredited_months": uncredited,
        "other_investment_income": other,
        "other_breakdown": other_rows,
        "gross_income": loan_interest + unit_trust + other,
        "expenses": expenses,
        "net_profit": loan_interest + unit_trust + other - expenses,
    }


@transaction.atomic
def capture_profit(cycle, actor):
    """
    Capture (or update) the cycle's profit statement and every member's share. Needs the
    Treasurer or Administrator. Capturing again after changes replaces the figures and
    withdraws the statement from members until it is published again.
    """

    _require(actor, "has_financial_write_access", "capture the profit statement")

    if cycle.status == cycle.Status.UPCOMING:
        raise InvestmentError("An upcoming cycle has no profit to capture.")

    figures = profit_figures(cycle)
    shares = contribution_shares()
    distributable = max(figures["net_profit"], ZERO)
    amounts = split_proportionally(
        distributable, {row["member"].pk: row["total"] for row in shares["rows"]}
    )

    statement = ProfitStatement.objects.select_for_update().filter(cycle=cycle).first()
    was_published = bool(statement and statement.is_published)
    created = statement is None

    if created:
        statement = ProfitStatement(cycle=cycle)

    statement.loan_interest = figures["loan_interest"]
    statement.unit_trust_income = figures["unit_trust_income"]
    statement.other_investment_income = figures["other_investment_income"]
    statement.expenses = figures["expenses"]
    statement.net_profit = figures["net_profit"]
    statement.pool_deposits = shares["total"]
    statement.captured_at = timezone.now()
    statement.captured_by = actor
    statement.published_at = None
    statement.published_by = None
    statement.save()

    statement.shares.all().delete()
    ProfitShare.objects.bulk_create(
        ProfitShare(
            statement=statement,
            member=row["member"],
            deposits=row["total"],
            share_percent=(row["ratio"] * 100).quantize(Decimal("0.0001")),
            amount=amounts.get(row["member"].pk, ZERO),
        )
        for row in shares["rows"]
    )

    log_action(
        AuditLog.Action.CREATE if created else AuditLog.Action.UPDATE,
        instance=statement,
        actor=actor,
        new_value={
            "event": "PROFIT_CAPTURED",
            "loan_interest": str(statement.loan_interest),
            "unit_trust_income": str(statement.unit_trust_income),
            "other_investment_income": str(statement.other_investment_income),
            "expenses": str(statement.expenses),
            "net_profit": str(statement.net_profit),
            "withdrawn_from_members": was_published,
        },
    )

    return statement


@transaction.atomic
def publish_profit(statement, actor):
    """Make the statement visible to every member (Chairperson or Administrator)."""

    _require(actor, "has_chairperson_access", "publish the profit statement")

    statement = ProfitStatement.objects.select_for_update().get(pk=statement.pk)
    statement.published_at = timezone.now()
    statement.published_by = actor
    statement.save(update_fields=["published_at", "published_by"])

    log_action(
        AuditLog.Action.STATUS_CHANGE, instance=statement, actor=actor,
        new_value={"event": "PROFIT_PUBLISHED", "net_profit": str(statement.net_profit)},
    )
    return statement


@transaction.atomic
def unpublish_profit(statement, actor):
    _require(actor, "has_chairperson_access", "withdraw the profit statement")

    statement = ProfitStatement.objects.select_for_update().get(pk=statement.pk)
    statement.published_at = None
    statement.published_by = None
    statement.save(update_fields=["published_at", "published_by"])

    log_action(
        AuditLog.Action.STATUS_CHANGE, instance=statement, actor=actor,
        new_value={"event": "PROFIT_UNPUBLISHED"},
    )
    return statement


@transaction.atomic
def delete_profit_statement(statement, actor, reason):
    _require(actor, "can_delete_records", "delete profit statements")
    reason = _delete_reason(reason)

    statement = ProfitStatement.objects.get(pk=statement.pk)

    log_action(
        AuditLog.Action.DELETE, instance=statement, actor=actor,
        old_value={"cycle": str(statement.cycle), "net_profit": str(statement.net_profit),
                   "published": statement.is_published},
        new_value={"event": "PROFIT_STATEMENT_DELETED", "reason": reason},
    )
    statement.delete()


def member_profit(member):
    """The member's share in every published profit statement, newest first, and their total."""

    shares = list(
        ProfitShare.objects.filter(member=member, statement__published_at__isnull=False)
        .select_related("statement__cycle")
        .order_by("-statement__cycle__start_date")
    )
    return {"shares": shares, "total": sum((s.amount for s in shares), ZERO)}



# ============================================================
# Other savings: unit trust interest credited to members every month
# ============================================================

def _month_start(value):
    return value.replace(day=1)


def _month_end(first):
    from calendar import monthrange

    return first.replace(day=monthrange(first.year, first.month)[1])


def unit_trust_income_for_month(first):
    """Unit trust earnings received in the month, less fees (reversed entries left out)."""

    from django.db.models import Sum

    rows = InvestmentTransaction.objects.filter(
        investment__kind=Investment.Kind.UNIT_TRUST,
        reversed_at__isnull=True,
        transaction_date__gte=first,
        transaction_date__lte=_month_end(first),
    )
    income = rows.filter(kind=T.INCOME).aggregate(t=Sum("amount"))["t"] or ZERO
    fees = rows.filter(kind=T.FEE).aggregate(t=Sum("amount"))["t"] or ZERO
    return income - fees


def _committed(member):
    """Interest already taken out or asked for."""

    from django.db.models import Sum

    return InterestWithdrawal.objects.filter(
        member=member, status__in=[InterestWithdrawal.Status.REQUESTED, InterestWithdrawal.Status.PAID]
    ).aggregate(t=Sum("amount"))["t"] or ZERO


def interest_balance(member):
    """What the member has earned, what they have taken or asked for, and what is available now."""

    from django.db.models import Sum

    earned = MemberInterest.objects.filter(member=member).aggregate(t=Sum("amount"))["t"] or ZERO
    paid = InterestWithdrawal.objects.filter(
        member=member, status=InterestWithdrawal.Status.PAID
    ).aggregate(t=Sum("amount"))["t"] or ZERO
    pending = InterestWithdrawal.objects.filter(
        member=member, status=InterestWithdrawal.Status.REQUESTED
    ).aggregate(t=Sum("amount"))["t"] or ZERO

    return {"earned": earned, "withdrawn": paid, "pending": pending, "available": earned - paid - pending}


def _average_balance(events, first):
    """
    Time-weighted average of a balance over the month starting `first`.

    `events` is a list of (date, amount change). Money counts from the day it arrives, so a
    deposit on the 16th earns for about half of the month, and one on the last day for a day.
    """

    last = _month_end(first)
    days = (last - first).days + 1
    balance = sum((delta for when, delta in events if when < first), ZERO)
    in_month = sorted((when, delta) for when, delta in events if first <= when <= last)

    total = ZERO
    index = 0

    for offset in range(days):
        day = first + timedelta(days=offset)
        while index < len(in_month) and in_month[index][0] <= day:
            balance += in_month[index][1]
            index += 1
        total += balance

    return total / days


def interest_preview(month):
    """
    How the month's unit trust earnings divide, without saving anything.

    The unit trust holds the group's money and members' Other savings together. Its earnings (less
    fees) are divided by how much of the unit trust's money each supplied, averaged over the days
    of the month:

        Other savings part = earnings x (average Other savings balance / average money in the unit trust)

    The rest stays with the group as its usual unit trust income. Each member's part of the Other
    savings portion follows their own average Other savings balance. Deposits and money invested
    count from the day they are dated.
    """

    first = _month_start(month)
    last = _month_end(first)
    income = unit_trust_income_for_month(first)

    capital_events = [
        (t.transaction_date, t.amount if t.kind == T.INVEST else -t.amount)
        for t in InvestmentTransaction.objects.filter(
            investment__kind=Investment.Kind.UNIT_TRUST,
            reversed_at__isnull=True,
            kind__in=[T.INVEST, T.REDEEM],
            transaction_date__lte=last,
        )
    ]
    capital = _average_balance(capital_events, first)

    by_member = {}
    for d in OtherSavingsDeposit.objects.filter(deposit_date__lte=last).select_related("member"):
        by_member.setdefault(d.member_id, []).append((d.deposit_date, d.amount))

    averages = {pk: _average_balance(events, first) for pk, events in by_member.items()}
    others_average = sum(averages.values(), ZERO)

    ratio = ZERO
    if capital > 0:
        ratio = min(others_average / capital, Decimal("1"))

    others_income = (max(income, ZERO) * ratio).quantize(Decimal("1"), rounding=ROUND_DOWN)

    return {
        "month": first,
        "income": income,
        "capital": capital.quantize(Decimal("0.01")),
        "others_average": others_average.quantize(Decimal("0.01")),
        "ratio": ratio,
        "ratio_percent": (ratio * 100).quantize(Decimal("0.01")),
        "others_income": others_income,
        "group_income": income - others_income,
        "averages": averages,
        "all_invested": capital >= others_average,
    }


@transaction.atomic
def record_other_deposit(actor, member, amount, deposit_date, method="CASH", reference=""):
    """Record extra money a member saves with FCU (Treasurer or Administrator)."""

    _require(actor, "has_financial_write_access", "record Other savings")

    amount = Decimal(amount)

    if amount <= 0:
        raise InvestmentError("The amount must be greater than zero.")

    if deposit_date > timezone.localdate():
        raise InvestmentError("A deposit cannot be dated in the future.")

    if method not in OtherSavingsDeposit.Method.values:
        raise InvestmentError("Choose how the money was paid.")

    deposit = OtherSavingsDeposit.objects.create(
        member=member, amount=amount, deposit_date=deposit_date, payment_method=method,
        reference=(reference or "").strip(), recorded_by=actor,
    )

    log_action(
        AuditLog.Action.CREATE, instance=deposit, actor=actor,
        new_value={"event": "OTHER_SAVINGS_DEPOSIT", "member": member.member_code, "amount": str(amount),
                   "deposit_date": deposit_date.isoformat(), "method": method, "reference": deposit.reference},
    )
    return deposit


@transaction.atomic
def delete_other_deposit(deposit, actor, reason):
    _require(actor, "can_delete_records", "delete Other savings deposits")
    reason = _delete_reason(reason)

    deposit = OtherSavingsDeposit.objects.select_related("member").get(pk=deposit.pk)

    if InterestCredit.objects.filter(month__gte=_month_start(deposit.deposit_date)).exists():
        raise InvestmentError(
            "Interest has already been credited for the month of this deposit or later. Delete those "
            "credited months first, or record a correction instead."
        )

    log_action(
        AuditLog.Action.DELETE, instance=deposit, actor=actor,
        old_value={"member": deposit.member.member_code, "amount": str(deposit.amount),
                   "deposit_date": deposit.deposit_date.isoformat()},
        new_value={"event": "OTHER_SAVINGS_DEPOSIT_DELETED", "reason": reason},
    )
    deposit.delete()


@transaction.atomic
def credit_unit_trust_interest(month, actor):
    """
    Credit a month's interest to members' Other savings (Treasurer or Administrator).

    Only the Other savings part of the unit trust's earnings is credited (see interest_preview);
    the group keeps the rest. Doing it again for the same month recalculates it, as long as no member
    would end up with less than they have already withdrawn or asked for.
    """

    _require(actor, "has_financial_write_access", "credit Other savings interest")

    first = _month_start(month)

    if first > _month_start(timezone.localdate()):
        raise InvestmentError("Interest cannot be credited for a month that has not started.")

    preview = interest_preview(first)

    if preview["income"] <= 0:
        raise InvestmentError(
            f"No unit trust earnings (after fees) are recorded for {first:%B %Y}. Record them on the "
            "unit trust investment first."
        )

    if preview["others_average"] <= 0:
        raise InvestmentError(f"No Other savings were held in {first:%B %Y}, so there is nothing to credit.")

    if preview["capital"] <= 0:
        raise InvestmentError(
            f"No money is recorded as invested in the unit trust in {first:%B %Y}. Record the money "
            "placed in the unit trust (including Other savings) as invested first."
        )

    amounts = split_proportionally(preview["others_income"], preview["averages"])

    credit = InterestCredit.objects.select_for_update().filter(month=first).first()
    created = credit is None

    if created:
        credit = InterestCredit(month=first)

    # Never leave a member with less than they have already taken or requested.
    if not created:
        old = {l.member_id: l.amount for l in credit.lines.all()}
        for member in Member.objects.filter(pk__in=set(old) | set(amounts)):
            balance = interest_balance(member)
            new_total = balance["earned"] - old.get(member.pk, ZERO) + amounts.get(member.pk, ZERO)
            if new_total < balance["withdrawn"] + balance["pending"]:
                raise InvestmentError(
                    f"{member.member_code} has already withdrawn or asked for more than the recalculated "
                    "interest, so the month cannot be recalculated."
                )

    credit.income = preview["income"]
    credit.pool_deposits = preview["others_average"]
    credit.capital = preview["capital"]
    credit.others_income = preview["others_income"]
    credit.group_income = preview["group_income"]
    credit.credited_at = timezone.now()
    credit.credited_by = actor
    credit.save()

    credit.lines.all().delete()
    pool = sum(preview["averages"].values(), ZERO)
    MemberInterest.objects.bulk_create(
        MemberInterest(
            credit=credit,
            member_id=pk,
            deposits=average.quantize(Decimal("0.01")),
            share_percent=(average / pool * 100).quantize(Decimal("0.0001")) if pool else ZERO,
            amount=amounts.get(pk, ZERO),
        )
        for pk, average in preview["averages"].items()
    )

    log_action(
        AuditLog.Action.CREATE if created else AuditLog.Action.UPDATE,
        instance=credit,
        actor=actor,
        new_value={
            "event": "UNIT_TRUST_INTEREST_CREDITED",
            "month": first.isoformat(),
            "unit_trust_income": str(preview["income"]),
            "capital": str(preview["capital"]),
            "other_savings_average": str(preview["others_average"]),
            "credited_to_other_savings": str(preview["others_income"]),
            "kept_by_the_group": str(preview["group_income"]),
        },
    )

    return credit


@transaction.atomic
def delete_interest_credit(credit, actor, reason):
    _require(actor, "can_delete_records", "delete credited interest")
    reason = _delete_reason(reason)

    credit = InterestCredit.objects.get(pk=credit.pk)

    for line in credit.lines.select_related("member"):
        balance = interest_balance(line.member)
        if balance["earned"] - line.amount < balance["withdrawn"] + balance["pending"]:
            raise InvestmentError(
                f"{line.member.member_code} has already withdrawn or asked for part of this interest, "
                "so it cannot be deleted."
            )

    log_action(
        AuditLog.Action.DELETE, instance=credit, actor=actor,
        old_value={"month": credit.month.isoformat(), "income": str(credit.income)},
        new_value={"event": "INTEREST_CREDIT_DELETED", "reason": reason},
    )
    credit.delete()


@transaction.atomic
def request_withdrawal(member, amount, actor):
    """A member asks to take out some or all of the interest available to them."""

    if actor is None or actor.member_id != member.pk:
        raise PermissionDenied("You can only withdraw your own interest.")

    amount = Decimal(amount)

    if amount <= 0 or amount != amount.to_integral_value():
        raise InvestmentError("Enter a whole number of shillings greater than zero.")

    available = interest_balance(member)["available"]

    if amount > available:
        raise InvestmentError(f"Only UGX {available:,.0f} is available to withdraw.")

    withdrawal = InterestWithdrawal.objects.create(member=member, amount=amount, requested_by=actor)

    log_action(
        AuditLog.Action.CREATE, instance=withdrawal, actor=actor,
        new_value={"event": "INTEREST_WITHDRAWAL_REQUESTED", "amount": str(amount)},
    )
    return withdrawal


def _open_withdrawal(withdrawal):
    withdrawal = InterestWithdrawal.objects.select_for_update().select_related("member").get(pk=withdrawal.pk)
    if withdrawal.status != InterestWithdrawal.Status.REQUESTED:
        raise InvestmentError("This request has already been dealt with.")
    return withdrawal


@transaction.atomic
def pay_withdrawal(withdrawal, actor, method, reference=""):
    _require(actor, "has_financial_write_access", "pay interest withdrawals")

    withdrawal = _open_withdrawal(withdrawal)

    if method not in ("CASH", "BANK", "MOBILE_MONEY"):
        raise InvestmentError("Choose how the money was paid.")

    withdrawal.status = InterestWithdrawal.Status.PAID
    withdrawal.decided_at = timezone.now()
    withdrawal.decided_by = actor
    withdrawal.payment_method = method
    withdrawal.reference = (reference or "").strip()
    withdrawal.save()

    log_action(
        AuditLog.Action.UPDATE, instance=withdrawal, actor=actor,
        new_value={"event": "INTEREST_WITHDRAWAL_PAID", "amount": str(withdrawal.amount),
                   "method": method, "reference": withdrawal.reference},
    )
    return withdrawal


@transaction.atomic
def reject_withdrawal(withdrawal, actor, note):
    _require(actor, "has_financial_write_access", "reject interest withdrawals")

    withdrawal = _open_withdrawal(withdrawal)
    note = (note or "").strip()

    if not note:
        raise InvestmentError("Give the member a reason for rejecting the request.")

    withdrawal.status = InterestWithdrawal.Status.REJECTED
    withdrawal.decided_at = timezone.now()
    withdrawal.decided_by = actor
    withdrawal.note = note
    withdrawal.save()

    log_action(
        AuditLog.Action.UPDATE, instance=withdrawal, actor=actor,
        new_value={"event": "INTEREST_WITHDRAWAL_REJECTED", "note": note},
    )
    return withdrawal


@transaction.atomic
def cancel_withdrawal(withdrawal, actor):
    withdrawal = _open_withdrawal(withdrawal)

    if actor is None or actor.member_id != withdrawal.member_id:
        raise PermissionDenied("Only the member can cancel their own request.")

    withdrawal.status = InterestWithdrawal.Status.CANCELLED
    withdrawal.decided_at = timezone.now()
    withdrawal.save()

    log_action(
        AuditLog.Action.UPDATE, instance=withdrawal, actor=actor,
        new_value={"event": "INTEREST_WITHDRAWAL_CANCELLED"},
    )
    return withdrawal


@transaction.atomic
def delete_withdrawal(withdrawal, actor, reason):
    _require(actor, "can_delete_records", "delete interest withdrawals")
    reason = _delete_reason(reason)

    withdrawal = InterestWithdrawal.objects.get(pk=withdrawal.pk)

    log_action(
        AuditLog.Action.DELETE, instance=withdrawal, actor=actor,
        old_value={"member": withdrawal.member.member_code, "amount": str(withdrawal.amount),
                   "status": withdrawal.status},
        new_value={"event": "INTEREST_WITHDRAWAL_DELETED", "reason": reason},
    )
    withdrawal.delete()


def principal_balance(member):
    """What the member has saved in Other savings (their own extra money, not interest)."""

    from django.db.models import Sum

    return OtherSavingsDeposit.objects.filter(member=member).aggregate(t=Sum("amount"))["t"] or ZERO


def other_savings(member):
    """
    Everything a member sees on their Other savings page: what they have saved, the interest
    earned and available, month-by-month history and withdrawals, and what they can expect each
    month at their current balance. The expectation uses the latest month credited and is an
    estimate, not a promise.
    """

    balance = interest_balance(member)
    principal = principal_balance(member)
    lines = list(
        MemberInterest.objects.filter(member=member).select_related("credit").order_by("-credit__month")
    )
    latest = InterestCredit.objects.order_by("-month").first()

    monthly_rate = ZERO     # interest earned in the latest month per shilling of Other savings
    per_100k = ZERO
    estimate = ZERO

    if latest and latest.pool_deposits > 0:
        monthly_rate = latest.others_income / latest.pool_deposits
        per_100k = (monthly_rate * Decimal("100000")).quantize(Decimal("1"))
        estimate = (monthly_rate * principal).quantize(Decimal("1"))

    average = ZERO
    if lines:
        average = (sum((l.amount for l in lines), ZERO) / len(lines)).quantize(Decimal("1"))

    return {
        "balance": balance,
        "principal": principal,
        "deposits": list(OtherSavingsDeposit.objects.filter(member=member)),
        "lines": lines,
        "withdrawals": list(InterestWithdrawal.objects.filter(member=member)),
        "latest": latest,
        "monthly_rate_percent": (monthly_rate * 100).quantize(Decimal("0.01")),
        "estimate_monthly": estimate,
        "per_100k": per_100k,
        "average_monthly": average,
        "months_credited": len(lines),
    }


def other_savings_overview():
    """For management: savings held and interest owed per member, months credited, open requests."""

    from django.db.models import Sum

    member_ids = set(OtherSavingsDeposit.objects.values_list("member_id", flat=True)) | set(
        MemberInterest.objects.values_list("member_id", flat=True)
    )
    rows = [
        {"member": m, "principal": principal_balance(m), **interest_balance(m)}
        for m in Member.objects.filter(pk__in=member_ids).order_by("member_code")
    ]

    return {
        "rows": rows,
        "principal": sum((r["principal"] for r in rows), ZERO),
        "owed": sum((r["available"] + r["pending"] for r in rows), ZERO),
        "paid": InterestWithdrawal.objects.filter(status=InterestWithdrawal.Status.PAID)
        .aggregate(t=Sum("amount"))["t"] or ZERO,
        "credits": list(InterestCredit.objects.select_related("credited_by")),
        "deposits": list(OtherSavingsDeposit.objects.select_related("member", "recorded_by")[:30]),
        "requests": list(
            InterestWithdrawal.objects.filter(status=InterestWithdrawal.Status.REQUESTED).select_related("member")
        ),
        "recent": list(
            InterestWithdrawal.objects.exclude(status=InterestWithdrawal.Status.REQUESTED)
            .select_related("member")[:20]
        ),
    }
