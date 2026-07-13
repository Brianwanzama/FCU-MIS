"""
Contributions — the data layer (SRS v1.2, Phase 3, Deliverable 1).

A Contribution row is a TRANSACTION: one payment, as it actually happened. It is
not a monthly summary. A member may settle a single month in two instalments, pay
two months at once, or pay late — each of those is a separate row, and the summary
figures (months contributed, total, remaining balance) are derived by aggregating
rows, never by storing them.

The distinction that makes this work is between two dates:

    payment_date   WHEN the money arrived.
    period_month   WHICH month the money settles.

They are routinely different, and the difference is not an edge case — Manual §3.2
gives a Payment Window that runs to the 5th of the FOLLOWING month, so a June
contribution paid on 4 July is on time. Without period_month there is no way to
tell that payment apart from an early July one, which would make the late-payment
penalty (§3.3), the months-contributed counts, and the Inactive trigger (§2.1(b))
all uncomputable.

Manual references:
    §1.1     "Contribution" includes BOTH the monthly savings and the Emergency Fund
             payment — hence ContributionType, and hence they are counted separately.
    §3.1     UGX 100,000 savings + UGX 5,000 Emergency Fund, per member, per month.
    §3.2     The Payment Window: 1st of the month to the 5th of the month following.
    §6.1     Emergency Fund contributions are ring-fenced to the Emergency Fund.
    §10.1    The Treasurer maintains detailed records of Contributions.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

"""
Future architecture

The Contribution model represents only a financial transaction.

Business workflows will be implemented in ContributionService.

That service will eventually:

- validate business rules
- create Contribution rows
- write Audit Log entries
- post General Ledger entries
- refresh Member summaries
- refresh Cycle summaries
- refresh Financial Position snapshots

