import re

from django.db import models


class Member(models.Model):
    """
    The permanent identity record for an FCU member (SRS §7 / §9, FR-1).

    member_code (e.g. FCU005) is the permanent business identifier used
    throughout the system. It is never edited or reused.
    """

    class Status(models.TextChoices):
        ACTIVE = "ACTIVE", "Active"
        INACTIVE = "INACTIVE", "Inactive"
        WITHDRAWN = "WITHDRAWN", "Withdrawn"

    class InactiveReason(models.TextChoices):
        CONTRIBUTION_ARREARS = (
            "CONTRIBUTION_ARREARS",
            "Contribution Arrears",
        )

    member_code = models.CharField(
        max_length=10,
        unique=True,
        editable=False,
        db_index=True,
        help_text="Permanent, system-generated identifier. Never edited or reused (FR-1.2).",
    )

    full_name = models.CharField(max_length=150)

    email = models.EmailField(
        unique=True,
        null=True,
        blank=True,
        help_text=(
            "Leave blank (NULL) until the member provides a real email. "
            "Members without an email cannot activate an online account "
            "(FR-2.2)."
        ),
    )

    phone = models.CharField(max_length=20, blank=True)

    national_id_number = models.CharField(
        "NIN",
        max_length=30,
        blank=True,
        help_text="National Identification Number recorded on the Loan Application Form.",
    )

    joined_date = models.DateField()

    status = models.CharField(
        max_length=10,
        choices=Status.choices,
        default=Status.ACTIVE,
        editable=False,
        db_index=True,
        help_text=(
            "ACTIVE and INACTIVE are maintained automatically by the "
            "Member Status Engine. WITHDRAWN is a permanent administrative "
            "status applied when a member formally exits FCU."
        ),
    )

    inactive_reason = models.CharField(
        max_length=40,
        choices=InactiveReason.choices,
        blank=True,
        editable=False,
        help_text=(
            "Reason the member is currently inactive. "
            "Automatically maintained by the Member Status Engine."
        ),
    )

    withdrawn_date = models.DateField(
        null=True,
        blank=True,
        editable=False,
        help_text=(
            "Effective date the member officially withdrew from FCU."
        ),
    )

    withdrawal_reason = models.TextField(
        blank=True,
        editable=False,
        help_text=(
            "Reason recorded when a member formally withdraws from FCU."
        ),
    )

    is_designated_admin = models.BooleanField(
        default=False,
        help_text=(
            "Seed-time flag only. When this member activates an account, "
            "the Administrator role is automatically assigned."
        ),
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["member_code"]

    def __str__(self):
        return f"{self.member_code} — {self.full_name}"

    def save(self, *args, **kwargs):
        if not self.member_code:
            self.member_code = self.generate_next_code()
        super().save(*args, **kwargs)

    @classmethod
    def generate_next_code(cls):
        """
        Return the next available FCU member code.

        Codes are zero-padded to three digits (FCU001, FCU002, ...), but
        naturally expand beyond FCU999 if membership grows.
        """

        max_num = 0

        for code in cls.objects.values_list("member_code", flat=True):
            match = re.match(r"^FCU(\d+)$", code)
            if match:
                max_num = max(max_num, int(match.group(1)))

        return f"FCU{max_num + 1:03d}"