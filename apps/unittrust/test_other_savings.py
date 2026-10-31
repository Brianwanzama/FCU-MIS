"""
Other savings: extra money members save with FCU, placed in the unit trust. Only the share of the unit
trust's earnings that belongs to that money is credited to members; the rest stays with the group.
"""
from datetime import date, timedelta
from decimal import Decimal

from django.core.exceptions import PermissionDenied
from django.utils import timezone

from apps.auditlog.models import AuditLog

from . import services
from .models import InterestCredit, InterestWithdrawal, MemberInterest, OtherSavingsDeposit
from .tests import InvestmentCase


class SavingsCase(InvestmentCase):
    def setUp(self):
        today = timezone.localdate()
        self.month = (today.replace(day=1) - timedelta(days=1)).replace(day=1)   # last month
        self.ut = services.create_investment(
            self.treasurer, name="Unit Trust A", kind="UNIT_TRUST", provider="Fund Co",
            opened_on=self.month - timedelta(days=90))
        # The unit trust holds 1,000,000 all month (group money plus Other savings).
        self.tx(self.ut, "INVEST", "1000000", day=self.month)
        # 50,000 earned, 10,000 fees: 40,000 for the month.
        self.tx(self.ut, "INCOME", "50000", day=self.month + timedelta(days=10))
        self.tx(self.ut, "FEE", "10000", day=self.month + timedelta(days=10))
        # Other savings: m1 100,000 and m2 300,000, both from the first day of the month.
        self.save_for(self.m1, "100000")
        self.save_for(self.m2, "300000")

    def save_for(self, member, amount, day=None, actor=None):
        return services.record_other_deposit(actor or self.treasurer, member, Decimal(amount), day or self.month)


class AverageBalanceTests(InvestmentCase):
    def test_money_counts_from_the_day_it_arrives(self):
        june = date(2026, 6, 1)                                   # 30 days
        self.assertEqual(services._average_balance([(date(2026, 6, 1), Decimal("300000"))], june), Decimal("300000"))
        self.assertEqual(services._average_balance([(date(2026, 6, 16), Decimal("300000"))], june), Decimal("150000"))
        self.assertEqual(services._average_balance([(date(2026, 6, 30), Decimal("300000"))], june), Decimal("10000"))
        self.assertEqual(services._average_balance([(date(2026, 7, 1), Decimal("300000"))], june), Decimal("0"))

    def test_money_from_earlier_months_and_money_taken_out(self):
        june = date(2026, 6, 1)
        events = [(date(2026, 3, 5), Decimal("100000")), (date(2026, 6, 16), Decimal("-100000"))]
        self.assertEqual(services._average_balance(events, june), Decimal("50000"))


