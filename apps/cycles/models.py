"""
Financial Year and Financial Cycle, the temporal backbone of FCU-MIS (SRS v1.2, Phase 2).

Every financial record in every later phase (contributions, loans, repayments,
distributions) hangs off exactly one Cycle. Nothing here knows about money
movement itself; the General Ledger remains the single financial source of truth.
What this module owns is *when* a thing happened and *whether that period is
still open for writing*.

Manual references:
    2.1(b)  a Financial Cycle is six (6) months
    3.1     UGX 100,000 savings + UGX 5,000 Emergency Fund per member per month
    3.2     the Payment Window runs to the 10th of the *following* month
    4.5(d)  no loan may extend beyond the currently running Financial Cycle
    10.2    the Treasurer reports bi-annually - i.e. once per Cycle, twice per Financial Year
    11.4    the General Meeting may amend the contribution amounts
"""

from calendar import monthrange
from datetime import date
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import F, Q
from django.utils import timezone

# Manual 2.1(b). Anything other than this needs an audited Administrator override.
STATUTORY_CYCLE_MONTHS = 6

MONTH_NAMES = [
    "", "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
]


# Manual 3.2 (v3.0): a month's Contributions are due by this day of the NEXT month.
PAYMENT_WINDOW_LAST_DAY = 10


def _last_day_of_month(year: int, month: int) -> date:
    return date(year, month, monthrange(year, month)[1])


