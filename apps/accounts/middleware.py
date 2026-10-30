from django.shortcuts import redirect
from django.urls import reverse

from .views import IMPERSONATOR_KEY


class ForcePasswordChangeMiddleware:
    """
    A signed-in user flagged with must_change_password can only reach the
    change-password page (and log out) until they have chosen a new password.
    The Administrator signed in as someone else is not held up by it.
    """

    ALWAYS_ALLOWED_PREFIXES = ("/static/", "/healthz/")

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)

        if (
            user is not None
            and user.is_authenticated
            and user.must_change_password
            and not request.session.get(IMPERSONATOR_KEY)
        ):
            allowed = {
                reverse("accounts:password_change"),
                reverse("accounts:logout"),
            }
            path = request.path
            if path not in allowed and not path.startswith(self.ALWAYS_ALLOWED_PREFIXES):
                return redirect("accounts:password_change")

        return self.get_response(request)
