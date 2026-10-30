"""Deleting contributions, and the quick-amount lists on the entry form."""
from datetime import date
from decimal import Decimal

from django.core.exceptions import PermissionDenied
from django.test import TestCase

from apps.accounts.models import User
from apps.auditlog.models import AuditLog
from apps.cycles import services as cycle_services
from apps.cycles.models import Cycle
from apps.members.models import Member

from . import services
from .forms import DEPOSIT_QUICK_AMOUNTS, EMERGENCY_QUICK_AMOUNTS
from .models import Contribution, ContributionReversal


class DeleteContributionTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        mk = lambda name, role: User.objects.create_user(username=name, password="pw", role=role)
        cls.admin = mk("admin", User.Role.ADMINISTRATOR)
        cls.treasurer = mk("treasurer", User.Role.TREASURER)
        cls.chair = mk("chair", User.Role.CHAIRPERSON)
        cls.secretary = mk("secretary", User.Role.SECRETARY)
        cls.cycle = cycle_services.create_cycle(
            Cycle(cycle_number=1, start_date=date(2026, 1, 1), duration_months=6), actor=cls.admin,
        )
        cycle_services.activate_cycle(cls.cycle, actor=cls.admin)
        cls.member = Member.objects.order_by("member_code").first()

    def make(self, amount="100000"):
        return services.record_contribution(
            Contribution(
                member=self.member, cycle=self.cycle, contribution_type="MONTHLY",
                amount=Decimal(amount), period_month=date(2026, 1, 1),
                payment_date=date(2026, 1, 5), payment_method="CASH", recorded_by=self.treasurer,
            ),
            actor=self.treasurer,
        )

    def paid(self):
        return services.member_balance(self.member, self.cycle)["monthly_paid"]

    def test_delete_removes_it_from_the_balance_and_keeps_an_audit_copy(self):
        c = self.make()
        self.assertEqual(self.paid(), Decimal("100000"))
        services.delete_contribution(c, self.treasurer, "entered for the wrong member")
        self.assertFalse(Contribution.objects.filter(pk=c.pk).exists())
        self.assertEqual(self.paid(), Decimal("0"))
        entry = AuditLog.objects.get(new_value__event="CONTRIBUTION_DELETED")
        self.assertEqual(entry.old_value["amount"], "100000.00")
        self.assertEqual(entry.new_value["reason"], "entered for the wrong member")

    def test_a_reason_is_required(self):
        c = self.make()
        with self.assertRaises(services.ContributionError):
            services.delete_contribution(c, self.treasurer, "  ")

    def test_who_may_delete(self):
        for user in (self.treasurer, self.chair, self.admin):
            c = self.make()
            services.delete_contribution(c, user, "mistake")
        with self.assertRaises(PermissionDenied):
            services.delete_contribution(self.make(), self.secretary, "mistake")

    def test_a_contribution_with_reversal_requests_can_still_be_deleted(self):
        c = self.make()
        services.request_reversal(c, self.treasurer, "wrong")
        services.delete_contribution(c, self.admin, "wrong")
        self.assertFalse(ContributionReversal.objects.exists())

    def test_closed_cycle_contributions_cannot_be_deleted(self):
        c = self.make()
        cycle_services.close_cycle(self.cycle, actor=self.admin)
        with self.assertRaises(services.ContributionError):
            services.delete_contribution(c, self.admin, "late")

    def test_http_flow(self):
        c = self.make()
        self.client.force_login(self.treasurer)
        self.assertContains(self.client.get(f"/contributions/{c.pk}/"), "Delete")
        self.assertEqual(self.client.get(f"/contributions/{c.pk}/delete/").status_code, 200)
        bad = self.client.post(f"/contributions/{c.pk}/delete/", {"reason": ""})
        self.assertEqual(bad.status_code, 200)
        self.assertTrue(Contribution.objects.filter(pk=c.pk).exists())
        ok = self.client.post(f"/contributions/{c.pk}/delete/", {"reason": "duplicate"})
        self.assertRedirects(ok, "/contributions/")
        self.assertFalse(Contribution.objects.filter(pk=c.pk).exists())

    def test_secretary_cannot_open_the_delete_page(self):
        c = self.make()
        self.client.force_login(self.secretary)
        self.assertEqual(self.client.get(f"/contributions/{c.pk}/delete/").status_code, 302)
        self.assertTrue(Contribution.objects.filter(pk=c.pk).exists())


class QuickAmountTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_user(username="admin", password="pw", role=User.Role.ADMINISTRATOR)
        cls.cycle = cycle_services.create_cycle(
            Cycle(cycle_number=1, start_date=date(2026, 1, 1), duration_months=6), actor=cls.admin,
        )
        cycle_services.activate_cycle(cls.cycle, actor=cls.admin)

    def test_lists_are_100k_and_5k_steps(self):
        self.assertEqual(DEPOSIT_QUICK_AMOUNTS[:3], [100000, 200000, 300000])
        self.assertEqual(EMERGENCY_QUICK_AMOUNTS[:3], [5000, 10000, 15000])

    def test_entry_form_offers_them(self):
        self.client.force_login(self.admin)
        page = self.client.get("/contributions/new/")
        self.assertContains(page, "Quick amount")
        self.assertContains(page, "deposit-amounts")
        self.assertContains(page, "600000")
        self.assertContains(page, "30000")
