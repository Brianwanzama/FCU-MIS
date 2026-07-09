from django.contrib.auth.signals import user_logged_in, user_logged_out, user_login_failed
from django.dispatch import receiver

from .models import AuditLog
from .utils import _client_ip


@receiver(user_logged_in)
def on_login(sender, request, user, **kwargs):
    AuditLog.objects.create(
        actor=user,
        action=AuditLog.Action.LOGIN,
        model_name="User",
        object_id=str(user.pk),
        object_repr=user.get_username(),
        ip_address=_client_ip(request),
    )


@receiver(user_logged_out)
def on_logout(sender, request, user, **kwargs):
    if user is None:
        return
    AuditLog.objects.create(
        actor=user,
        action=AuditLog.Action.LOGOUT,
        model_name="User",
        object_id=str(user.pk),
        object_repr=user.get_username(),
        ip_address=_client_ip(request),
    )


@receiver(user_login_failed)
def on_login_failed(sender, credentials, request=None, **kwargs):
    AuditLog.objects.create(
        actor=None,
        action=AuditLog.Action.LOGIN_FAILED,
        model_name="User",
        object_repr=credentials.get("username", "unknown"),
        ip_address=_client_ip(request) if request else None,
    )
