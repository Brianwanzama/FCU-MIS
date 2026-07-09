"""
Shared RBAC building blocks (FR-3). Every view in every app — current and future
phases — should use one of these rather than re-implementing role checks inline,
so the access rules stay consistent and are enforced server-side, never just by
hiding a button in a template.
"""
from functools import wraps

from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.core.exceptions import PermissionDenied


def role_required(*roles):
    """Function-view decorator: @role_required(User.Role.ADMINISTRATOR, User.Role.TREASURER)"""

    def decorator(view_func):
        @wraps(view_func)
        @login_required
        def _wrapped(request, *args, **kwargs):
            if request.user.role not in roles:
                raise PermissionDenied("You do not have permission to access this page.")
            return view_func(request, *args, **kwargs)

        return _wrapped

    return decorator


class RoleRequiredMixin(LoginRequiredMixin, UserPassesTestMixin):
    """Class-based-view mixin. Set `allowed_roles = (User.Role.ADMINISTRATOR, ...)` on the view."""

    allowed_roles = ()

    def test_func(self):
        return self.request.user.role in self.allowed_roles


def owner_or_staff_required(get_member_code):
    """
    Enforces FR-3.3: a plain Member may only ever see their own record.
    `get_member_code(request, *args, **kwargs)` should return the member_code
    the view is about to display; staff roles (everyone but MEMBER) bypass the check.
    """

    def decorator(view_func):
        @wraps(view_func)
        @login_required
        def _wrapped(request, *args, **kwargs):
            user = request.user
            if user.is_plain_member:
                target_code = get_member_code(request, *args, **kwargs)
                if target_code != user.member.member_code:
                    raise PermissionDenied("Members may only view their own information.")
            return view_func(request, *args, **kwargs)

        return _wrapped

    return decorator
