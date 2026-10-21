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


class MemberStatusRuleTests(TestCase):
    """
    Status is decided when a cycle closes: fail the target and you are Inactive
    for the next cycle; complete it and you are Active.
    """

    @classmethod
    def setUpTestData(cls):
        cls.actor = User.objects.create_user(
            username="admin1", password="x", role=User.Role.ADMINISTRATOR
        )
        cls.cycle = cycle_services.create_cycle(
            Cycle(cycle_number=1, start_date=date(2026, 1, 1), duration_months=6),
            actor=cls.actor,
        )
        cycle_services.activate_cycle(cls.cycle, actor=cls.actor)
        codes = list(Member.objects.order_by("member_code").values_list("pk", flat=True))
        cls.full, cls.partial, cls.withdrawn = (Member.objects.get(pk=pk) for pk in codes[:3])
        Member.objects.filter(pk=cls.withdrawn.pk).update(status=Member.Status.WITHDRAWN)

    def pay(self, member, kind, amount):
        services.record_contribution(
            Contribution(
                member=member, cycle=self.cycle, contribution_type=kind,
                amount=Decimal(amount), period_month=date(2026, 1, 1),
                payment_date=date(2026, 1, 5), payment_method="CASH",
                recorded_by=self.actor,
            ),
            actor=self.actor,
        )

    def status(self, member):
        member.refresh_from_db()
        return member.status

    def test_part_payment_mid_cycle_does_not_make_a_member_inactive(self):
        self.pay(self.partial, M.MONTHLY, "100000")
        self.assertEqual(self.status(self.partial), Member.Status.ACTIVE)

    def test_closing_deactivates_members_who_missed_the_target(self):
        self.pay(self.full, M.MONTHLY, "600000")
        self.pay(self.full, M.EMERGENCY, "30000")
        self.pay(self.partial, M.MONTHLY, "100000")

        cycle_services.close_cycle(self.cycle, actor=self.actor)

        self.assertEqual(self.status(self.full), Member.Status.ACTIVE)
        self.assertEqual(self.status(self.partial), Member.Status.INACTIVE)
        self.assertEqual(
            self.partial.inactive_reason, Member.InactiveReason.CONTRIBUTION_ARREARS
        )

    def test_missing_only_the_emergency_obligation_still_fails_the_cycle(self):
        self.pay(self.full, M.MONTHLY, "600000")
        cycle_services.close_cycle(self.cycle, actor=self.actor)
        self.assertEqual(self.status(self.full), Member.Status.INACTIVE)

    def test_withdrawn_member_is_never_reactivated(self):
        self.pay(self.withdrawn, M.MONTHLY, "600000")
        self.pay(self.withdrawn, M.EMERGENCY, "30000")
        cycle_services.close_cycle(self.cycle, actor=self.actor)
        self.assertEqual(self.status(self.withdrawn), Member.Status.WITHDRAWN)

    def test_close_warns_about_members_who_will_become_inactive(self):
        self.pay(self.partial, M.MONTHLY, "100000")
        issues = self.cycle.blocking_issues_for_close()
        self.assertTrue(any("will become Inactive" in i for i in issues))

    def test_closing_is_recorded_in_the_audit_log(self):
        cycle_services.close_cycle(self.cycle, actor=self.actor)
        from apps.auditlog.models import AuditLog

        entry = AuditLog.objects.filter(
            new_value__event="CYCLE_CLOSED"
        ).latest("pk")
        self.assertIn(self.partial.member_code, entry.new_value["members_deactivated"])
