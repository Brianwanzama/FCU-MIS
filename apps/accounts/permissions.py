"""
Shared Role-Based Access Control (RBAC) helpers.

Administrator is the highest privilege level and automatically inherits the
permissions of every other role. Future modules (Cycles, Contributions, Loans,
Reports, etc.) should use these helpers instead of comparing roles directly.
"""

from functools import wraps

from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.core.exceptions import PermissionDenied

from .models import User


def role_required(*roles):
    """
    Function-view decorator.

    Example:

        @role_required(User.Role.TREASURER)

    Administrators are always allowed automatically.
    """

    def decorator(view_func):

        @wraps(view_func)
        @login_required
        def _wrapped(request, *args, **kwargs):

            user = request.user

            # Administrator inherits every permission.
            if user.is_administrator:
                return view_func(request, *args, **kwargs)

            if user.role not in roles:
                raise PermissionDenied(
                    "You do not have permission to access this page."
                )

            return view_func(request, *args, **kwargs)

        return _wrapped

    return decorator


class RoleRequiredMixin(LoginRequiredMixin, UserPassesTestMixin):
    """
    Class-based view mixin.

    Example:

        allowed_roles = (
            User.Role.TREASURER,
        )

    Administrators automatically pass every permission check.
    """

    allowed_roles = ()

    def test_func(self):

        user = self.request.user

        if user.is_administrator:
            return True

        return user.role in self.allowed_roles


def owner_or_staff_required(get_member_code):
    """
    A Member may only access their own record.

    Administrators and office bearers automatically bypass
    the ownership restriction.
    """

    def decorator(view_func):

        @wraps(view_func)
        @login_required
        def _wrapped(request, *args, **kwargs):

            user = request.user

            # Administrator always has unrestricted access.
            if user.is_administrator:
                return view_func(request, *args, **kwargs)

            if user.is_plain_member:

                target_code = get_member_code(
                    request,
                    *args,
                    **kwargs,
                )

                if target_code != user.member.member_code:
                    raise PermissionDenied(
                        "Members may only view their own information."
                    )

            return view_func(request, *args, **kwargs)

        return _wrapped

    return decorator