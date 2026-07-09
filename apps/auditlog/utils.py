from .middleware import get_current_request
from .models import AuditLog


def _client_ip(request):
    if request is None:
        return None
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR")


def log_action(action, instance=None, old_value=None, new_value=None, actor=None, model_name="", object_id="", object_repr=""):
    """
    Central helper every app should call for anything FR-16 requires logged:
    financial record writes, status changes, role changes, settings changes,
    account activation. Keeping this in one place means every log entry has
    a consistent shape and nobody has to re-derive actor/IP by hand.
    """
    request = get_current_request()
    if actor is None and request is not None and request.user.is_authenticated:
        actor = request.user

    if instance is not None:
        model_name = model_name or instance.__class__.__name__
        object_id = object_id or str(getattr(instance, "pk", ""))
        object_repr = object_repr or str(instance)

    AuditLog.objects.create(
        actor=actor,
        action=action,
        model_name=model_name,
        object_id=object_id,
        object_repr=object_repr,
        old_value=old_value,
        new_value=new_value,
        ip_address=_client_ip(request),
    )
