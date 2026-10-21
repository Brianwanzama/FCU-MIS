"""
Loan applications (Manual v3.0, section 4).

A member downloads the official Loan Application Form and the Member Declaration
and Commitment Agreement, fills and signs them, and uploads them here. The system
checks the member against Manual 4.3 before the application is accepted, and the
Administrator or Chairperson then approves or rejects it.

Uploaded files are private: they are stored outside MEDIA_ROOT and are only ever
handed out by views that check who is asking.
"""

from django.conf import settings
from django.db import models

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