class SplitTests(SavingsCase):
    def test_only_the_other_savings_part_of_the_earnings_is_credited(self):
        p = services.interest_preview(self.month)
        self.assertEqual(p["income"], Decimal("40000"))
        self.assertEqual(p["capital"], Decimal("1000000.00"))
        self.assertEqual(p["others_average"], Decimal("400000.00"))
        self.assertEqual(p["ratio_percent"], Decimal("40.00"))
        self.assertEqual(p["others_income"], Decimal("16000"))
        self.assertEqual(p["group_income"], Decimal("24000"))      # the group's usual unit trust income stays

    def test_credit_shares_the_others_part_by_each_members_own_balance(self):
        credit = services.credit_unit_trust_interest(self.month, self.treasurer)
        amounts = {l.member_id: l.amount for l in credit.lines.all()}
        self.assertEqual(amounts[self.m1.pk], Decimal("4000"))
        self.assertEqual(amounts[self.m2.pk], Decimal("12000"))
        self.assertEqual(sum(amounts.values()), Decimal("16000"))
        self.assertEqual((credit.income, credit.others_income, credit.group_income),
                         (Decimal("40000"), Decimal("16000"), Decimal("24000")))

    def test_members_who_did_not_save_here_get_nothing(self):
        services.credit_unit_trust_interest(self.month, self.treasurer)
        self.assertFalse(MemberInterest.objects.filter(member=self.m3).exists())
        self.assertEqual(services.interest_balance(self.m3)["earned"], Decimal("0"))

    def test_regular_group_deposits_do_not_earn_other_savings_interest(self):
        self.deposit(self.m3, "900000")        # a big regular deposit
        credit = services.credit_unit_trust_interest(self.month, self.treasurer)
        self.assertEqual(credit.others_income, Decimal("16000"))
        self.assertFalse(credit.lines.filter(member=self.m3).exists())

    def test_a_late_deposit_earns_only_for_the_days_it_was_there(self):
        self.save_for(self.m3, "300000", day=self.month + timedelta(days=15))
        p = services.interest_preview(self.month)
        days = (services._month_end(self.month) - self.month).days + 1
        expected = Decimal(300000) * (days - 15) / days
        self.assertAlmostEqual(float(p["averages"][self.m3.pk]), float(expected), places=4)

    def test_money_taken_out_of_the_unit_trust_lowers_the_capital(self):
        self.tx(self.ut, "REDEEM", "500000", day=self.month + timedelta(days=15))
        p = services.interest_preview(self.month)
        days = (services._month_end(self.month) - self.month).days + 1
        expected = (Decimal(1000000) * days - Decimal(500000) * (days - 15)) / days
        self.assertAlmostEqual(float(p["capital"]), float(expected), places=1)

    def test_more_other_savings_than_invested_is_flagged_and_capped(self):
        self.save_for(self.m3, "2000000")
        p = services.interest_preview(self.month)
        self.assertEqual(p["ratio"], Decimal("1"))
        self.assertFalse(p["all_invested"])
        self.assertEqual(p["others_income"], Decimal("40000"))

    def test_reversed_unit_trust_transactions_do_not_count(self):
        entry = self.ut.transactions.filter(kind="INCOME").first()
        services.reverse_transaction(entry, self.chair, "wrong")
        self.assertEqual(services.interest_preview(self.month)["income"], Decimal("-10000"))
        with self.assertRaises(services.InvestmentError):
            services.credit_unit_trust_interest(self.month, self.treasurer)


class CreditRuleTests(SavingsCase):
    def test_nothing_to_credit_cases_are_refused(self):
        empty_month = (self.month - timedelta(days=40)).replace(day=1)
        with self.assertRaises(services.InvestmentError):
            services.credit_unit_trust_interest(empty_month, self.treasurer)           # no earnings
        OtherSavingsDeposit.objects.all().delete()
        with self.assertRaises(services.InvestmentError):
            services.credit_unit_trust_interest(self.month, self.treasurer)            # nobody saved here

    def test_future_months_are_refused(self):
        with self.assertRaises(services.InvestmentError):
            services.credit_unit_trust_interest(timezone.localdate().replace(day=1) + timedelta(days=40), self.treasurer)

    def test_only_treasurer_and_admin_credit_and_record(self):
        services.credit_unit_trust_interest(self.month, self.admin)
        for user in (self.chair, self.u1):
            with self.assertRaises(PermissionDenied):
                services.credit_unit_trust_interest(self.month, user)
            with self.assertRaises(PermissionDenied):
                self.save_for(self.m1, "1000", actor=user)

    def test_recalculating_replaces_the_month_and_never_duplicates(self):
        services.credit_unit_trust_interest(self.month, self.treasurer)
        self.tx(self.ut, "INCOME", "40000", day=self.month + timedelta(days=12))
        services.credit_unit_trust_interest(self.month, self.treasurer)
        self.assertEqual(MemberInterest.objects.filter(member=self.m1).count(), 1)
        self.assertEqual(services.interest_balance(self.m1)["earned"], Decimal("8000"))

    def test_recalculating_cannot_take_back_money_already_withdrawn(self):
        services.credit_unit_trust_interest(self.month, self.treasurer)
        services.request_withdrawal(self.m2, Decimal("12000"), self.u2)
        services.reverse_transaction(self.ut.transactions.filter(kind="INCOME").first(), self.chair, "wrong")
        self.tx(self.ut, "INCOME", "11000", day=self.month + timedelta(days=12))   # earnings fall to 1,000
        with self.assertRaises(services.InvestmentError):
            services.credit_unit_trust_interest(self.month, self.treasurer)

    def test_audited_with_the_split(self):
        services.credit_unit_trust_interest(self.month, self.treasurer)
        log = AuditLog.objects.get(new_value__event="UNIT_TRUST_INTEREST_CREDITED")
        self.assertEqual(Decimal(log.new_value["credited_to_other_savings"]), Decimal("16000"))
        self.assertEqual(Decimal(log.new_value["kept_by_the_group"]), Decimal("24000"))

    def test_deposit_rules_and_deleting(self):
        with self.assertRaises(services.InvestmentError):
            self.save_for(self.m1, "0")
        with self.assertRaises(services.InvestmentError):
            services.record_other_deposit(self.treasurer, self.m1, Decimal("1000"), timezone.localdate() + timedelta(days=1))
        d = self.save_for(self.m1, "5000", day=timezone.localdate())
        with self.assertRaises(services.InvestmentError):
            services.delete_other_deposit(d, self.treasurer, "")
        services.delete_other_deposit(d, self.treasurer, "entered twice")
        self.assertFalse(OtherSavingsDeposit.objects.filter(pk=d.pk).exists())
        self.assertTrue(AuditLog.objects.filter(new_value__event="OTHER_SAVINGS_DEPOSIT_DELETED").exists())

    def test_a_deposit_cannot_be_deleted_once_interest_was_credited_for_its_month(self):
        services.credit_unit_trust_interest(self.month, self.treasurer)
        with self.assertRaises(services.InvestmentError):
            services.delete_other_deposit(OtherSavingsDeposit.objects.first(), self.admin, "mistake")


