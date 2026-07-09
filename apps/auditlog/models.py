from django.conf import settings
from django.db import models


class AuditLog(models.Model):
    """
    Append-only record of every significant system action (FR-16).
    Deliberately has no update/delete path anywhere in the app layer —
    see AuditLogAdmin and the absence of any edit/delete view for this model.
    """

    class Action(models.TextChoices):
        CREATE = "CREATE", "Create"
        UPDATE = "UPDATE", "Update"
        DELETE = "DELETE", "Delete"  # logical/soft actions only — financial rows are never hard-deleted
        LOGIN = "LOGIN", "Login"
        LOGIN_FAILED = "LOGIN_FAILED", "Failed login"
        LOGOUT = "LOGOUT", "Logout"
        STATUS_CHANGE = "STATUS_CHANGE", "Status change"
        ROLE_CHANGE = "ROLE_CHANGE", "Role change"
        SETTINGS_CHANGE = "SETTINGS_CHANGE", "Settings change"
        ACCOUNT_ACTIVATED = "ACCOUNT_ACTIVATED", "Account activated"

    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="audit_entries",
        help_text="Null for system-initiated actions or anonymous failed logins.",
    )
    action = models.CharField(max_length=32, choices=Action.choices)
    model_name = models.CharField(max_length=100, blank=True)
    object_id = models.CharField(max_length=64, blank=True)
    object_repr = models.CharField(max_length=255, blank=True)
    old_value = models.JSONField(null=True, blank=True)
    new_value = models.JSONField(null=True, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    timestamp = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-timestamp"]
        indexes = [
            models.Index(fields=["-timestamp"]),
            models.Index(fields=["model_name", "object_id"]),
            models.Index(fields=["actor"]),
        ]
        verbose_name = "Audit log entry"
        verbose_name_plural = "Audit log entries"

    def __str__(self):
        who = self.actor.get_username() if self.actor else "system"
        return f"[{self.timestamp:%Y-%m-%d %H:%M}] {who} {self.action} {self.model_name} {self.object_repr}"
