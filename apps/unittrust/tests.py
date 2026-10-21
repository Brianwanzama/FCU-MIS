"""
Investments, transactions, positions, and profit distribution (Manual 7 and 8).
"""
from datetime import date, timedelta
from decimal import Decimal

from django.core.exceptions import PermissionDenied
from django.test import TestCase
from django.utils import timezone

from apps.accounts.models import User
from apps.auditlog.models import AuditLog
from apps.contributions import services as cs
from apps.contributions.models import Contribution
from apps.cycles import services as cycle_services
from apps.cycles.models import Cycle, _add_months
from apps.members.models import Member
from apps.members.services import member_financial_summary

from . import services
from .models import DistributionLine, Investment, InvestmentTransaction, ProfitDistribution

K = InvestmentTransaction.Kind


class InvestmentCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        mk = lambda name, role, member=None: User.objects.create_user(
            username=name, password="pw", role=role, member=member
        )
        cls.members = list(Member.objects.order_by("member_code")[:5])
        cls.m1, cls.m2, cls.m3, cls.m4, cls.m5 = cls.members
        cls.admin = mk("admin", User.Role.ADMINISTRATOR)
        cls.treasurer = mk("treasurer", User.Role.TREASURER)
        cls.chair = mk("chair", User.Role.CHAIRPERSON)
        cls.u1 = mk("u1", User.Role.MEMBER, cls.m1)
        cls.u2 = mk("u2", User.Role.MEMBER, cls.m2)

        first = timezone.localdate().replace(day=1)
        cls.cycle = cycle_services.create_cycle(
            Cycle(cycle_number=1, start_date=_add_months(first, -2), duration_months=6), actor=cls.admin
        )
        cycle_services.activate_cycle(cls.cycle, actor=cls.admin)

    def deposit(self, member, amount):
        return cs.record_contribution(
            Contribution(
                member=member, cycle=self.cycle, contribution_type="MONTHLY", amount=Decimal(amount),
                period_month=self.cycle.start_date, payment_date=timezone.localdate(),
                payment_method="CASH", recorded_by=self.admin,
            ),
            actor=self.admin,
        )

    def investment(self, kind="UNIT_TRUST", name="Unit Trust A"):
        return services.create_investment(
            self.treasurer, name=name, kind=kind, provider="Fund Co", opened_on=timezone.localdate() - timedelta(days=30),
        )

    def tx(self, inv, kind, amount, user=None, day=None):
        return services.record_transaction(
            inv, kind, Decimal(amount), day or timezone.localdate(), user or self.treasurer
        )


class PositionTests(InvestmentCase):
    def test_position_adds_up_money_in_out_and_earned(self):
        inv = self.investment()
        self.tx(inv, K.INVEST, "5000000")
        self.tx(inv, K.INCOME, "200000")
        self.tx(inv, K.FEE, "20000")
        self.tx(inv, K.VALUATION, "5300000")
        p = services.position(inv)
        self.assertEqual(p["invested"], Decimal("5000000"))
        self.assertEqual(p["income"], Decimal("200000"))
        self.assertEqual(p["market_value"], Decimal("5300000"))
        self.assertEqual(p["total_return"], Decimal("480000"))  # 5.3M + 0.2M - 5M - 0.02M
        self.assertEqual(p["return_percent"], Decimal("9.60"))

    def test_without_a_valuation_it_is_worth_what_is_still_in_it(self):
        inv = self.investment()
        self.tx(inv, K.INVEST, "1000000")
        self.tx(inv, K.REDEEM, "400000")
        self.assertEqual(services.position(inv)["market_value"], Decimal("600000"))

    def test_latest_valuation_wins(self):
        inv = self.investment()
        self.tx(inv, K.INVEST, "1000000")
        self.tx(inv, K.VALUATION, "1100000", day=timezone.localdate() - timedelta(days=10))
        self.tx(inv, K.VALUATION, "1200000")
        self.assertEqual(services.position(inv)["market_value"], Decimal("1200000"))

    def test_reversed_transactions_do_not_count(self):
        inv = self.investment()
        t = self.tx(inv, K.INVEST, "1000000")
        self.tx(inv, K.INCOME, "50000")
        services.reverse_transaction(t, self.chair, "Entered twice")
        p = services.position(inv)
        self.assertEqual(p["invested"], Decimal("0"))
        self.assertTrue(InvestmentTransaction.objects.filter(pk=t.pk).exists())

    def test_portfolio_totals(self):
        a, b = self.investment(name="A"), self.investment("REAL_ESTATE", "Plot")
        self.tx(a, K.INVEST, "1000000")
        self.tx(b, K.INVEST, "2000000")
        book = services.portfolio()
        self.assertEqual(book["invested"], Decimal("3000000"))
        self.assertEqual(book["market_value"], Decimal("3000000"))


