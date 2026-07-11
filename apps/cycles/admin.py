from django.contrib import admin

from .models import Cycle, FinancialYear


@admin.register(FinancialYear)
class FinancialYearAdmin(admin.ModelAdmin):
    list_display = ("year", "cycle_count", "is_complete")
    ordering = ("-year",)
    readonly_fields = ("created_at",)

    @admin.display(description="Cycles")
    def cycle_count(self, obj):
        return obj.cycles.count()

    def has_add_permission(self, request):
        # Financial years are derived from cycle start dates. Creating one by
        # hand would produce an empty year that nothing points at.
        return False


@admin.register(Cycle)
class CycleAdmin(admin.ModelAdmin):
    """
    Read-mostly. Status is deliberately NOT editable here - the Django admin has
    no audit hook, and a status changed through it would bypass services.py and
    leave no trail. Use the Cycles pages in the app itself.
    """

    list_display = (
        "cycle_number", "name", "financial_year", "status",
        "start_date", "end_date", "per_member_target",
    )
    list_filter = ("status", "financial_year")
    search_fields = ("name", "cycle_number")
    ordering = ("-cycle_number",)
    readonly_fields = (
        "financial_year", "end_date", "per_member_target", "status",
        "active_singleton", "activated_at", "activated_by",
        "closed_at", "closed_by", "reopen_count",
        "created_at", "updated_at",
    )

    def has_delete_permission(self, request, obj=None):
        # Deletion has rules (is_deletable) and must be audited. Both live in the
        # app's own delete view.
        return False
