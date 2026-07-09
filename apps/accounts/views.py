from django.contrib import messages
from django.contrib.auth import login as auth_login
from django.shortcuts import redirect, render
from django.views.generic import FormView, ListView, View

from apps.auditlog.models import AuditLog
from apps.auditlog.utils import log_action

from .forms import AccountActivationForm
from .models import User
from .permissions import RoleRequiredMixin


class ActivateAccountView(FormView):
    """FR-2.2 — closed-membership self-service activation. No public registration
    exists anywhere else in the system; this is the only way a login is created."""

    template_name = "accounts/activate_account.html"
    form_class = AccountActivationForm

    def form_valid(self, form):
        member = form.cleaned_data["member"]
        password = form.cleaned_data["password1"]

        user = User(
            username=member.member_code,
            email=member.email,
            first_name=member.full_name.split(" ")[0],
        )
        # A member flagged at seed time as a designated admin (FCU001, FCU004 per the
        # brief) is automatically granted the Administrator role the moment they
        # activate — nobody has to remember to promote them by hand after the fact.
        # The brief states Administrators have "unrestricted access to every part of
        # the system," so this also grants Django Admin (is_staff/is_superuser) access,
        # since several Admin-only screens (Member Management, Document Templates,
        # Audit Log) are currently implemented via the built-in Django Admin. If a
        # dedicated Administrator UI later replaces Django Admin for these, this
        # grant can be narrowed to is_staff only.
        if member.is_designated_admin:
            user.role = User.Role.ADMINISTRATOR
            user.is_staff = True
            user.is_superuser = True
        else:
            user.role = User.Role.MEMBER
        user.member = member
        user.set_password(password)
        user.save()

        log_action(
            AuditLog.Action.ACCOUNT_ACTIVATED,
            instance=user,
            new_value={"member_code": member.member_code, "role": user.role},
            actor=user,
        )

        auth_login(self.request, user)
        messages.success(self.request, f"Welcome, {member.full_name.split(' ')[0]}! Your account is now active.")
        return redirect("core:dashboard")


class UserManagementView(RoleRequiredMixin, ListView):
    """FR-14 'Manage Users / Assign Roles' — Administrator-only."""

    allowed_roles = (User.Role.ADMINISTRATOR,)
    model = User
    template_name = "accounts/user_management.html"
    context_object_name = "users"
    queryset = User.objects.select_related("member").order_by("member__member_code")


class ChangeUserRoleView(RoleRequiredMixin, View):
    """FR-3.2 — only an Administrator may change a role, and every change is audit-logged."""

    allowed_roles = (User.Role.ADMINISTRATOR,)

    def post(self, request, pk):
        target_user = User.objects.select_related("member").get(pk=pk)
        old_role = target_user.role
        new_role = request.POST.get("role")

        if new_role in User.Role.values and new_role != old_role:
            target_user.role = new_role
            # Keep Django Admin access in sync with the Administrator role (see the
            # matching comment in ActivateAccountView) rather than letting the two
            # ever drift apart.
            target_user.is_staff = new_role == User.Role.ADMINISTRATOR
            target_user.is_superuser = new_role == User.Role.ADMINISTRATOR
            target_user.save(update_fields=["role", "is_staff", "is_superuser"])
            log_action(
                AuditLog.Action.ROLE_CHANGE,
                instance=target_user,
                old_value={"role": old_role},
                new_value={"role": new_role},
            )
            messages.success(
                request,
                f"{target_user.member.member_code}'s role changed from "
                f"{old_role.title()} to {new_role.title()}.",
            )
        return redirect("accounts:user_management")