class ProfitStaysWithTheGroupTests(SavingsCase):
    def test_the_profit_statement_keeps_the_groups_usual_unit_trust_income(self):
        from apps.reports.services import financial_position

        self.assertEqual(services.profit_figures(self.cycle)["unit_trust_income"], Decimal("40000"))
        self.assertEqual(services.profit_figures(self.cycle)["uncredited_months"], [self.month])
        services.credit_unit_trust_interest(self.month, self.treasurer)
        f = services.profit_figures(self.cycle)
        self.assertEqual(f["unit_trust_total"], Decimal("40000"))
        self.assertEqual(f["unit_trust_to_other_savings"], Decimal("16000"))
        self.assertEqual(f["unit_trust_income"], Decimal("24000"))      # only what is deducted for Other savings
        self.assertEqual(f["uncredited_months"], [])
        self.assertEqual(financial_position()["other_savings_owed"], Decimal("416000"))


class WithdrawalTests(SavingsCase):
    def setUp(self):
        super().setUp()
        services.credit_unit_trust_interest(self.month, self.treasurer)

    def test_balance_after_a_request_and_a_payment(self):
        w = services.request_withdrawal(self.m2, Decimal("5000"), self.u2)
        b = services.interest_balance(self.m2)
        self.assertEqual((b["earned"], b["pending"], b["available"]), (Decimal("12000"), Decimal("5000"), Decimal("7000")))
        services.pay_withdrawal(w, self.treasurer, "MOBILE_MONEY", "TX1")
        b = services.interest_balance(self.m2)
        self.assertEqual((b["withdrawn"], b["pending"], b["available"]), (Decimal("5000"), Decimal("0"), Decimal("7000")))

    def test_only_the_interest_is_withdrawable_not_the_savings(self):
        with self.assertRaises(services.InvestmentError):
            services.request_withdrawal(self.m2, Decimal("12001"), self.u2)

    def test_nobody_else_can_ask_for_a_members_interest(self):
        with self.assertRaises(PermissionDenied):
            services.request_withdrawal(self.m2, Decimal("1000"), self.u1)
        with self.assertRaises(PermissionDenied):
            services.request_withdrawal(self.m2, Decimal("1000"), self.treasurer)

    def test_reject_cancel_and_one_decision_only(self):
        w = services.request_withdrawal(self.m2, Decimal("5000"), self.u2)
        with self.assertRaises(services.InvestmentError):
            services.reject_withdrawal(w, self.treasurer, "")
        with self.assertRaises(PermissionDenied):
            services.cancel_withdrawal(w, self.u1)
        services.cancel_withdrawal(w, self.u2)
        self.assertEqual(services.interest_balance(self.m2)["available"], Decimal("12000"))
        with self.assertRaises(services.InvestmentError):
            services.pay_withdrawal(w, self.admin, "CASH")

    def test_paid_interest_leaves_the_cash_and_lowers_what_is_owed(self):
        from apps.reports.services import financial_position

        before = financial_position()
        services.pay_withdrawal(services.request_withdrawal(self.m2, Decimal("10000"), self.u2), self.treasurer, "CASH")
        after = financial_position()
        self.assertEqual(after["derived_cash"], before["derived_cash"] - Decimal("10000"))
        self.assertEqual(after["other_savings_owed"], before["other_savings_owed"] - Decimal("10000"))

    def test_deleting_records_needs_a_reason(self):
        w = services.request_withdrawal(self.m2, Decimal("5000"), self.u2)
        with self.assertRaises(services.InvestmentError):
            services.delete_withdrawal(w, self.treasurer, "")
        services.delete_withdrawal(w, self.treasurer, "entered by mistake")
        self.assertFalse(InterestWithdrawal.objects.exists())

    def test_deleting_credited_interest_is_blocked_after_a_withdrawal(self):
        services.request_withdrawal(self.m2, Decimal("12000"), self.u2)
        with self.assertRaises(services.InvestmentError):
            services.delete_interest_credit(InterestCredit.objects.get(), self.admin, "oops")


