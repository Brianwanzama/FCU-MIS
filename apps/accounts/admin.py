from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin

from .models import User


@admin.register(User)
class UserAdmin(DjangoUserAdmin):
    list_display = ("username", "member", "role", "is_active", "last_login")
    list_filter = ("role", "is_active")
    search_fields = ("username", "email", "member__full_name", "member__member_code")
    fieldsets = DjangoUserAdmin.fieldsets + (
        ("FCU-MIS", {"fields": ("member", "role")}),
    )
