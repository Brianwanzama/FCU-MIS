"""
Loan applications (Manual v3.0, section 4).

A member downloads the official Loan Application Form and the Member Declaration
and Commitment Agreement, fills and signs them, and uploads them here. The system
checks the member against Manual 4.3 before the application is accepted, and the
Administrator or Chairperson then approves or rejects it.

Uploaded files are private: they are stored outside MEDIA_ROOT and are only ever
handed out by views that check who is asking.
"""

from decimal import Decimal

from django.conf import settings
from django.db import models
from django.utils import timezone

from .storage import get_private_storage


def undertaking_path(instance, filename):
    return f"undertakings/{instance.member.member_code}/{filename}"


def application_form_path(instance, filename):
    return f"loan_applications/{instance.member.member_code}/{filename}"


class SignedUndertaking(models.Model):
    """
    A member's signed Member Declaration and Commitment Agreement (Manual 9).
    It is also the member's consent to loan recovery from their contributions
    (Manual 4.6), so a loan cannot be approved without a verified one.
    """

    class Status(models.TextChoices):
        PENDING = "PENDING", "Awaiting verification"
        VERIFIED = "VERIFIED", "Verified"
        REJECTED = "REJECTED", "Rejected"

    member = models.ForeignKey(
        "members.Member", on_delete=models.PROTECT, related_name="undertakings"
    )
    file = models.FileField(upload_to=undertaking_path, storage=get_private_storage)
    original_name = models.CharField(max_length=255)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+"
    )
    uploaded_at = models.DateTimeField(auto_now_add=True)
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    review_note = models.TextField(blank=True)

    class Meta:
        ordering = ["-uploaded_at"]

    def __str__(self):
        return f"Undertaking of {self.member.member_code} ({self.get_status_display()})"


class LoanApplication(models.Model):
    class Status(models.TextChoices):
        SUBMITTED = "SUBMITTED", "Submitted"
        APPROVED = "APPROVED", "Approved"
        DISBURSED = "DISBURSED", "Disbursed"
        REJECTED = "REJECTED", "Rejected"
        WITHDRAWN = "WITHDRAWN", "Withdrawn"

    member = models.ForeignKey(
        "members.Member", on_delete=models.PROTECT, related_name="loan_applications"
    )
    cycle = models.ForeignKey(
        "cycles.Cycle", on_delete=models.PROTECT, related_name="loan_applications"
    )
    amount_requested = models.DecimalField(max_digits=12, decimal_places=2)
    period_months = models.PositiveSmallIntegerField()

    application_form = models.FileField(upload_to=application_form_path, storage=get_private_storage)
    application_form_name = models.CharField(max_length=255)
    undertaking = models.ForeignKey(
        SignedUndertaking, on_delete=models.PROTECT, related_name="applications"
    )

    status = models.CharField(max_length=10, choices=Status.choices, default=Status.SUBMITTED)
    submitted_at = models.DateTimeField(auto_now_add=True)
    eligibility_snapshot = models.JSONField(
        default=dict,
        help_text="The Manual 4.3 checks exactly as they stood when the application was submitted.",
    )

    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    decided_at = models.DateTimeField(null=True, blank=True)
    decision_note = models.TextField(blank=True)

    amount_approved = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    interest_rate_percent = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    installment_amount = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    approved_schedule = models.JSONField(default=list, blank=True)

    class Meta:
        ordering = ["-submitted_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["member"],
                condition=models.Q(status="SUBMITTED"),
                name="one_open_loan_application_per_member",
            ),
            models.CheckConstraint(
                condition=models.Q(amount_requested__gt=0),
                name="loan_application_amount_positive",
            ),
        ]

    def __str__(self):
        return f"{self.reference} - {self.member.member_code} - {self.get_status_display()}"

    @property
    def reference(self):
        return f"LN-{self.pk:05d}" if self.pk else "LN-new"