class TransactionRuleTests(InvestmentCase):
    def test_only_treasurer_or_administrator_record(self):
        inv = self.investment()
        for user in (self.chair, self.u1):
            with self.assertRaises(PermissionDenied):
                self.tx(inv, K.INVEST, "1000", user=user)
        self.tx(inv, K.INVEST, "1000", user=self.admin)

    def test_validation(self):
        inv = self.investment()
        with self.assertRaises(services.InvestmentError):
            self.tx(inv, K.INVEST, "0")
        with self.assertRaises(services.InvestmentError):
            self.tx(inv, K.INVEST, "1000", day=timezone.localdate() + timedelta(days=1))
        with self.assertRaises(services.InvestmentError):
            self.tx(inv, K.INVEST, "1000", day=inv.opened_on - timedelta(days=1))
        self.tx(inv, K.VALUATION, "0")  # a write-off is a legitimate valuation

    def test_cannot_redeem_more_than_is_held(self):
        inv = self.investment()
        self.tx(inv, K.INVEST, "1000000")
        with self.assertRaises(services.InvestmentError):
            self.tx(inv, K.REDEEM, "1000001")

    def test_reversal_rules(self):
        inv = self.investment()
        t = self.tx(inv, K.INVEST, "1000000")
        with self.assertRaises(PermissionDenied):
            services.reverse_transaction(t, self.treasurer, "x")
        with self.assertRaises(services.InvestmentError):
            services.reverse_transaction(t, self.chair, "")
        own = self.tx(inv, K.INCOME, "1000", user=self.admin)
        with self.assertRaises(services.InvestmentError):
            services.reverse_transaction(own, self.admin, "own")
        services.reverse_transaction(t, self.admin, "Duplicate")
        with self.assertRaises(services.InvestmentError):
            services.reverse_transaction(t, self.chair, "again")

    def test_everything_is_audited(self):
        inv = self.investment()
        t = self.tx(inv, K.INVEST, "1000000")
        services.reverse_transaction(t, self.chair, "Duplicate")
        events = set(AuditLog.objects.values_list("new_value__event", flat=True))
        self.assertTrue({"INVESTMENT_CREATED", "INVESTMENT_TRANSACTION_RECORDED", "INVESTMENT_TRANSACTION_REVERSED"} <= events)

    def test_close_needs_nothing_left_inside(self):
        inv = self.investment()
        self.tx(inv, K.INVEST, "1000000")
        with self.assertRaises(services.InvestmentError):
            services.close_investment(inv, self.chair)
        self.tx(inv, K.REDEEM, "1000000")
        services.close_investment(inv, self.chair)
        with self.assertRaises(services.InvestmentError):
            self.tx(inv, K.INCOME, "1")  # closed


class SplitTests(TestCase):
    def test_shares_add_up_exactly(self):
        bases = {1: Decimal("100"), 2: Decimal("100"), 3: Decimal("100")}
        shares = services.split_proportionally(Decimal("100"), bases)
        self.assertEqual(sum(shares.values()), Decimal("100"))
        self.assertEqual(sorted(shares.values()), [Decimal("33"), Decimal("33"), Decimal("34")])

    def test_proportional_to_contributions(self):
        shares = services.split_proportionally(Decimal("1000000"), {1: Decimal("600000"), 2: Decimal("300000"), 3: Decimal("100000")})
        self.assertEqual(shares, {1: Decimal("600000"), 2: Decimal("300000"), 3: Decimal("100000")})

    def test_no_bases_means_no_shares(self):
        self.assertEqual(services.split_proportionally(Decimal("100"), {1: Decimal("0")}), {1: Decimal("0")})


