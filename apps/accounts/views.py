import logging

import secrets

from django.contrib import messages
from django.contrib.auth import login as auth_login
from django.contrib.auth import views as auth_views
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import HttpResponseRedirect
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse_lazy
from django.views.decorators.http import require_POST
from django.views.generic import FormView, ListView, View

from apps.auditlog.models import AuditLog
from apps.auditlog.utils import log_action

from .forms import AccountActivationForm
from .models import User
from .permissions import RoleRequiredMixin

logger = logging.getLogger(__name__)


class ActivateAccountView(FormView):
    """
    FR-2.2, Closed-membership self-service account activation.

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
        old_extra = sorted(target_user.extra_roles or [])
        new_role = request.POST.get("role")
        new_extra = sorted(
            r for r in set(request.POST.getlist("extra_roles"))
            if r in User.Role.values and r != new_role
        )

        # ---------------------------------------------------------
        # Validate selected role
        # ---------------------------------------------------------

        if new_role not in User.Role.values:
            messages.error(
                request,
                "Invalid role selected.",
            )
            return redirect("accounts:user_management")

        if new_role == old_role and new_extra == old_extra:
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
            and User.Role.ADMINISTRATOR not in {new_role, *new_extra}
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
            User.Role.ADMINISTRATOR in target_user.roles
            and User.Role.ADMINISTRATOR not in {new_role, *new_extra}
        ):

            administrator_count = sum(
                1 for u in User.objects.filter(is_active=True)
                if User.Role.ADMINISTRATOR in u.roles
            )

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
        target_user.extra_roles = new_extra

        is_admin = User.Role.ADMINISTRATOR in target_user.roles

        target_user.is_staff = is_admin
        target_user.is_superuser = is_admin

        target_user.save(
            update_fields=[
                "role",
                "extra_roles",
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
                "extra_roles": old_extra,
            },
            new_value={
                "role": new_role,
                "extra_roles": new_extra,
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
                f"{display_name} now holds: "
                f"{target_user.roles_display}."
            ),
        )

        return redirect("accounts:user_management")


class FCUPasswordResetView(auth_views.PasswordResetView):
    """
    Password reset that never shows a server error because the mail server is down or
    not configured. The visitor always sees the same "check your email" page (so the
    form cannot be used to find out which emails are registered), and the failure is
    written to the log for the Administrator.
    """

    def form_valid(self, form):

        try:
            return super().form_valid(form)
        except Exception:
            logger.exception("Password reset email could not be sent")
            return HttpResponseRedirect(self.get_success_url())



# ======================================================================
# Passwords
# ======================================================================

class ChangePasswordView(auth_views.PasswordChangeView):
    """
    Any signed-in user can set a new password. When the account was flagged
    (must_change_password) this is also the page they are sent to first.
    """

    template_name = "accounts/password_change.html"
    success_url = reverse_lazy("core:dashboard")

    def form_valid(self, form):
        response = super().form_valid(form)

        user = self.request.user
        if user.must_change_password:
            user.must_change_password = False
            user.save(update_fields=["must_change_password"])

        log_action(
            AuditLog.Action.UPDATE,
            instance=user,
            actor=user,
            new_value={"event": "PASSWORD_CHANGED"},
        )
        messages.success(self.request, "Your password has been changed.")

        return response


class UserSecurityMixin(RoleRequiredMixin):
    allowed_roles = (User.Role.ADMINISTRATOR,)


class SetTemporaryPasswordView(UserSecurityMixin, View):
    """
    Administrator gives a member a temporary password (shown once) and the member
    must choose their own at the next sign-in. Works without any email set up.
    """

    http_method_names = ["post"]

    def post(self, request, pk):
        target = get_object_or_404(User, pk=pk)

        if target.pk == request.user.pk:
            messages.error(request, "Use Change password for your own account.")
            return redirect("accounts:user_management")

        temporary = "-".join(secrets.token_hex(2) for _ in range(3))
        target.set_password(temporary)
        target.must_change_password = True
        target.save(update_fields=["password", "must_change_password"])

        log_action(
            AuditLog.Action.UPDATE,
            instance=target,
            actor=request.user,
            new_value={"event": "TEMPORARY_PASSWORD_SET"},
        )
        messages.warning(
            request,
            f"Temporary password for {target.username}: {temporary}  "
            "(shown once - give it to the member; they must choose a new one when they sign in).",
        )
        return redirect("accounts:user_management")


class RequirePasswordChangeView(UserSecurityMixin, View):
    """
    Prompt members to choose a new password at their next sign-in: one user
    (user pk) or everyone else (pk "all").
    """

    http_method_names = ["post"]

    def post(self, request, pk=None):
        users = User.objects.exclude(pk=request.user.pk)
        if pk is not None:
            users = users.filter(pk=pk)

        count = users.update(must_change_password=True)

        log_action(
            AuditLog.Action.UPDATE,
            actor=request.user,
            model_name="User",
            object_repr="password change required",
            new_value={"event": "PASSWORD_CHANGE_REQUIRED", "users": count},
        )
        messages.success(
            request,
            f"{count} user(s) will be asked to choose a new password the next time they sign in.",
        )
        return redirect("accounts:user_management")


# ======================================================================
# Sign in as a member (Administrator only)
# ======================================================================

IMPERSONATOR_KEY = "impersonator_id"


@login_required
@require_POST
def impersonate_start(request, pk):
    """The Administrator signs in as another user to see exactly what they see."""

    admin = request.user

    if not admin.can_impersonate or request.session.get(IMPERSONATOR_KEY):
        raise PermissionDenied("Only an Administrator can sign in as a member.")

    target = get_object_or_404(User, pk=pk, is_active=True)

    if target.pk == admin.pk:
        messages.info(request, "You are already signed in as yourself.")
        return redirect("accounts:user_management")

    if target.can_impersonate:
        messages.error(request, "You cannot sign in as another Administrator.")
        return redirect("accounts:user_management")

    log_action(
        AuditLog.Action.LOGIN,
        instance=target,
        actor=admin,
        new_value={"event": "IMPERSONATION_STARTED", "as": target.username},
    )

    # login() starts a fresh session for a different user, so mark it afterwards.
    auth_login(request, target, backend="django.contrib.auth.backends.ModelBackend")
    request.session[IMPERSONATOR_KEY] = admin.pk

    messages.info(request, f"You are now signed in as {target.username}. Use the banner to switch back.")
    return redirect("core:dashboard")


@login_required
@require_POST
def impersonate_stop(request):
    admin_id = request.session.get(IMPERSONATOR_KEY)
    if not admin_id:
        return redirect("core:dashboard")

    admin = get_object_or_404(User, pk=admin_id, is_active=True)
    as_user = request.user

    auth_login(request, admin, backend="django.contrib.auth.backends.ModelBackend")
    log_action(
        AuditLog.Action.LOGIN,
        instance=as_user,
        actor=admin,
        new_value={"event": "IMPERSONATION_ENDED", "as": as_user.username},
    )

    messages.success(request, "You are back in your own account.")
    return redirect("accounts:user_management")