def _add_months(d: date, months: int) -> date:
    """Returns the first day of the month `months` after d's month."""
    zero_based = (d.year * 12 + (d.month - 1)) + months
    return date(zero_based // 12, (zero_based % 12) + 1, 1)


class FinancialYear(models.Model):
    """
    A calendar year, grouping the two Cycles that run inside it (Jan-Jun, Jul-Dec).

    This exists because Manual 10.2 requires *bi-annual* Treasurer reporting and
    the Vision-2028 net-worth targets are stated per year - both of which need a
    stable annual grouping to aggregate against. It is derived automatically from
    a Cycle's start date rather than being hand-managed, so it can never drift
    out of step with the cycles it contains.
    """

    year = models.PositiveSmallIntegerField(
        unique=True,
        help_text="Calendar year, e.g. 2026.",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-year"]
        verbose_name = "Financial year"
        verbose_name_plural = "Financial years"

    def __str__(self):
        return f"FY{self.year}"

    @property
    def label(self):
        return f"Financial Year {self.year}"

    @property
    def is_complete(self):
        """A financial year is complete once it holds two closed cycles."""
        return self.cycles.filter(status=Cycle.Status.CLOSED).count() >= 2


class Cycle(models.Model):
    """
    A single Financial Cycle (Manual 1.1, 2.1(b)) - six months by default.

    Design decisions worth knowing before you change anything here:

    * `end_date` and `per_member_target` are DERIVED, never typed. `start_date`
      and `duration_months` are the only inputs. This removes any possibility of
      the stated end date and the stated duration disagreeing with each other -
      which matters a great deal once Manual 4.5(d) ("no loan may extend beyond
      the currently running Financial Cycle") is enforced against these dates.

    * Deposits and emergency contributions are SEPARATE obligations:
      `member_deposit_target` (100,000 x 6 = UGX 600,000) and
      `member_emergency_target` (5,000 x 6 = UGX 30,000). `per_member_target`
      is only their stored sum (UGX 630,000) and must not be reported as the
      deposit target. Unit-wide figures are deliberately NOT stored - they
      depend on how many members are eligible, which changes mid-cycle. Read
      them from `unit_deposit_target` / `unit_emergency_target` instead.

    * CLOSED means LOCKED. Closing is an explicit, deliberate act with pre-close
      validation - it is never a silent side effect of activating some other
      cycle. Reopening is Administrator-only and requires a written reason, which
      is written to the audit log.
    """

    class Status(models.TextChoices):
        UPCOMING = "UPCOMING", "Upcoming"
        ACTIVE = "ACTIVE", "Active"
        CLOSED = "CLOSED", "Closed"

    # ------------------------------------------------------------------
    # Relations that would make a cycle undeletable.
    #
    # Every later phase that adds a ForeignKey to Cycle MUST add its
    # related_name here. This is the registry `is_deletable()` consults; a
    # deletion guard that silently forgets to check a new table is worse than
    # no guard at all, so the list is explicit rather than introspected.
    # ------------------------------------------------------------------
    PROTECTED_RELATIONS = (
        # "contributions",   # Phase 3
        # "ledger_entries",  # Phase 4 (General Ledger)
        # "loans",           # Phase 5
        # "repayments",      # Phase 6
    )

    financial_year = models.ForeignKey(
        FinancialYear,
        on_delete=models.PROTECT,
        related_name="cycles",
        editable=False,
        help_text="Derived from start_date - not entered by hand.",
    )

    cycle_number = models.PositiveIntegerField(
        unique=True,
        help_text="Sequential across FCU's whole history: Cycle 1, Cycle 2, ... Never reused.",
    )

    name = models.CharField(
        max_length=100,
        blank=True,
        help_text='e.g. "January-June 2026". Left blank, it is generated from the dates.',
    )

    start_date = models.DateField(
        help_text="First day the cycle covers.",
    )

    duration_months = models.PositiveSmallIntegerField(
        default=STATUTORY_CYCLE_MONTHS,
        help_text=(
            f"Manual 2.1(b) fixes a Financial Cycle at {STATUTORY_CYCLE_MONTHS} months. "
            "Anything else requires a written override reason below."
        ),
    )

    duration_override_reason = models.TextField(
        blank=True,
        help_text=(
            f"Mandatory whenever duration_months is not {STATUTORY_CYCLE_MONTHS}. "
            "Recorded in the audit log. Exists mainly to allow the historical "
            "Cycles 1-4 import to reflect what actually happened."
        ),
    )

    end_date = models.DateField(
        editable=False,
        help_text="Derived: last day of the final month of the cycle.",
    )

    monthly_savings_amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal(settings.FCU_DEFAULT_MONTHLY_SAVINGS),
        help_text="Manual 3.1 - UGX 100,000. Amendable only by General Meeting resolution (11.4).",
    )

    monthly_emergency_amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal(settings.FCU_DEFAULT_MONTHLY_EMERGENCY),
        help_text="Manual 3.1 / 6.1 - UGX 5,000. Amendable only by General Meeting resolution (11.4).",
    )

    per_member_target = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        editable=False,
        help_text="Derived: (monthly savings + monthly emergency) x duration_months.",
    )

    status = models.CharField(
        max_length=10,
        choices=Status.choices,
        default=Status.UPCOMING,
        editable=False,
        help_text="Changed only through activate() / close() / reopen(), never edited directly.",
    )

    # Partial-unique trick: Postgres does not collide on NULLs, so this column
    # holds True on exactly the active cycle and NULL everywhere else. That gives
    # us the "only one ACTIVE cycle" rule at the DATABASE level, not merely in
    # application code where a race or a stray admin save could slip past it.
    active_singleton = models.BooleanField(
        null=True,
        default=None,
        editable=False,
    )

    activated_at = models.DateTimeField(null=True, blank=True, editable=False)
    activated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True, blank=True, editable=False,
        on_delete=models.SET_NULL, related_name="cycles_activated",
    )
    closed_at = models.DateTimeField(null=True, blank=True, editable=False)
    closed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True, blank=True, editable=False,
        on_delete=models.SET_NULL, related_name="cycles_closed",
    )
    reopen_count = models.PositiveSmallIntegerField(
        default=0,
        editable=False,
        help_text="How many times this cycle has been reopened after closing. Should stay 0.",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-cycle_number"]
        constraints = [
            models.UniqueConstraint(
                fields=["active_singleton"],
                name="cycles_only_one_active_cycle",
            ),
            models.CheckConstraint(
                condition=Q(end_date__gte=F("start_date")),
                name="cycles_end_date_after_start_date",
            ),
            models.CheckConstraint(
                condition=Q(duration_months__gte=1),
                name="cycles_duration_at_least_one_month",
            ),
        ]

    def __str__(self):
        return f"Cycle {self.cycle_number} ({self.name})"

    # ------------------------------------------------------------------
    # Derivation
    # ------------------------------------------------------------------

    def derive_end_date(self):
        if not self.start_date or not self.duration_months:
            return None
        final_month_first = _add_months(self.start_date, self.duration_months - 1)
        return _last_day_of_month(final_month_first.year, final_month_first.month)

    def derive_per_member_target(self):
        savings = self.monthly_savings_amount or Decimal("0")
        emergency = self.monthly_emergency_amount or Decimal("0")
        return (Decimal(savings) + Decimal(emergency)) * self.duration_months

    def derive_name(self):
        end = self.end_date or self.derive_end_date()
        if not end or not self.start_date:
            return ""
        if self.start_date.year == end.year:
            return f"{MONTH_NAMES[self.start_date.month]}-{MONTH_NAMES[end.month]} {end.year}"
        return (
            f"{MONTH_NAMES[self.start_date.month]} {self.start_date.year}-"
            f"{MONTH_NAMES[end.month]} {end.year}"
        )

    def save(self, *args, **kwargs):
        self.end_date = self.derive_end_date()
        self.per_member_target = self.derive_per_member_target()
        if not self.name:
            self.name = self.derive_name()
        if self.start_date and not self.financial_year_id:
            fy, _ = FinancialYear.objects.get_or_create(year=self.start_date.year)
            self.financial_year = fy
        # Keep the DB-level singleton column in lockstep with status.
        self.active_singleton = True if self.status == self.Status.ACTIVE else None
        super().save(*args, **kwargs)

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def clean(self):
        errors = {}

        if self.duration_months and self.duration_months != STATUTORY_CYCLE_MONTHS:
            if not (self.duration_override_reason or "").strip():
                errors["duration_override_reason"] = ValidationError(
                    f"Manual 2.1(b) fixes a Financial Cycle at {STATUTORY_CYCLE_MONTHS} months. "
                    f"To record a {self.duration_months}-month cycle you must state why.",
                    code="override_reason_required",
                )

        if self.monthly_savings_amount is not None and self.monthly_savings_amount < 0:
            errors["monthly_savings_amount"] = ValidationError("Cannot be negative.")
        if self.monthly_emergency_amount is not None and self.monthly_emergency_amount < 0:
            errors["monthly_emergency_amount"] = ValidationError("Cannot be negative.")

        # Date overlap. Cycles are a partition of time - two cycles may never
        # cover the same day, or a contribution dated in the overlap would be
        # ambiguous as to which cycle's target it counts toward.
        if self.start_date and self.duration_months:
            end = self.derive_end_date()
            clash = Cycle.objects.filter(
                start_date__lte=end,
                end_date__gte=self.start_date,
            )
            if self.pk:
                clash = clash.exclude(pk=self.pk)
            overlapping = clash.first()
            if overlapping:
                errors["start_date"] = ValidationError(
                    "These dates (%(start)s to %(end)s) overlap %(other)s, which runs "
                    "%(o_start)s to %(o_end)s. Cycles may not overlap.",
                    code="overlap",
                    params={
                        "start": self.start_date,
                        "end": end,
                        "other": str(overlapping),
                        "o_start": overlapping.start_date,
                        "o_end": overlapping.end_date,
                    },
                )

        if errors:
            raise ValidationError(errors)

    # ------------------------------------------------------------------
    # Derived read-only figures
    # ------------------------------------------------------------------

    @property
    def member_deposit_target(self):
        """One member's deposit (savings) target: monthly savings x duration."""
        return (self.monthly_savings_amount or Decimal("0")) * (self.duration_months or 0)

    @property
    def member_emergency_target(self):
        """One member's emergency obligation: monthly emergency x duration."""
        return (self.monthly_emergency_amount or Decimal("0")) * (self.duration_months or 0)

    @property
    def eligible_member_count(self):
        """
        Members counted towards the unit-wide targets.

        An UPCOMING cycle has no membership record yet, so it projects today's
        Active headcount. Once a cycle has been activated, its own membership
        record is used, so the targets of a past cycle never change when
        members later join or leave.
        """
        if self.status == self.Status.UPCOMING and self.activated_at is None:
            from apps.members.models import Member

            return Member.objects.filter(status=Member.Status.ACTIVE).count()

        return self.memberships.filter(left_on__isnull=True).count()

    @property
    def unit_deposit_target(self):
        """Unit-wide deposit target (emergency excluded): members x deposit target."""
        return self.member_deposit_target * self.eligible_member_count

    @property
    def unit_emergency_target(self):
        """Unit-wide emergency obligation, reported separately from deposits."""
        return self.member_emergency_target * self.eligible_member_count

    @property
    def is_open(self):
        """Can financial records still be written against this cycle?"""
        return self.status != self.Status.CLOSED

    @property
    def is_locked(self):
        return self.status == self.Status.CLOSED

    @property
    def payment_window_closes(self):
        """
        Manual 3.2 (v3.0): a month's contribution may be paid up to the 10th of
        the FOLLOWING month. So the last legitimate payment for a cycle lands *after*
        the cycle's own end_date. close() warns about this; nothing else should
        assume end_date is the last day money can arrive.
        """
        nxt = _add_months(self.end_date, 1)
        return date(nxt.year, nxt.month, PAYMENT_WINDOW_LAST_DAY)

    @property
    def has_financial_records(self):
        for relation in self.PROTECTED_RELATIONS:
            manager = getattr(self, relation, None)
            if manager is not None and manager.exists():
                return True
        return False

    def is_deletable(self):
        """
        Returns (bool, reason).

        A cycle is deletable ONLY if it is UPCOMING, has never been activated,
        and carries no financial records. Anything that has ever been the active
        cycle is part of FCU's financial history and is never hard-deleted -
        deleting it would punch a hole in the audit trail, which is precisely
        what an append-only audit log exists to prevent.
        """
        if self.status == self.Status.ACTIVE:
            return False, "The active cycle cannot be deleted. Close it first."
        if self.status == self.Status.CLOSED:
            return False, "A closed cycle is part of FCU's financial history and cannot be deleted."
        if self.activated_at is not None:
            return False, "This cycle has been active before and cannot be deleted."
        if self.has_financial_records:
            return False, "This cycle has financial records attached and cannot be deleted."
        return True, ""

    def out_of_range_payments(self):
        """
        Payments counting towards this cycle whose month lies outside the
        cycle's (possibly revised) dates. They need a person to review them.
        """
        from apps.contributions.services import cycle_payments

        first = self.start_date.replace(day=1)
        last = (self.end_date or self.derive_end_date()).replace(day=1)

        return cycle_payments(self).exclude(
            period_month__gte=first, period_month__lte=last
        )

    def pending_reversals(self):
        from apps.contributions.models import ContributionReversal

        return ContributionReversal.objects.filter(
            status=ContributionReversal.Status.PENDING,
            contribution__cycle=self,
        )

    def hard_blockers_for_close(self):
        """
        Things that must be settled before closing. Unlike the warnings below,
        these stop the close: closing locks the records, and a reversal that is
        still waiting for approval could never be decided cleanly afterwards.
        """
        blockers = []

        pending = self.pending_reversals().count()
        if pending:
            blockers.append(
                f"{pending} reversal request(s) are still pending approval. "
                f"Approve or reject them before closing."
            )

        return blockers

    def blocking_issues_for_close(self):
        """
        Reasons an Administrator should think twice before closing. Returned as a
        list of human-readable strings and shown on the close-confirmation screen.

        These are WARNINGS, not hard blocks - an Administrator may still have a
        legitimate reason to close (and Manual 4.7 gives the Executive Committee
        machinery for handling a defaulted loan after the fact). What is NOT
        acceptable is closing without being told.
        """
        issues = []
        today = timezone.localdate()

        if today <= self.end_date:
            issues.append(
                f"This cycle does not end until {self.end_date:%d %b %Y}."
            )
        elif today < self.payment_window_closes:
            issues.append(
                f"The Payment Window for the final month is still open until "
                f"{self.payment_window_closes:%d %b %Y} (Manual 3.2). Contributions "
                f"may still legitimately arrive."
            )

        from apps.members.services import members_failing_cycle_target

        flagged = self.out_of_range_payments().count()
        if flagged:
            issues.append(
                f"{flagged} payment(s) fall outside this cycle's dates and have "
                f"not been reviewed."
            )

        failing = members_failing_cycle_target(self)
        if failing:
            codes = ", ".join(m.member_code for m in failing)
            issues.append(
                f"{len(failing)} member(s) have not met this cycle's target and will "
                f"become Inactive for the next cycle: {codes}."
            )
        from apps.contributions.services import cycle_contribution_summary

        totals = cycle_contribution_summary(self)
        if totals["outstanding"] > 0:
            issues.append(
                f"Deposits outstanding: UGX {totals['outstanding']:,.0f} of "
                f"UGX {totals['expected']:,.0f}."
            )
        if totals["emergency_outstanding"] > 0:
            issues.append(
                f"Emergency contributions outstanding: "
                f"UGX {totals['emergency_outstanding']:,.0f}."
            )
        owed_loans = [
            loan for loan in self.loans.exclude(status="SETTLED") if loan.outstanding > 0
        ]
        if owed_loans:
            total = sum(loan.outstanding for loan in owed_loans)
            issues.append(
                f"{len(owed_loans)} loan(s) from this cycle are not settled "
                f"(UGX {total:,.0f} outstanding). Manual 4.5(d) expects every loan to "
                f"be repaid within the cycle; overdue ones are subject to recovery."
            )
        # Later phases will extend this with: unreconciled ledger entries.
        return issues



class CycleMembership(models.Model):
    """
    Who was eligible in a cycle, and from when until when.

    Targets must not drift as members come and go, so a cycle's unit-wide
    targets count the members recorded here, not today's headcount:

    * activating a cycle records every Active member at that moment;
    * a member who becomes Active during the cycle (a new joiner, or someone who
      cleared old arrears) is added and owes the full cycle amount;
    * a member who withdraws gets `left_on` set and stops counting.

    Closed cycles never change after closing, so their targets stay put.
    """

    cycle = models.ForeignKey(Cycle, on_delete=models.PROTECT, related_name="memberships")
    member = models.ForeignKey(
        "members.Member", on_delete=models.PROTECT, related_name="cycle_memberships"
    )
    joined_on = models.DateField()
    left_on = models.DateField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["member__member_code"]
        constraints = [
            models.UniqueConstraint(fields=["cycle", "member"], name="one_membership_per_member_per_cycle"),
        ]

    def __str__(self):
        return f"{self.member.member_code} in {self.cycle}"