class Loan(models.Model):
    """
    A loan that has been paid out. Created only by disbursing an approved
    application, so every loan traces back to the signed forms and the decision.
    """

    class Status(models.TextChoices):
        ACTIVE = "ACTIVE", "Active"
        SETTLED = "SETTLED", "Settled"
        DEFAULTED = "DEFAULTED", "Defaulted"

    application = models.OneToOneField(
        LoanApplication, on_delete=models.PROTECT, related_name="loan", null=True, blank=True,
        help_text="Empty for a loan that was made outside the system and added by an officer.",
    )
    is_offsystem = models.BooleanField(
        default=False,
        help_text="A loan managed off-system (before the system, or outside its application process).",
    )
    offsystem_note = models.TextField(blank=True)
    member = models.ForeignKey("members.Member", on_delete=models.PROTECT, related_name="loans")
    cycle = models.ForeignKey("cycles.Cycle", on_delete=models.PROTECT, related_name="loans")
    principal = models.DecimalField(max_digits=12, decimal_places=2)
    interest_rate_percent = models.DecimalField(max_digits=5, decimal_places=2)
    period_months = models.PositiveSmallIntegerField()

    disbursed_on = models.DateField()
    disbursed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+"
    )
    disbursement_method = models.CharField(max_length=20)
    disbursement_reference = models.CharField(max_length=100, blank=True)

    status = models.CharField(max_length=10, choices=Status.choices, default=Status.ACTIVE)
    settled_on = models.DateField(null=True, blank=True)
    defaulted_on = models.DateField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-disbursed_on", "-id"]

    def __str__(self):
        return f"{self.reference} - {self.member.member_code}"

    @property
    def reference(self):
        if self.application_id:
            return self.application.reference
        return f"OFF-{self.pk:05d}" if self.pk else "OFF-new"

    @property
    def disbursement_method_label(self):
        try:
            return LoanRepayment.Method(self.disbursement_method).label
        except ValueError:
            return self.disbursement_method

    @property
    def total_due(self):
        return sum((i.amount_due for i in self.installments.all()), Decimal("0"))

    @property
    def total_paid(self):
        return sum(
            (r.amount for r in self.repayments.all() if r.reversed_at is None), Decimal("0")
        )

    @property
    def outstanding(self):
        return max(self.total_due - self.total_paid, Decimal("0"))

    @property
    def final_due_date(self):
        last = self.installments.order_by("-number").first()
        return last.due_date if last else None

    def has_overdue_installment(self, today=None):
        today = today or timezone.localdate()
        return any(i.due_date < today and i.remaining > 0 for i in self.installments.all())

    def display_status(self, today=None):
        """Current, Overdue, Defaulted or Settled: what a person should be told."""

        if self.status == self.Status.SETTLED:
            return "Settled"
        if self.status == self.Status.DEFAULTED:
            return "Defaulted"
        return "Overdue" if self.has_overdue_installment(today) else "Current"


class LoanInstallment(models.Model):
    loan = models.ForeignKey(Loan, on_delete=models.PROTECT, related_name="installments")
    number = models.PositiveSmallIntegerField()
    due_date = models.DateField()
    principal_due = models.DecimalField(max_digits=12, decimal_places=2)
    interest_due = models.DecimalField(max_digits=12, decimal_places=2)
    amount_due = models.DecimalField(max_digits=12, decimal_places=2)
    amount_paid = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0"))

    class Meta:
        ordering = ["number"]
        constraints = [
            models.UniqueConstraint(fields=["loan", "number"], name="one_installment_per_number"),
        ]

    @property
    def remaining(self):
        return max(self.amount_due - self.amount_paid, Decimal("0"))


class LoanRepayment(models.Model):
    """
    Money that reduces a loan: a payment received, or a recovery from the
    member's savings. A recovery is a separate transaction linked to the loan;
    it never edits or removes the contributions it is taken from, so statements
    show both the original deposits and the offset.
    """

    class Kind(models.TextChoices):
        PAYMENT = "PAYMENT", "Repayment"
        SAVINGS_OFFSET = "SAVINGS_OFFSET", "Recovered from savings"

    class Method(models.TextChoices):
        CASH = "CASH", "Cash"
        BANK = "BANK", "Bank Transfer"
        MOBILE_MONEY = "MOBILE_MONEY", "Mobile Money"

    loan = models.ForeignKey(Loan, on_delete=models.PROTECT, related_name="repayments")
    kind = models.CharField(max_length=20, choices=Kind.choices, default=Kind.PAYMENT)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    payment_date = models.DateField()
    payment_method = models.CharField(max_length=20, choices=Method.choices, blank=True)
    reference = models.CharField(max_length=100, blank=True)
    cycle = models.ForeignKey(
        "cycles.Cycle", on_delete=models.PROTECT, related_name="loan_repayments",
        help_text="The cycle in which this was recorded. A savings offset counts against this cycle's contribution position.",
    )
    recorded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+",
        help_text="Empty for automatic recoveries run by the system.",
    )
    recovery_date = models.DateField(
        null=True, blank=True,
        help_text="For recoveries: the day it ran. At most one live recovery per loan per day.",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    reversed_at = models.DateTimeField(null=True, blank=True)
    reversed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    reversal_reason = models.TextField(blank=True)

    class Meta:
        ordering = ["payment_date", "id"]
        constraints = [
            models.CheckConstraint(condition=models.Q(amount__gt=0), name="loan_repayment_positive"),
            models.UniqueConstraint(
                fields=["loan", "recovery_date"],
                condition=models.Q(kind="SAVINGS_OFFSET", reversed_at__isnull=True),
                name="one_live_recovery_per_loan_per_day",
            ),
        ]

    def __str__(self):
        return f"{self.loan.reference} {self.get_kind_display()} {self.amount}"



class RecoveryRun(models.Model):
    """
    One row per day the automatic recovery check has run. The unique date makes
    sure the daily check happens once however many servers or visits there are.
    """

    run_date = models.DateField(unique=True)
    started_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    loans_checked = models.PositiveIntegerField(default=0)
    total_recovered = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0"))
    error = models.TextField(blank=True)

    class Meta:
        ordering = ["-run_date"]

    def __str__(self):
        return f"Recovery check {self.run_date}"