class DistributionTests(InvestmentCase):
    def setUp(self):
        # m1 600k, m2 300k, m3 100k; m4 and m5 have nothing
        self.deposit(self.m1, "600000")
        self.deposit(self.m2, "300000")
        self.deposit(self.m3, "100000")

    def draft(self, total="1000000"):
        return services.draft_distribution(self.treasurer, Decimal(total), "Loan interest")

    def lines(self, d):
        return {l.member_id: l for l in d.lines.all()}

    def test_only_members_in_good_standing_share_and_in_proportion(self):
        d = self.draft()
        lines = self.lines(d)
        eligible = [l for l in lines.values() if l.eligible]
        self.assertEqual(sum(l.amount for l in lines.values()), Decimal("1000000"))
        for l in lines.values():
            if not l.eligible:
                self.assertEqual(l.amount, Decimal("0"))
                self.assertTrue(l.reason)
        self.assertTrue(eligible)

    def test_inactive_withdrawn_and_in_arrears_members_get_nothing(self):
        Member.objects.filter(pk=self.m1.pk).update(status=Member.Status.INACTIVE)
        Member.objects.filter(pk=self.m2.pk).update(status=Member.Status.WITHDRAWN)
        d = self.draft()
        lines = self.lines(d)
        self.assertFalse(lines[self.m1.pk].eligible)
        self.assertIn("Inactive", lines[self.m1.pk].reason)
        self.assertFalse(lines[self.m2.pk].eligible)
        self.assertEqual(lines[self.m2.pk].reason, "Withdrawn member")

    def test_member_with_no_deposits_gets_nothing(self):
        d = self.draft()
        line = self.lines(d)[self.m5.pk]
        self.assertFalse(line.eligible)
        self.assertEqual(line.amount, Decimal("0"))
        self.assertTrue(line.reason)

    def test_shares_follow_deposits_among_the_eligible(self):
        # Pay everyone up to date so m1-m3 are in Good Standing, whatever day of the month it is.
        for m, amount in ((self.m1, "300000"), (self.m2, "300000"), (self.m3, "300000")):
            self.deposit(m, amount)
        d = self.draft()
        lines = self.lines(d)
        elig = {pk: l for pk, l in lines.items() if l.eligible}
        self.assertEqual(set(elig), {self.m1.pk, self.m2.pk, self.m3.pk})
        # bases: 900k, 600k, 400k -> shares of 1,900k
        self.assertEqual(lines[self.m1.pk].amount, Decimal("473684"))
        self.assertEqual(sum(l.amount for l in lines.values()), Decimal("1000000"))
        self.assertGreater(lines[self.m1.pk].amount, lines[self.m2.pk].amount)
        self.assertGreater(lines[self.m2.pk].amount, lines[self.m3.pk].amount)

    def test_drafting_needs_the_treasurer_or_administrator(self):
        with self.assertRaises(PermissionDenied):
            services.draft_distribution(self.chair, Decimal("1000"), "x")
        with self.assertRaises(services.InvestmentError):
            services.draft_distribution(self.treasurer, Decimal("0"), "x")
        with self.assertRaises(services.InvestmentError):
            services.draft_distribution(self.treasurer, Decimal("1000"), " ")

    def test_approval_needs_resolution_and_a_different_approver(self):
        d = self.draft()
        with self.assertRaises(PermissionDenied):
            services.approve_distribution(d, self.treasurer, "GM/2026/7")
        with self.assertRaises(services.InvestmentError):
            services.approve_distribution(d, self.chair, "")
        admin_draft = services.draft_distribution(self.admin, Decimal("1000"), "own")
        with self.assertRaises(services.InvestmentError):
            services.approve_distribution(admin_draft, self.admin, "GM/2026/7")
        d = services.approve_distribution(d, self.chair, "GM/2026/7")
        self.assertEqual(d.status, ProfitDistribution.Status.APPROVED)
        self.assertEqual(d.resolution_reference, "GM/2026/7")

    def test_shares_are_recalculated_at_approval_and_then_fixed(self):
        d = self.draft()
        Member.objects.filter(pk=self.m3.pk).update(status=Member.Status.INACTIVE)
        d = services.approve_distribution(d, self.chair, "GM/2026/7")
        self.assertFalse(self.lines(d)[self.m3.pk].eligible)
        before = {l.member_id: l.amount for l in d.lines.all()}
        Member.objects.filter(pk=self.m3.pk).update(status=Member.Status.ACTIVE)
        self.assertEqual({l.member_id: l.amount for l in d.lines.all()}, before)

    def test_pay_and_cancel_lifecycle(self):
        d = services.approve_distribution(self.draft(), self.chair, "GM/1")
        with self.assertRaises(PermissionDenied):
            services.mark_distribution_paid(d, self.chair)
        d = services.mark_distribution_paid(d, self.treasurer)
        self.assertEqual(d.status, ProfitDistribution.Status.PAID)
        with self.assertRaises(services.InvestmentError):
            services.cancel_distribution(d, self.chair, "too late")
        with self.assertRaises(services.InvestmentError):
            services.approve_distribution(d, self.chair, "GM/1")

    def test_cancel_needs_a_reason(self):
        d = self.draft()
        with self.assertRaises(services.InvestmentError):
            services.cancel_distribution(d, self.chair, "")
        services.cancel_distribution(d, self.chair, "Not enough profit")

    def test_no_eligible_members_cannot_be_approved(self):
        Member.objects.update(status=Member.Status.INACTIVE)
        d = self.draft()
        with self.assertRaises(services.InvestmentError):
            services.approve_distribution(d, self.chair, "GM/1")

    def test_distribution_is_audited(self):
        services.approve_distribution(self.draft(), self.chair, "GM/9")
        e = AuditLog.objects.filter(new_value__event="DISTRIBUTION_APPROVED").get()
        self.assertEqual(e.new_value["resolution"], "GM/9")


