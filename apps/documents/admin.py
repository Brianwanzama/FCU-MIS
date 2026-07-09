from django.contrib import admin

from apps.auditlog.models import AuditLog
from apps.auditlog.utils import log_action

from .models import DocumentTemplate


@admin.register(DocumentTemplate)
class DocumentTemplateAdmin(admin.ModelAdmin):
    list_display = ("doc_type", "version_label", "is_current", "effective_from", "uploaded_by")
    list_filter = ("doc_type", "is_current")

    def save_model(self, request, obj, form, change):
        obj.uploaded_by = request.user
        super().save_model(request, obj, form, change)
        log_action(
            AuditLog.Action.UPDATE if change else AuditLog.Action.CREATE,
            instance=obj,
            new_value={"doc_type": obj.doc_type, "version_label": obj.version_label},
            actor=request.user,
        )
