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

    extra_roles = models.JSONField(
        default=list,
        blank=True,
        help_text=(
            "Additional roles held on top of the main role (for example an "
            "Administrator who also acts as Treasurer). Permissions are the "
            "union of every role held."
        ),
    )

    must_change_password = models.BooleanField(
        default=False,
        help_text=(
            "When set, the user is taken to the change-password page after "
            "signing in and cannot use the system until a new password is set."
        ),
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
    def roles(self):
        """Every role held: the main role plus any additional roles."""
        held = {self.role}
        held.update(r for r in (self.extra_roles or []) if r in self.Role.values)
        return held

    def has_role(self, *roles):
        """True if the user holds any of the given roles (Administrator holds all)."""
        held = self.roles
        return self.Role.ADMINISTRATOR in held or any(r in held for r in roles)

    @property
    def is_administrator(self):
        return self.Role.ADMINISTRATOR in self.roles

    @property
    def is_treasurer(self):
        return self.Role.TREASURER in self.roles

    @property
    def is_secretary(self):
        return self.Role.SECRETARY in self.roles

    @property
    def is_chairperson(self):
        return self.Role.CHAIRPERSON in self.roles

    @property
    def is_plain_member(self):
        """Holds no office: only the Member role."""
        return self.roles == {self.Role.MEMBER}

    @property
    def roles_display(self):
        labels = dict(self.Role.choices)
        ordered = [self.role] + sorted(r for r in self.roles if r != self.role)
        return ", ".join(labels[r] for r in ordered)

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
        return self.has_role(self.Role.ADMINISTRATOR, self.Role.TREASURER)

    @property
    def has_financial_view_access(self):
        """View every member's financial records (contribution register)."""
        return self.has_role(self.Role.ADMINISTRATOR, self.Role.TREASURER, self.Role.CHAIRPERSON)

    def can_view_member_finances(self, member):
        """
        A member sees only their own financial records. The Administrator,
        Treasurer and Chairperson can see everyone's.
        """
        if self.has_financial_view_access:
            return True

        return self.member_id is not None and self.member_id == member.pk

    @property
    def can_edit_contributions(self):
        """Correct an existing contribution (reason required, fully audited)."""
        return self.has_financial_view_access

    @property
    def can_request_reversal(self):
        """Ask for a contribution to be reversed. Needs approval to take effect."""
        return self.has_financial_view_access

    @property
    def can_delete_records(self):
        """Delete a wrongly entered record (kept in the audit log with the reason)."""
        return self.has_role(self.Role.TREASURER, self.Role.CHAIRPERSON)

    @property
    def can_record_offsystem_loans(self):
        """Add a loan that was made outside the system."""
        return self.has_role(self.Role.TREASURER, self.Role.CHAIRPERSON)

    @property
    def can_impersonate(self):
        """Sign in as another member to see exactly what they see."""
        return self.role == self.Role.ADMINISTRATOR or self.is_superuser

    @property
    def can_approve_reversal(self):
        """Approve or reject a reversal request. The Treasurer cannot."""
        return self.has_role(self.Role.ADMINISTRATOR, self.Role.CHAIRPERSON)

    @property
    def can_approve_loans(self):
        """Approve or reject loan applications (Administrator or Chairperson)."""
        return self.has_chairperson_access

    @property
    def can_verify_undertakings(self):
        """Check a member's signed Declaration and Commitment Agreement."""
        return self.has_role(self.Role.ADMINISTRATOR, self.Role.TREASURER, self.Role.CHAIRPERSON, self.Role.SECRETARY)

    @property
    def has_secretariat_access(self):
        """
        Member administration, correspondence,
        meeting records and document management.
        """
        return self.has_role(self.Role.ADMINISTRATOR, self.Role.SECRETARY)

    @property
    def has_chairperson_access(self):
        """
        Chairperson approvals and executive actions.
        """
        return self.has_role(self.Role.ADMINISTRATOR, self.Role.CHAIRPERSON)

    @property
    def has_reports_access(self):
        """
        Access organisation-wide reports.
        """
        return self.has_role(self.Role.ADMINISTRATOR, self.Role.TREASURER, self.Role.SECRETARY, self.Role.CHAIRPERSON)