class MemberViewTests(InvestmentCase):
    def login(self, name):
        self.client.logout()
        self.client.login(username=name, password="pw")

    def test_every_member_sees_the_portfolio_and_their_share(self):
        self.deposit(self.m1, "600000")
        inv = self.investment()
        self.tx(inv, K.INVEST, "1200000")
        self.login("u1")
        r = self.client.get("/investments/")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.context["mine"]["share_percent"], Decimal("100.00"))
        self.assertEqual(r.context["mine"]["portfolio_share"], Decimal("1200000"))

    def test_member_share_follows_their_deposits(self):
        self.deposit(self.m1, "600000")
        self.deposit(self.m2, "200000")
        inv = self.investment()
        self.tx(inv, K.INVEST, "800000")
        s = services.member_investment_summary(self.m2)
        self.assertEqual(s["share_percent"], Decimal("25.00"))
        self.assertEqual(s["portfolio_share"], Decimal("200000"))
        self.assertEqual(s["unit_trust"], Decimal("200000"))

    def test_members_see_totals_but_not_transactions(self):
        inv = self.investment()
        self.tx(inv, K.INVEST, "1000000")
        self.login("u1")
        r = self.client.get(f"/investments/{inv.pk}/")
        self.assertEqual(r.status_code, 200)
        self.assertFalse(r.context["can_see_transactions"])
        self.assertNotContains(r, "Record a transaction")
        self.login("treasurer")
        self.assertTrue(self.client.get(f"/investments/{inv.pk}/").context["can_see_transactions"])

    def test_distributions_pages_are_for_officers(self):
        self.login("u1")
        self.assertEqual(self.client.get("/investments/distributions/").status_code, 403)
        self.login("chair")
        self.assertEqual(self.client.get("/investments/distributions/").status_code, 200)

    def test_posting_is_role_checked(self):
        inv = self.investment()
        self.login("chair")
        self.client.post(f"/investments/{inv.pk}/transaction/", {"kind": "INVEST", "amount": "1000", "transaction_date": timezone.localdate().isoformat()})
        self.assertEqual(inv.transactions.count(), 0)
        self.login("treasurer")
        self.client.post(f"/investments/{inv.pk}/transaction/", {"kind": "INVEST", "amount": "1000", "transaction_date": timezone.localdate().isoformat()})
        self.assertEqual(inv.transactions.count(), 1)

    def test_distribution_flow_through_the_pages(self):
        self.deposit(self.m1, "600000")
        self.login("treasurer")
        r = self.client.post("/investments/distributions/", {"description": "Interest", "total_amount": "500000"})
        d = ProfitDistribution.objects.get()
        self.assertRedirects(r, f"/investments/distributions/{d.pk}/", fetch_redirect_response=False)
        self.client.post(f"/investments/distributions/{d.pk}/approve/", {"resolution_reference": "GM/1"})
        d.refresh_from_db()
        self.assertEqual(d.status, "DRAFT")  # the Treasurer cannot approve
        self.login("chair")
        self.client.post(f"/investments/distributions/{d.pk}/approve/", {"resolution_reference": "GM/1"})
        d.refresh_from_db()
        self.assertEqual(d.status, "APPROVED")

    def test_dashboard_net_position_uses_bonuses_not_a_double_counted_share(self):
        self.deposit(self.m1, "600000")
        inv = self.investment()
        self.tx(inv, K.INVEST, "1000000")
        s = member_financial_summary(self.m1, self.cycle)
        self.assertEqual(s["net_position"], Decimal("600000"))  # deposits only; no bonuses, no loan

    def test_paid_bonus_counts_towards_net_position(self):
        self.deposit(self.m1, "600000")
        d = services.draft_distribution(self.treasurer, Decimal("100000"), "Interest")
        d = services.approve_distribution(d, self.chair, "GM/1")
        services.mark_distribution_paid(d, self.treasurer)
        s = member_financial_summary(self.m1, self.cycle)
        self.assertEqual(s["investment"]["distributions_paid"], DistributionLine.objects.get(distribution=d, member=self.m1).amount)
        self.assertEqual(s["net_position"], Decimal("600000") + s["investment"]["distributions_paid"])
