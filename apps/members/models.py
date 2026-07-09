import re

from django.db import models


class Member(models.Model):
    """
    The core, permanent identity record for an FCU member (SRS §7/§9, FR-1).
    member_code (e.g. "FCU005") is the human-facing business key used everywhere
    in the UI and on every other table's foreign key — never the member's name.
    """

    class Status(models.TextChoices):
        ACTIVE = "ACTIVE", "Active"
        INACTIVE = "INACTIVE", "Inactive"

    member_code = models.CharField(
        max_length=10,
        unique=True,
        editable=False,
        db_index=True,
        help_text="Permanent, system-generated. Never reused, never edited (FR-1.2).",
    )
    full_name = models.CharField(max_length=150)
    email = models.EmailField(
        unique=True,
        null=True,
        blank=True,
        help_text=(
            "Blank (NULL) for a member whose email isn't yet known — never a fake "
            "placeholder address. A blank email blocks that member's account "
            "activation (FR-2.2) until an Administrator adds the real one."
        ),
    )
    phone = models.CharField(max_length=20, blank=True)
    national_id_number = models.CharField(
        "NIN", max_length=30, blank=True, help_text="National ID Number, as captured on the Loan Application Form."
    )
    joined_date = models.DateField()

    status = models.CharField(
        max_length=10,
        choices=Status.choices,
        default=Status.ACTIVE,
        editable=False,
        help_text=(
            "Computed automatically by the member-status engine (FR-6 / Manual §2.1, §3.5). "
            "There is deliberately no form or admin field to set this directly — "
            "see apps.cycles for the recalculation logic (added in that roadmap phase)."
        ),
    )

    is_designated_admin = models.BooleanField(
        default=False,
        help_text=(
            "Seed-time flag only. When a member with this flag set activates their account, "
            "apps.accounts automatically assigns the Administrator role (per FCU001/FCU004 in the brief). "
            "Not exposed in any regular member-management form."
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
        Finds the highest existing FCU### number and returns the next one,
        zero-padded to at least 3 digits (grows naturally past FCU999 without truncation).

        Note: this does a table scan rather than using a DB sequence. That's a deliberate,
        acceptable trade-off at FCU's current and Vision-2028 scale (Admin-created members
        only, no concurrent public sign-up, target of ~30 members by 2028) — flagged here
        so it's revisited if that assumption ever changes.
        """
        max_num = 0
        for code in cls.objects.values_list("member_code", flat=True):
            match = re.match(r"^FCU(\d+)$", code)
            if match:
                max_num = max(max_num, int(match.group(1)))
        return f"FCU{max_num + 1:03d}"