class PageTests(SavingsCase):
    def setUp(self):
        super().setUp()
        services.credit_unit_trust_interest(self.month, self.treasurer)

    def test_summary_numbers(self):
        s = services.other_savings(self.m2)
        self.assertEqual(s["principal"], Decimal("300000"))
        self.assertEqual(s["monthly_rate_percent"], Decimal("4.00"))        # 16,000 on 400,000
        self.assertEqual(s["estimate_monthly"], Decimal("12000"))
        self.assertEqual(s["per_100k"], Decimal("4000"))
        self.assertEqual(s["balance"]["available"], Decimal("12000"))

    def test_member_page(self):
        self.client.force_login(self.u2)
        page = self.client.get("/investments/other-savings/")
        for text in ("My Other savings", "Save more, earn more", "UGX 100,000", "Request withdrawal", "can be withdrawn at any time"):
            self.assertContains(page, text)
        self.assertNotContains(page, "Management")
        self.assertNotContains(page, self.m1.full_name)

    def test_management_page_shows_the_split(self):
        self.client.force_login(self.treasurer)
        page = self.client.get("/investments/other-savings/")
        for text in ("Stays with the group", "To Other savings", "Record Other savings from a member", "Other savings held"):
            self.assertContains(page, text)
        self.assertNotContains(page, "—")

    def test_http_flow_record_credit_request_and_pay(self):
        self.client.force_login(self.treasurer)
        self.client.post("/investments/other-savings/deposit/", {
            "member": self.m1.pk, "amount": "50000", "deposit_date": timezone.localdate().isoformat(), "payment_method": "CASH"})
        self.assertEqual(OtherSavingsDeposit.objects.filter(member=self.m1).count(), 2)
        self.client.force_login(self.u2)
        self.client.post("/investments/other-savings/withdraw/", {"amount": "4000"})
        w = InterestWithdrawal.objects.get()
        self.client.force_login(self.treasurer)
        self.client.post(f"/investments/other-savings/pay/{w.pk}/", {"payment_method": "CASH", "reference": "R1"})
        w.refresh_from_db()
        self.assertEqual(w.status, "PAID")

    def test_a_chairperson_can_see_but_not_record_credit_or_pay(self):
        self.client.force_login(self.chair)
        self.assertContains(self.client.get("/investments/other-savings/"), "Each member")
        self.client.post("/investments/other-savings/deposit/", {
            "member": self.m1.pk, "amount": "1000", "deposit_date": timezone.localdate().isoformat(), "payment_method": "CASH"})
        self.assertEqual(OtherSavingsDeposit.objects.count(), 2)
