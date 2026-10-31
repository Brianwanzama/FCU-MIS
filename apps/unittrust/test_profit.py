"""The profit statement: interest on loans, unit trust, other investments, less expenses."""
from datetime import timedelta
from decimal import Decimal

from django.core.exceptions import PermissionDenied
from django.utils import timezone

from apps.auditlog.models import AuditLog
from apps.expenses.models import Expense
from apps.expenses.services import record_expense
from apps.loans.services import record_offsystem_loan

from . import services
from .models import ProfitStatement
from .tests import InvestmentCase


class ProfitCase(InvestmentCase):
    def setUp(self):
        self.deposit(self.m1, "300000")
        self.deposit(self.m2, "100000")

        # Interest on loans: 300,000 for 3 months at 5%; first instalment (interest 15,000) repaid.
        record_offsystem_loan(
            self.treasurer, self.m1, Decimal("300000"), 3, timezone.localdate() - timedelta(days=20),
            already_repaid=Decimal("110163"))

        ut = self.investment("UNIT_TRUST", "Unit Trust A")
        self.tx(ut, "INVEST", "1000000")
        self.tx(ut, "INCOME", "50000")
        self.tx(ut, "FEE", "5000")
        mf = self.investment("MICROFINANCE", "Village venture")
        self.tx(mf, "INVEST", "500000")
        self.tx(mf, "INCOME", "30000")
        re = self.investment("REAL_ESTATE", "Plot")
        self.tx(re, "INCOME", "20000")

        record_expense(
            Expense(cycle=self.cycle, category="MEETING", description="AGM tea", amount=Decimal("10000"),
                    expense_date=timezone.localdate(), payment_method="CASH"), self.treasurer)


class FigureTests(ProfitCase):
    def test_income_is_split_by_source_and_expenses_come_off(self):
        f = services.profit_figures(self.cycle)
        self.assertEqual(f["loan_interest"], Decimal("15000"))
        self.assertEqual(f["unit_trust_income"], Decimal("45000"))          # 50,000 less 5,000 fees
        self.assertEqual(f["other_investment_income"], Decimal("50000"))    # 30,000 + 20,000
        self.assertEqual(f["gross_income"], Decimal("110000"))
        self.assertEqual(f["expenses"], Decimal("10000"))
        self.assertEqual(f["net_profit"], Decimal("100000"))
        self.assertEqual({r["kind"]: r["amount"] for r in f["other_breakdown"]},
                         {"MICROFINANCE": Decimal("30000"), "REAL_ESTATE": Decimal("20000")})

    def test_reversed_transactions_do_not_count(self):
        ut = self.investment("UNIT_TRUST", "Unit Trust B")
        entry = self.tx(ut, "INCOME", "1000000")
        services.reverse_transaction(entry, self.chair, "wrong")
        self.assertEqual(services.profit_figures(self.cycle)["unit_trust_income"], Decimal("45000"))


class CaptureAndPublishTests(ProfitCase):
    def test_capture_shares_the_net_profit_by_relative_contribution(self):
        st = services.capture_profit(self.cycle, self.treasurer)
        self.assertEqual(st.net_profit, Decimal("100000"))
        amounts = {s.member_id: s.amount for s in st.shares.all()}
        self.assertEqual(amounts[self.m1.pk], Decimal("75000"))
        self.assertEqual(amounts[self.m2.pk], Decimal("25000"))
        self.assertEqual(sum(amounts.values()), Decimal("100000"))
        self.assertFalse(st.is_published)

    def test_members_see_nothing_until_published_then_their_own_share(self):
        st = services.capture_profit(self.cycle, self.treasurer)
        self.assertEqual(services.member_profit(self.m1)["shares"], [])
        services.publish_profit(st, self.chair)
        mine = services.member_profit(self.m1)
        self.assertEqual(mine["total"], Decimal("75000"))
        self.assertEqual(mine["shares"][0].share_percent, Decimal("75.0000"))
        self.assertEqual(services.member_profit(self.m2)["total"], Decimal("25000"))

    def test_updating_withdraws_it_until_published_again(self):
        st = services.capture_profit(self.cycle, self.treasurer)
        services.publish_profit(st, self.admin)
        self.tx(self.investment("UNIT_TRUST", "UT C"), "INCOME", "100000")
        st = services.capture_profit(self.cycle, self.treasurer)
        self.assertFalse(st.is_published)
        self.assertEqual(st.unit_trust_income, Decimal("145000"))
        self.assertEqual(ProfitStatement.objects.count(), 1)

    def test_a_loss_shares_nothing(self):
        record_expense(
            Expense(cycle=self.cycle, category="OTHER", description="Big bill", amount=Decimal("500000"),
                    expense_date=timezone.localdate(), payment_method="CASH"), self.treasurer)
        st = services.capture_profit(self.cycle, self.treasurer)
        self.assertLess(st.net_profit, 0)
        self.assertTrue(all(s.amount == 0 for s in st.shares.all()))

    def test_who_may_capture_and_publish(self):
        with self.assertRaises(PermissionDenied):
            services.capture_profit(self.cycle, self.chair)
        with self.assertRaises(PermissionDenied):
            services.capture_profit(self.cycle, self.u1)
        st = services.capture_profit(self.cycle, self.admin)
        with self.assertRaises(PermissionDenied):
            services.publish_profit(st, self.treasurer)

    def test_everything_is_audited_and_delete_needs_a_reason(self):
        st = services.capture_profit(self.cycle, self.treasurer)
        services.publish_profit(st, self.chair)
        events = set(AuditLog.objects.filter(model_name="ProfitStatement").values_list("new_value__event", flat=True))
        self.assertTrue({"PROFIT_CAPTURED", "PROFIT_PUBLISHED"} <= events)
        with self.assertRaises(services.InvestmentError):
            services.delete_profit_statement(st, self.treasurer, "")
        services.delete_profit_statement(st, self.treasurer, "wrong cycle")
        self.assertFalse(ProfitStatement.objects.exists())


class ProfitPageTests(ProfitCase):
    def test_management_sees_the_sources_and_every_share(self):
        self.client.force_login(self.treasurer)
        self.client.post(f"/investments/profit/{self.cycle.pk}/capture/")
        page = self.client.get("/investments/profit/")
        for text in ("Interest on loans", "Unit trust", "Other investments", "Net profit"):
            self.assertContains(page, text)
        self.assertContains(page, "Each member's share of the profit")

    def test_chairperson_publishes_and_a_member_sees_only_their_own_share(self):
        self.client.force_login(self.treasurer)
        self.client.post(f"/investments/profit/{self.cycle.pk}/capture/")
        self.client.force_login(self.u1)
        self.assertContains(self.client.get("/investments/profit/"), "No profit has been published yet")
        self.client.force_login(self.chair)
        self.client.post(f"/investments/profit/{self.cycle.pk}/publish/")
        self.client.force_login(self.u1)
        page = self.client.get("/investments/profit/")
        self.assertContains(page, "My profit")
        self.assertContains(page, "75.00%")
        self.assertNotContains(page, "Interest on loans")      # the breakdown is for management
        self.assertNotContains(page, self.m2.full_name)         # other members' shares are private

    def test_a_member_cannot_capture_or_publish(self):
        self.client.force_login(self.u1)
        self.client.post(f"/investments/profit/{self.cycle.pk}/capture/")
        self.assertFalse(ProfitStatement.objects.exists())

    def test_no_em_dashes(self):
        self.client.force_login(self.treasurer)
        self.assertNotContains(self.client.get("/investments/profit/"), "—")
