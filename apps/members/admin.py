from django.contrib import admin

from .models import Member


@admin.register(Member)
class MemberAdmin(admin.ModelAdmin):
    list_display = ("member_code", "full_name", "email", "phone", "status", "joined_date")
    list_filter = ("status",)
    search_fields = ("member_code", "full_name", "email", "phone", "national_id_number")
    readonly_fields = ("member_code", "status", "created_at", "updated_at")
    ordering = ("member_code",)
