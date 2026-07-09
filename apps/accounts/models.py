from django.contrib.auth.models import AbstractUser
from django.db import models


class User(AbstractUser):
    """
    Custom auth user (AUTH_USER_MODEL). Every User corresponds to exactly one
    Member (closed-membership system, FR-2) and carries the RBAC role (FR-3).
    `username` is set to the member_code at activation time, so members can log
    in with the same ID that identifies them everywhere else in the system.
    """

    class Role(models.TextChoices):
        ADMINISTRATOR = "ADMINISTRATOR", "Administrator"
        TREASURER = "TREASURER", "Treasurer"
        CHAIRPERSON = "CHAIRPERSON", "Chairperson"
        SECRETARY = "SECRETARY", "Secretary"
        MEMBER = "MEMBER", "Member"

    member = models.OneToOneField(
        "members.Member",
        on_delete=models.PROTECT,  # never allow a Member to be deleted out from under an active login
        related_name="user_account",
        null=True,
        blank=True,
        help_text=(
            "Null only for a technical break-glass superuser created via createsuperuser "
            "(e.g. initial deployment bootstrapping). Every real member-facing account "
            "(Member/Treasurer/Chairperson/Secretary/Administrator) must have one."
        ),
    )
    role = models.CharField(max_length=20, choices=Role.choices, default=Role.MEMBER)

    def __str__(self):
        return f"{self.member.member_code} ({self.get_role_display()})"

    # --- convenience checks used throughout templates/views for RBAC (FR-3) ---
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
        """Treasurer + Administrator can record contributions/loans/repayments/expenses (FR-13/FR-14)."""
        return self.role in (self.Role.TREASURER, self.Role.ADMINISTRATOR)

    @property
    def has_reports_access(self):
        """Everyone except a plain Member can view the cross-member reports (FR-13/FR-14/brief)."""
        return self.role != self.Role.MEMBER
