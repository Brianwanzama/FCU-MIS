from django.apps import AppConfig


class AuditlogConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.auditlog"
    verbose_name = "Audit Log"

    def ready(self):
        from . import signals  # noqa: F401  (registers the auth signal receivers)
