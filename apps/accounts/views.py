from django.contrib import messages
from django.contrib.auth import login as auth_login
from django.shortcuts import redirect
from django.views.generic import FormView, ListView, View

from apps.auditlog.models import AuditLog
from apps.auditlog.utils import log_action

from .forms import AccountActivationForm
from .models import User
from .permissions import RoleRequiredMixin


class ActivateAccountView(FormView):
    """
    FR-2.2 — Closed-membership self-service account activation.

    Only existing FCU members can activate an account.
    """

    template_name = "accounts/activate_account.html"
    form_class = AccountActivationForm

    def form_valid(self, form):
        member = form.cleaned_data["member"]
        password = form.cleaned_data["password1"]

        user = User(
            username=member.member_code,
            email=member.email,
            first_name=member.full_name.split(" ")[0],
            member=member,
        )

        # Designated administrators automatically receive full system access.
        if member.is_designated_admin:
            user.role = User.Role.ADMINISTRATOR
            user.is_staff = True
            user.is_superuser = True
        else:
            user.role = User.Role.MEMBER

        user.set_password(password)
        user.save()

        log_action(
            AuditLog.Action.ACCOUNT_ACTIVATED,
            instance=user,
            new_value={
                "member_code": member.member_code,
                "role": user.role,
            },
            actor=user,
        )

        auth_login(self.request, user)

        messages.success(
            self.request,
            f"Welcome, {member.full_name.split()[0]}! Your account has been activated successfully.",
        )

        return redirect("core:dashboard")


class UserManagementView(RoleRequiredMixin, ListView):
    """
    Administrator-only user management.
    """

    allowed_roles = (User.Role.ADMINISTRATOR,)

    model = User
    template_name = "accounts/user_management.html"
    context_object_name = "users"

    queryset = (
        User.objects.select_related("member")
        .order_by("member__member_code")
    )


class ChangeUserRoleView(RoleRequiredMixin, View):
    """
    Administrator-only role management.

    Business Rules

    • Administrator role automatically grants full system access.
    • Designated administrators cannot be demoted.
    • At least one active Administrator must always exist.
    • All successful role changes are audit logged.
    """

    allowed_roles = (User.Role.ADMINISTRATOR,)

    def post(self, request, pk):

        target_user = User.objects.select_related("member").get(pk=pk)

        old_role = target_user.role
        new_role = request.POST.get("role")

        # ---------------------------------------------------------
        # Validate selected role
        # ---------------------------------------------------------

        if new_role not in User.Role.values:
            messages.error(
                request,
                "Invalid role selected.",
            )
            return redirect("accounts:user_management")

        if new_role == old_role:
            messages.info(
                request,
                "No changes were made.",
            )
            return redirect("accounts:user_management")

        # ---------------------------------------------------------
        # Protect designated system administrators
        # ---------------------------------------------------------

        if (
            target_user.member
            and target_user.member.is_designated_admin
            and new_role != User.Role.ADMINISTRATOR
        ):
            messages.error(
                request,
                (
                    "This member is a designated system administrator "
                    "and cannot be assigned another role."
                ),
            )
            return redirect("accounts:user_management")

        # ---------------------------------------------------------
        # Prevent removing the last active Administrator
        # ---------------------------------------------------------

        if (
            old_role == User.Role.ADMINISTRATOR
            and new_role != User.Role.ADMINISTRATOR
        ):

            administrator_count = User.objects.filter(
                role=User.Role.ADMINISTRATOR,
                is_active=True,
            ).count()

            if administrator_count <= 1:
                messages.error(
                    request,
                    (
                        "Operation cancelled. "
                        "At least one active Administrator must remain "
                        "in the system."
                    ),
                )
                return redirect("accounts:user_management")

        # ---------------------------------------------------------
        # Apply the role change
        # ---------------------------------------------------------

        target_user.role = new_role

        target_user.is_staff = (
            new_role == User.Role.ADMINISTRATOR
        )

        target_user.is_superuser = (
            new_role == User.Role.ADMINISTRATOR
        )

        target_user.save(
            update_fields=[
                "role",
                "is_staff",
                "is_superuser",
            ]
        )

        # ---------------------------------------------------------
        # Audit log
        # ---------------------------------------------------------

        log_action(
            AuditLog.Action.ROLE_CHANGE,
            instance=target_user,
            old_value={
                "role": old_role,
            },
            new_value={
                "role": new_role,
            },
            actor=request.user,
        )

        # ---------------------------------------------------------
        # Success message
        # ---------------------------------------------------------

        if target_user.member:
            display_name = (
                f"{target_user.member.full_name} "
                f"({target_user.member.member_code})"
            )
        else:
            display_name = target_user.username

        messages.success(
            request,
            (
                f"{display_name} is now assigned the role "
                f"{target_user.get_role_display()}."
            ),
        )

        return redirect("accounts:user_management")