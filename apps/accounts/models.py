from django.contrib.auth.models import AbstractUser
from django.db import models


class User(AbstractUser):
    """
    Custom authentication user (AUTH_USER_MODEL).

    Every User corresponds to exactly one FCU Member and carries one
    application role.

    Design Decision
    ----------------
    FCU uses a single primary role.

    Administrator is the highest privilege level and automatically inherits
    all Treasurer, Secretary, Chairperson and Member capabilities through the
    application's permission layer.

    This keeps the database simple while allowing FCU001 (Administrator) to
    perform every function in the system.
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
            "Leave blank only for a technical deployment superuser. "
            "Every FCU member account must be linked to one Member record."
        ),
    )

    role = models.CharField(
        max_length=20,
        choices=Role.choices,
        default=Role.MEMBER,
    )

    def __str__(self):
        if self.member:
            return (
                f"{self.member.member_code} - "
                f"{self.member.full_name} "
                f"({self.get_role_display()})"
            )

        return f"{self.username} ({self.get_role_display()})"

    # ==========================================================
    # Basic Role Helpers
    # ==========================================================

    @property
    def is_administrator(self):
        return self.role == self.Role.ADMINISTRATOR

    @property
    def is_treasurer(self):
        return self.role == self.Role.TREASURER

    @property
    def is_secretary(self):
        return self.role == self.Role.SECRETARY

    @property
    def is_chairperson(self):
        return self.role == self.Role.CHAIRPERSON

    @property
    def is_plain_member(self):
        return self.role == self.Role.MEMBER

    # ==========================================================
    # Permission Helpers
    #
    # Administrator automatically inherits all permissions.
    # The rest of the application should use these helpers
    # instead of comparing roles directly.
    # ==========================================================

    @property
    def has_full_access(self):
        """
        System Administrator.

        Full unrestricted access to every module.
        """
        return self.is_administrator

    @property
    def has_financial_write_access(self):
        """
        Record contributions, loans, repayments,
        expenses and unit trust transactions.
        """
        return self.role in (
            self.Role.ADMINISTRATOR,
            self.Role.TREASURER,
        )

    @property
    def has_secretariat_access(self):
        """
        Member administration, correspondence,
        meeting records and document management.
        """
        return self.role in (
            self.Role.ADMINISTRATOR,
            self.Role.SECRETARY,
        )

    @property
    def has_chairperson_access(self):
        """
        Chairperson approvals and executive actions.
        """
        return self.role in (
            self.Role.ADMINISTRATOR,
            self.Role.CHAIRPERSON,
        )

    @property
    def has_reports_access(self):
        """
        Access organisation-wide reports.
        """
        return self.role in (
            self.Role.ADMINISTRATOR,
            self.Role.TREASURER,
            self.Role.SECRETARY,
            self.Role.CHAIRPERSON,
        )