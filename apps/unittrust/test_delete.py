"""Deleting investments, their transactions and profit distributions."""
from decimal import Decimal

from django.core.exceptions import PermissionDenied
from django.utils import timezone

from apps.auditlog.models import AuditLog

from . import services
from .models import Investment, InvestmentTransaction, ProfitDistribution
from .tests import InvestmentCase


class DeleteInvestmentTests(InvestmentCase):
    def test_deleting_a_transaction_updates_the_position_and_is_audited(self):
        inv = self.investment()
        entry = self.tx(inv, "INVEST", "1000000")
        self.assertEqual(services.position(inv)["invested"], Decimal("1000000"))
        services.delete_transaction(entry, self.treasurer, "wrong amount")
        self.assertFalse(InvestmentTransaction.objects.filter(pk=entry.pk).exists())
        self.assertEqual(services.position(inv)["invested"], Decimal("0"))
        self.assertTrue(AuditLog.objects.filter(new_value__event="INVESTMENT_TRANSACTION_DELETED").exists())

    def test_deleting_an_investment_removes_its_transactions(self):
        inv = self.investment()
        self.tx(inv, "INVEST", "500000")
        self.tx(inv, "INCOME", "20000")
        services.delete_investment(inv, self.chair, "created by mistake")
        self.assertFalse(Investment.objects.filter(pk=inv.pk).exists())
        self.assertFalse(InvestmentTransaction.objects.filter(investment_id=inv.pk).exists())
        log = AuditLog.objects.get(new_value__event="INVESTMENT_DELETED")
        self.assertEqual(len(log.old_value["transactions"]), 2)

    def test_deleting_a_distribution(self):
        self.deposit(self.m1, "600000")
        dist = services.draft_distribution(self.treasurer, Decimal("100000"), "Interest")
        services.delete_distribution(dist, self.admin, "drafted twice")
        self.assertFalse(ProfitDistribution.objects.filter(pk=dist.pk).exists())

    def test_reason_and_permission_required(self):
        inv = self.investment()
        with self.assertRaises(services.InvestmentError):
            services.delete_investment(inv, self.treasurer, "")
        with self.assertRaises(PermissionDenied):
            services.delete_investment(inv, self.u1, "x")

    def test_http_flow(self):
        inv = self.investment()
        entry = self.tx(inv, "INVEST", "100000")
        self.client.force_login(self.treasurer)
        self.assertContains(self.client.get(f"/investments/{inv.pk}/"), "Delete investment")
        self.client.post(f"/investments/transactions/{entry.pk}/delete/", {"reason": "typo"})
        self.assertFalse(InvestmentTransaction.objects.filter(pk=entry.pk).exists())
        r = self.client.post(f"/investments/{inv.pk}/delete/", {"reason": "typo"})
        self.assertEqual(r.status_code, 302)
        self.assertFalse(Investment.objects.filter(pk=inv.pk).exists())
