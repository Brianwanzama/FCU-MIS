"""
Financial-calculation tests: deposits and emergency contributions are separate
obligations, and targets come from the cycle configuration.
"""
from datetime import date
from decimal import Decimal

from django.template import Context, Template
from django.test import SimpleTestCase, TestCase

from apps.accounts.models import User
from apps.cycles import services as cycle_services
from apps.cycles.models import Cycle
from apps.members.models import Member

from . import services
from .models import Contribution

M = Contribution.ContributionType


class FinancialTargetTests(TestCase):
    """The FCU worked example: 19 members, UGX 100,000 + 5,000 a month, 6 months."""

    @classmethod
    def setUpTestData(cls):
        cls.actor = User.objects.create_user(
            username="treasurer", password="x", role=User.Role.TREASURER
        )
        cycle = Cycle(
            cycle_number=1, start_date=date(2026, 1, 1), duration_months=6,
            monthly_savings_amount=Decimal("100000"),
            monthly_emergency_amount=Decimal("5000"),
        )
        cls.cycle = cycle_services.create_cycle(cycle, actor=cls.actor)
        cycle_services.activate_cycle(cls.cycle, actor=cls.actor)
        cls.member = Member.objects.order_by("member_code").first()

    def pay(self, kind, amount, member=None):
        c = Contribution(
            member=member or self.member, cycle=self.cycle, contribution_type=kind,
            amount=Decimal(amount), period_month=date(2026, 1, 1),
            payment_date=date(2026, 1, 5), payment_method="CASH",
            recorded_by=self.actor,
        )
        return services.record_contribution(c, actor=self.actor)

    def test_member_targets_are_separate(self):
        self.assertEqual(self.cycle.member_deposit_target, Decimal("600000"))
        self.assertEqual(self.cycle.member_emergency_target, Decimal("30000"))

    def test_unit_targets_follow_active_member_count(self):
        n = Member.objects.filter(status=Member.Status.ACTIVE).count()
        self.assertEqual(self.cycle.eligible_member_count, n)
        self.assertEqual(self.cycle.unit_deposit_target, Decimal("600000") * n)
        self.assertEqual(self.cycle.unit_emergency_target, Decimal("30000") * n)

    def test_nineteen_members_gives_the_fcu_figures(self):
        Member.objects.exclude(pk__in=Member.objects.order_by("member_code")[:19]).update(
            status=Member.Status.INACTIVE
        )
        Member.objects.filter(pk__in=Member.objects.order_by("member_code")[:19]).update(
            status=Member.Status.ACTIVE
        )
        self.assertEqual(self.cycle.unit_deposit_target, Decimal("11400000"))
        self.assertEqual(self.cycle.unit_emergency_target, Decimal("570000"))

    def test_cycle_summary_keeps_deposits_and_emergency_apart(self):
        self.pay(M.MONTHLY, "100000")
        self.pay(M.EMERGENCY, "5000")
        s = services.cycle_contribution_summary(self.cycle)
        self.assertEqual(s["collected"], Decimal("100000"))
        self.assertEqual(s["emergency_collected"], Decimal("5000"))
        self.assertEqual(s["expected"], self.cycle.unit_deposit_target)
        self.assertEqual(s["emergency_expected"], self.cycle.unit_emergency_target)
        self.assertEqual(s["outstanding"], s["expected"] - Decimal("100000"))

    def test_emergency_overpayment_does_not_cover_a_deposit_shortfall(self):
        self.pay(M.MONTHLY, "500000")      # 100,000 short
        self.pay(M.EMERGENCY, "130000")    # 100,000 over
        b = services.member_balance(self.member, self.cycle)
        self.assertEqual(b["monthly_balance"], Decimal("100000"))
        self.assertEqual(b["emergency_balance"], Decimal("-100000"))
        self.assertFalse(b["eligible"])

    def test_member_is_eligible_only_when_both_are_cleared(self):
        self.pay(M.MONTHLY, "600000")
        self.assertFalse(services.member_balance(self.member, self.cycle)["eligible"])
        self.pay(M.EMERGENCY, "30000")
        self.assertTrue(services.member_balance(self.member, self.cycle)["eligible"])

    def test_total_deposits_exclude_emergency(self):
        from apps.members.services import member_financial_summary

        self.pay(M.MONTHLY, "100000")
        self.pay(M.EMERGENCY, "5000")
        s = member_financial_summary(self.member, self.cycle)
        self.assertEqual(s["total_deposits"], Decimal("100000"))
        self.assertEqual(s["total_emergency"], Decimal("5000"))


class CurrencyFilterTests(SimpleTestCase):
    def render(self, value):
        return Template("{{ v|ugx }}").render(Context({"v": value}))

    def test_formats_with_thousands_separators(self):
        for raw, shown in [
            (100000, "UGX 100,000"),
            (600000, "UGX 600,000"),
            (1250000, "UGX 1,250,000"),
            (12000000, "UGX 12,000,000"),
            (Decimal("100000.00"), "UGX 100,000"),
            (0, "UGX 0"),
            (-50000, "UGX -50,000"),
        ]:
            self.assertEqual(self.render(raw), shown)

    def test_bad_values_do_not_break_the_page(self):
        self.assertEqual(self.render(None), "UGX 0")
        self.assertEqual(self.render(""), "UGX 0")
