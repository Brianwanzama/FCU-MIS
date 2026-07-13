from django.contrib import admin

# Register your models here.
"""
Django admin for Contributions.

IMPORTANT

This admin exists primarily for inspection, troubleshooting and controlled
corrections during the early phases of FCU-MIS.

Routine contribution entry MUST be performed through the Treasurer interface
(Deliverable 2 onwards), where every action is validated, audited and eventually
posted to the General Ledger.

For this reason:

- recorded_by remains editable here.
- Audit logging is intentionally NOT implemented in this admin.
- Financial posting is intentionally NOT implemented here.
"""

from django.contrib import admin
from django.db.models import QuerySet
from django.http import HttpRequest

from .models import Contribution


@admin.register(Contribution)
class ContributionAdmin(admin.ModelAdmin):
    """
    Administrative interface for Contribution records.

    Optimised for reviewing and correcting existing records rather than routine
    financial data entry.
    """

    list_select_related = (
        "member",
        "cycle",
        "recorded_by",
    )

    list_display = (
        "member",
        "cycle",
        "contribution_type",
        "amount",
        "period_month",
        "payment_date",
        "payment_method",
        "recorded_by",
    )

    list_filter = (
        "cycle",
        "contribution_type",
        "payment_method",
        "payment_date",
    )

    search_fields = (
        "member__full_name",
        "member__member_code",
        "reference",
    )

    autocomplete_fields = (
        "member",
        "cycle",
        "recorded_by",
    )

    date_hierarchy = "payment_date"

    ordering = (
        "-payment_date",
        "-created_at",
    )

    readonly_fields = (
        "created_at",
        "updated_at",
    )

    fieldsets = (
        (
            None,
            {
                "fields": (
                    "member",
                    "cycle",
                    "contribution_type",
                    "amount",
                ),
            },
        ),
        (
            "Payment Details",
            {
                "fields": (
                    "period_month",
                    "payment_date",
                    "payment_method",
                    "reference",
                ),
                "description": (
                    "Period Month represents the month being settled, while "
                    "Payment Date records when the money was actually received. "
                    "These values may legitimately differ."
                ),
            },
        ),
        (
            "Audit Information",
            {
                "fields": (
                    "recorded_by",
                    "remarks",
                    "created_at",
                    "updated_at",
                ),
            },
        ),
    )

    def get_queryset(self, request: HttpRequest) -> QuerySet[Contribution]:
        """
        Optimise the changelist by loading related objects in a single query.
        """
        return (
            super()
            .get_queryset(request)
            .select_related(
                "member",
                "cycle",
                "recorded_by",
                "recorded_by__member",
            )
        )