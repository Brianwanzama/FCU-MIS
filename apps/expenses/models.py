"""
Expenses of the unit, recorded against the cycle in which they were paid so that each cycle's
report can show what was spent (Manual 10.2) and the derived cash figure stays honest.
"""

from decimal import Decimal

from django.conf import settings
from django.db import models


class Expense(models.Model):
    class Category(models.TextChoices):
        ADMINISTRATION = "ADMINISTRATION", "Administration"
        MEETING = "MEETING", "Meetings and refreshments"
        BANK_CHARGES = "BANK_CHARGES", "Bank and mobile money charges"
        TRANSPORT = "TRANSPORT", "Transport"
        STATIONERY = "STATIONERY", "Stationery and printing"
        COMMUNICATION = "COMMUNICATION", "Airtime and communication"
        WELFARE = "WELFARE", "Welfare and support"
        OTHER = "OTHER", "Other"

    class PaymentMethod(models.TextChoices):
        CASH = "CASH", "Cash"
        BANK = "BANK", "Bank Transfer"
        MOBILE_MONEY = "MOBILE_MONEY", "Mobile Money"

    cycle = models.ForeignKey("cycles.Cycle", on_delete=models.PROTECT, related_name="expenses")
    category = models.CharField(max_length=20, choices=Category.choices)
    description = models.CharField(max_length=200, help_text="What the money was spent on.")
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    expense_date = models.DateField(help_text="The day the money was spent.")
    paid_to = models.CharField(max_length=150, blank=True, help_text="Who was paid (shop, person, bank).")
    payment_method = models.CharField(max_length=20, choices=PaymentMethod.choices, default=PaymentMethod.CASH)
    reference = models.CharField(max_length=100, blank=True, help_text="Receipt number or transaction ID.")
    notes = models.TextField(blank=True)

    recorded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-expense_date", "-id"]
        constraints = [
            models.CheckConstraint(condition=models.Q(amount__gt=0), name="expense_amount_positive"),
        ]

    def __str__(self):
        return f"{self.get_category_display()}: {self.description} ({self.amount:,.0f})"