The model intentionally contains no accounting logic.
"""
class Contribution(models.Model):
    """
    A single payment made by a Member into FCU during a Cycle.

    Deletion behaviour is PROTECT on every relation, deliberately:

    * member — a Member who has contributed has financial history. Manual §2.5(c)
      requires those contributions to survive the member's withdrawal so they can
      be refunded net of dues, and §4.6 allows recovery of a defaulted loan from
      them. CASCADE here would erase that history the moment anyone tidied up the
      member list, silently and irreversibly. A member with contributions should
      simply not be deletable.

    * cycle — apps.cycles already refuses to delete a cycle carrying financial
      records; PROTECT is the database-level half of that promise.

    * recorded_by — the Treasurer or Administrator who entered the row. Removing a
      user must never quietly rewrite who recorded what.
    """

    class ContributionType(models.TextChoices):
        MONTHLY = "MONTHLY", "Monthly Contribution"
        EMERGENCY = "EMERGENCY", "Emergency Contribution"

    class PaymentMethod(models.TextChoices):
        CASH = "CASH", "Cash"
        BANK = "BANK", "Bank Transfer"
        MOBILE_MONEY = "MOBILE_MONEY", "Mobile Money"

    member = models.ForeignKey(
        "members.Member",
        on_delete=models.PROTECT,
        related_name="contributions",
        help_text="The Member whose contribution this is.",
    )

    cycle = models.ForeignKey(
        "cycles.Cycle",
        on_delete=models.PROTECT,
        related_name="contributions",
        help_text="The Financial Cycle this contribution counts toward.",
    )

    contribution_type = models.CharField(
        max_length=20,
        choices=ContributionType.choices,
        help_text=(
            "Manual §1.1 defines a Contribution as including both the monthly "
            "savings payment and the Emergency Fund payment. They are tracked "
            "separately because a member may pay one and not the other, and the "
            "Emergency Fund is ring-fenced under §6.1."
        ),
    )

    amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        help_text=(
            "The amount actually paid — not the amount due. Partial payments are "
            "legitimate and are stored as separate rows; the shortfall is derived, "
            "never stored."
        ),
    )

    period_month = models.DateField(
        help_text=(
            "WHICH month this payment settles, normalised to the 1st of that month. "
            "Distinct from payment_date: Manual §3.2 lets a month be paid up to the "
            "5th of the following month, so a June contribution may legitimately "
            "carry a July payment_date."
        ),
    )

    payment_date = models.DateField(
        help_text="WHEN the money was received. Used to judge lateness against Manual §3.2.",
    )

    payment_method = models.CharField(
        max_length=20,
        choices=PaymentMethod.choices,
        help_text="How the money reached FCU.",
    )

    reference = models.CharField(
        max_length=100,
        blank=True,
        help_text=(
            "Optional. Mobile-money transaction ID, bank slip number, or receipt "
            "number — whatever lets this row be traced back to a real movement of money."
        ),
    )

    remarks = models.TextField(
        blank=True,
        help_text="Optional free text, e.g. an explanation for a part payment.",
    )

    recorded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="contributions_recorded",
        help_text="The Treasurer or Administrator who entered this row (Manual §10.1).",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-payment_date", "-created_at"]
        verbose_name = "Contribution"
        verbose_name_plural = "Contributions"
        indexes = [
            models.Index(fields=["member"]),
            models.Index(fields=["cycle"]),
            models.Index(fields=["payment_date"]),
            models.Index(fields=["contribution_type"]),
            models.Index(fields=["member", "cycle"]),
            # The aggregation key for the Member Contribution Summary: months
            # contributed is a count of DISTINCT period_month values per member,
            # per cycle, per type. This index is what makes that read cheap.
            models.Index(fields=["member", "cycle", "contribution_type", "period_month"]),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(amount__gt=Decimal("0")),
                name="contributions_amount_greater_than_zero",
            ),
        ]

    def __str__(self) -> str:
        return (
            f"{self.member.member_code} - "
            f"Cycle {self.cycle.cycle_number} - "
            f"{self.get_contribution_type_display()} - "
            f"UGX {self.amount:,.0f}"
        )
    @property
    def is_monthly(self) -> bool:
        """
        Return True if this is a Monthly Contribution.
        """
        return self.contribution_type == self.ContributionType.MONTHLY

    @property
    def is_emergency(self) -> bool:
        """
        Return True if this is an Emergency Contribution.
        """
        return self.contribution_type == self.ContributionType.EMERGENCY
    def save(self, *args, **kwargs) -> None:
        # Normalise period_month to the 1st before it ever reaches the database.
        # Every aggregation groups on this column, so two rows for the same month
        # written with different days would silently split into two "months
        # contributed" — the exact miscount this field exists to prevent.
        if self.period_month is not None:
            self.period_month = self.period_month.replace(day=1)

        self.full_clean()

        super().save(*args, **kwargs)

    def clean(self) -> None:
        errors: dict[str, ValidationError] = {}

        if self.amount is not None and self.amount <= Decimal("0"):
            errors["amount"] = ValidationError(
                "A contribution must be greater than zero.",
                code="non_positive_amount",
            )

        today: date = timezone.localdate()

        if self.payment_date is not None and self.payment_date > today:
            errors["payment_date"] = ValidationError(
                "A contribution cannot be recorded as paid in the future.",
                code="future_payment_date",
            )

        # The cycle must be ACTIVE — but only when the row is first created.
        #
        # Enforcing it on every save would make historical Cycles 1-4 (which are
        # CLOSED) impossible to import, and would make an existing row in a
        # since-closed cycle impossible to correct a typo in. Closing a cycle
        # locks it against NEW records; it does not freeze the rows already there
        # into unmaintainable objects. The audit trail is what governs edits.
        if self._state.adding and self.cycle_id:
            from apps.cycles.models import Cycle

            if self.cycle.status != Cycle.Status.ACTIVE:
                errors["cycle"] = ValidationError(
                    "Contributions may only be recorded against the ACTIVE cycle. "
                    "%(cycle)s is %(status)s.",
                    code="cycle_not_active",
                    params={
                        "cycle": str(self.cycle),
                        "status": self.cycle.get_status_display(),
                    },
                )

        # A contribution must settle a month that the cycle actually covers,
        # otherwise it would inflate that cycle's totals with money belonging to
        # a different period.
        if self.period_month is not None and self.cycle_id:
            covered = self.period_month.replace(day=1)
            cycle_start = self.cycle.start_date.replace(day=1)
            cycle_end = self.cycle.end_date.replace(day=1)

            if not (cycle_start <= covered <= cycle_end):
                errors["period_month"] = ValidationError(
                    "%(month)s falls outside %(cycle)s, which covers "
                    "%(start)s to %(end)s.",
                    code="period_outside_cycle",
                    params={
                        "month": covered.strftime("%B %Y"),
                        "cycle": str(self.cycle),
                        "start": self.cycle.start_date.strftime("%B %Y"),
                        "end": self.cycle.end_date.strftime("%B %Y"),
                    },
                )

        if errors:
            raise ValidationError(errors)
