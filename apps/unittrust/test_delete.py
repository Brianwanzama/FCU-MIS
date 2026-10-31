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


class ContributionShareTests(InvestmentCase):
    def test_share_is_total_deposits_over_all_cycles_as_a_percent_of_everyones(self):
        self.deposit(self.m1, "300000")
        self.deposit(self.m2, "100000")
        self.deposit(self.m3, "100000")
        # Emergency contributions are never counted.
        cs_record = __import__("apps.contributions.services", fromlist=["record_contribution"]).record_contribution
        from apps.contributions.models import Contribution
        cs_record(Contribution(member=self.m1, cycle=self.cycle, contribution_type="EMERGENCY", amount=Decimal("30000"),
                               period_month=self.cycle.start_date, payment_date=timezone.localdate(),
                               payment_method="CASH", recorded_by=self.admin), actor=self.admin)
        data = services.contribution_shares()
        self.assertEqual(data["total"], Decimal("500000"))
        percents = {r["member"].pk: r["percent"] for r in data["rows"]}
        self.assertEqual(percents[self.m1.pk], Decimal("60.00"))
        self.assertEqual(percents[self.m2.pk], Decimal("20.00"))
        mine = services.member_investment_summary(self.m1)
        self.assertEqual(mine["share_percent"], Decimal("60.00"))
        self.assertEqual(mine["my_deposits"], Decimal("300000"))
        self.assertEqual(mine["all_deposits"], Decimal("500000"))

    def test_officers_see_every_members_share_and_members_do_not(self):
        self.deposit(self.m1, "300000")
        self.deposit(self.m2, "100000")
        self.client.force_login(self.treasurer)
        page = self.client.get("/investments/")
        self.assertContains(page, "Each member")
        self.assertContains(page, "75.00%")
        self.client.force_login(self.u1)
        self.assertNotContains(self.client.get("/investments/"), "Each member")
