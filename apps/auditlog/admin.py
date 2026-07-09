from django.contrib import admin

from .models import AuditLog


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    """Read-only by design — audit entries must be viewable but never editable
    or deletable, including by superusers, so the trail stays trustworthy."""

    list_display = ("timestamp", "actor", "action", "model_name", "object_repr", "ip_address")
    list_filter = ("action", "model_name")
    search_fields = ("object_repr", "actor__username", "actor__email", "ip_address")
    readonly_fields = [f.name for f in AuditLog._meta.fields]
    date_hierarchy = "timestamp"

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
