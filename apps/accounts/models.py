from django.contrib.auth.models import AbstractUser
from django.db import models


class User(AbstractUser):
    """
    Custom authentication user (AUTH_USER_MODEL).

    Every User corresponds to exactly one FCU Member and carries a role
    for Role-Based Access Control (RBAC).

    Login:
        - Members authenticate using their email address.
        - The Member Code (e.g. FCU001) remains the permanent financial
          identifier used throughout the system for contributions, loans,
          statements and reporting.

    A technical superuser created during deployment may exist without an
    associated Member record.
    """

    class Role(models.TextChoices):
        ADMINISTRATOR = "ADMINISTRATOR", "Administrator"
        TREASURER = "TREASURER", "Treasurer"
        CHAIRPERSON = "CHAIRPERSON", "Chairperson"
        SECRETARY = "SECRETARY", "Secretary"
        MEMBER = "MEMBER", "Member"

    member = models.OneToOneField(
        "members.Member",
        on_delete=models.PROTECT,
        related_name="user_account",
        null=True,
        blank=True,
        help_text=(
            "Leave blank only for a technical superuser created during "
            "initial deployment. Every real FCU member account must be "
            "linked to exactly one Member record."
        ),
    )

    role = models.CharField(
        max_length=20,
        choices=Role.choices,
        default=Role.MEMBER,
    )

    def __str__(self):
        """
        Safe string representation.

        Handles deployment superusers that are intentionally created
        without a linked Member record.
        """
        if self.member:
            return (
                f"{self.member.member_code} - "
                f"{self.member.full_name} "
                f"({self.get_role_display()})"
            )

        return f"{self.username} ({self.get_role_display()})"

    # ------------------------------------------------------------------
    # Role convenience properties
    # ------------------------------------------------------------------

    @property
    def is_administrator(self):
        return self.role == self.Role.ADMINISTRATOR

    @property
    def is_treasurer(self):
        return self.role == self.Role.TREASURER

    @property
    def is_chairperson(self):
        return self.role == self.Role.CHAIRPERSON

    @property
    def is_secretary(self):
        return self.role == self.Role.SECRETARY

    @property
    def is_plain_member(self):
        return self.role == self.Role.MEMBER

    @property
    def has_financial_write_access(self):
        """
        Treasurer and Administrator can record contributions,
        loans, repayments and expenses.
        """
        return self.role in (
            self.Role.TREASURER,
            self.Role.ADMINISTRATOR,
        )

    @property
    def has_reports_access(self):
        """
        Treasurer, Administrator, Secretary and Chairperson
        can access organisation-wide reports.
        """
        return self.role != self.Role.MEMBER