"""
Investments and profit distribution (Manual v3.0, sections 7 and 8).

FCU may invest in unit trusts, microfinance ventures and real estate, always
registered in FCU's name (collective ownership). Profits are mostly reinvested;
when FCU decides to share them, they are distributed as bonuses in proportion to
each member's contributions, to Active members in Good Standing only (8.2, 8.3).

Every movement of money is a transaction that is recorded with who and when, can
be reversed (never deleted), and is audited.
"""

from decimal import Decimal

from django.conf import settings
from django.db import models


class Investment(models.Model):
    class Kind(models.TextChoices):
        UNIT_TRUST = "UNIT_TRUST", "Unit trust"
        MICROFINANCE = "MICROFINANCE", "Microfinance venture"
        REAL_ESTATE = "REAL_ESTATE", "Real estate"

    class Status(models.TextChoices):
        ACTIVE = "ACTIVE", "Active"
        CLOSED = "CLOSED", "Closed"

    name = models.CharField(max_length=150)
    kind = models.CharField(max_length=20, choices=Kind.choices)
    provider = models.CharField(max_length=150, blank=True, help_text="Fund manager, bank or counterparty.")
    account_reference = models.CharField(max_length=100, blank=True, help_text="Account, folio or title number.")
    registered_in_fcu_name = models.BooleanField(
        default=True,
        help_text="Manual 7.2(a): all investments are registered in FCU's name.",
    )
    opened_on = models.DateField()
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.ACTIVE)
    notes = models.TextField(blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["status", "name"]

    def __str__(self):
        return self.name


class InvestmentTransaction(models.Model):
    class Kind(models.TextChoices):
        INVEST = "INVEST", "Money invested"
        REDEEM = "REDEEM", "Money returned (redemption or sale)"
        INCOME = "INCOME", "Earnings received (interest, dividend, rent)"
        FEE = "FEE", "Fees and charges"
        VALUATION = "VALUATION", "Market value update"

    investment = models.ForeignKey(Investment, on_delete=models.PROTECT, related_name="transactions")
    kind = models.CharField(max_length=12, choices=Kind.choices)
    amount = models.DecimalField(
        max_digits=14, decimal_places=2,
        help_text="For a market value update this is the TOTAL value of the holding on that date, not a payment.",
    )
    transaction_date = models.DateField()
    reference = models.CharField(max_length=100, blank=True)
    notes = models.TextField(blank=True)
    cycle = models.ForeignKey(
        "cycles.Cycle", on_delete=models.PROTECT, related_name="investment_transactions",
        help_text="The cycle in which it was recorded.",
    )
    recorded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    reversed_at = models.DateTimeField(null=True, blank=True)
    reversed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    reversal_reason = models.TextField(blank=True)

    class Meta:
        ordering = ["-transaction_date", "-id"]
        constraints = [
            models.CheckConstraint(condition=models.Q(amount__gte=0), name="investment_amount_not_negative"),
        ]

    def __str__(self):
        return f"{self.investment} {self.get_kind_display()} {self.amount}"


class ProfitDistribution(models.Model):
    """
    A bonus FCU shares among its members (Manual 8.2). Drafted by the Treasurer,
    approved with the General Meeting's resolution reference by the Administrator
    or Chairperson (not the person who drafted it), then marked paid.
    """

    class Status(models.TextChoices):
        DRAFT = "DRAFT", "Draft"
        APPROVED = "APPROVED", "Approved"
        PAID = "PAID", "Paid"
        CANCELLED = "CANCELLED", "Cancelled"

    cycle = models.ForeignKey("cycles.Cycle", on_delete=models.PROTECT, related_name="distributions")
    description = models.CharField(max_length=200, help_text="Where the profit came from, e.g. loan interest Jan-Jun 2026.")
    total_amount = models.DecimalField(max_digits=14, decimal_places=2)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.DRAFT)

    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    resolution_reference = models.CharField(
        max_length=150, blank=True, help_text="The General Meeting resolution that approved sharing this profit."
    )
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    approved_at = models.DateTimeField(null=True, blank=True)
    paid_on = models.DateField(null=True, blank=True)
    paid_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    cancel_reason = models.TextField(blank=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.CheckConstraint(condition=models.Q(total_amount__gt=0), name="distribution_total_positive"),
        ]

    def __str__(self):
        return f"{self.description} ({self.get_status_display()})"


class DistributionLine(models.Model):
    distribution = models.ForeignKey(ProfitDistribution, on_delete=models.CASCADE, related_name="lines")
    member = models.ForeignKey("members.Member", on_delete=models.PROTECT, related_name="distribution_lines")
    contribution_base = models.DecimalField(
        max_digits=14, decimal_places=2,
        help_text="The member's accumulated deposits when the shares were worked out.",
    )
    eligible = models.BooleanField(default=False)
    reason = models.CharField(max_length=200, blank=True, help_text="Why the member gets nothing, if they do not.")
    share_percent = models.DecimalField(max_digits=9, decimal_places=4, default=Decimal("0"))
    amount = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0"))

    class Meta:
        ordering = ["member__member_code"]
        constraints = [
            models.UniqueConstraint(fields=["distribution", "member"], name="one_line_per_member"),
        ]


class ProfitStatement(models.Model):
    """
    The profit FCU made in a cycle, captured from the records: interest on loans, earnings on
    unit trust, earnings on other investments, less expenses. Management captures (and re-captures)
    it, then the Chairperson or Administrator publishes it; from then on every member can see their
    own share, worked out from their relative contribution (total deposits over all cycles as a
    percentage of everyone's). It is a statement of profit, not a payout: bonuses are still approved
    and paid through a profit distribution.
    """

    cycle = models.OneToOneField("cycles.Cycle", on_delete=models.PROTECT, related_name="profit_statement")

    loan_interest = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0"))
    unit_trust_income = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0"))
    other_investment_income = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0"))
    expenses = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0"))
    net_profit = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0"))
    pool_deposits = models.DecimalField(
        max_digits=14, decimal_places=2, default=Decimal("0"),
        help_text="Everyone's total deposits over all cycles when the shares were worked out.",
    )

    captured_at = models.DateTimeField()
    captured_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    published_at = models.DateTimeField(null=True, blank=True)
    published_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )

    class Meta:
        ordering = ["-cycle__start_date"]

    def __str__(self):
        return f"Profit of {self.cycle}"

    @property
    def is_published(self):
        return self.published_at is not None

    @property
    def gross_income(self):
        return self.loan_interest + self.unit_trust_income + self.other_investment_income


class ProfitShare(models.Model):
    statement = models.ForeignKey(ProfitStatement, on_delete=models.CASCADE, related_name="shares")
    member = models.ForeignKey("members.Member", on_delete=models.PROTECT, related_name="profit_shares")
    deposits = models.DecimalField(max_digits=14, decimal_places=2, help_text="Total deposits over all cycles.")
    share_percent = models.DecimalField(max_digits=9, decimal_places=4, default=Decimal("0"))
    amount = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0"))

    class Meta:
        ordering = ["member__member_code"]
        constraints = [
            models.UniqueConstraint(fields=["statement", "member"], name="one_profit_share_per_member"),
        ]

    def __str__(self):
        return f"{self.member.member_code} {self.amount}"
