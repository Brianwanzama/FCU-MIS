"""Other savings: unit trust interest credited to members every month, and withdrawing it."""
from datetime import timedelta
from decimal import Decimal

from django.core.exceptions import PermissionDenied
from django.utils import timezone

from apps.auditlog.models import AuditLog

from . import services
from .models import InterestWithdrawal, MemberInterest
from .tests import InvestmentCase


class SavingsCase(InvestmentCase):
    def setUp(self):
        self.month = timezone.localdate().replace(day=1)
        self.deposit(self.m1, "300000")
        self.deposit(self.m2, "100000")
        self.ut = self.investment("UNIT_TRUST", "Unit Trust A")
        self.tx(self.ut, "INVEST", "1000000")
        self.tx(self.ut, "INCOME", "50000")
        self.tx(self.ut, "FEE", "10000")     # 40,000 after fees
        other = self.investment("MICROFINANCE", "Venture")
        self.tx(other, "INCOME", "99999")    # never part of the unit trust interest


class CreditTests(SavingsCase):
    def test_income_for_the_month_is_unit_trust_only_less_fees(self):
        self.assertEqual(services.unit_trust_income_for_month(self.month), Decimal("40000"))

    def test_credit_shares_by_relative_contribution_and_adds_up(self):
        credit = services.credit_unit_trust_interest(self.month, self.treasurer)
        amounts = {l.member_id: l.amount for l in credit.lines.all()}
        self.assertEqual(amounts[self.m1.pk], Decimal("30000"))
        self.assertEqual(amounts[self.m2.pk], Decimal("10000"))
        self.assertEqual(sum(amounts.values()), Decimal("40000"))

    def test_nothing_to_credit_is_refused(self):
        with self.assertRaises(services.InvestmentError):
            services.credit_unit_trust_interest((self.month - timedelta(days=62)).replace(day=1), self.treasurer)

    def test_future_months_are_refused(self):
        with self.assertRaises(services.InvestmentError):
            services.credit_unit_trust_interest(self.month + timedelta(days=40), self.treasurer)

    def test_only_treasurer_and_admin_credit(self):
        services.credit_unit_trust_interest(self.month, self.admin)
        for user in (self.chair, self.u1):
            with self.assertRaises(PermissionDenied):
                services.credit_unit_trust_interest(self.month, user)

    def test_recalculating_replaces_the_month_and_never_duplicates(self):
        services.credit_unit_trust_interest(self.month, self.treasurer)
        self.tx(self.ut, "INCOME", "40000")
        services.credit_unit_trust_interest(self.month, self.treasurer)
        self.assertEqual(MemberInterest.objects.filter(member=self.m1).count(), 1)
        self.assertEqual(services.interest_balance(self.m1)["earned"], Decimal("60000"))

    def test_recalculating_cannot_take_back_money_already_withdrawn(self):
        services.credit_unit_trust_interest(self.month, self.treasurer)
        services.request_withdrawal(self.m1, Decimal("30000"), self.u1)
        entry = self.ut.transactions.filter(kind="INCOME").first()
        services.reverse_transaction(entry, self.chair, "wrong")
        with self.assertRaises(services.InvestmentError):
            services.credit_unit_trust_interest(self.month, self.treasurer)

    def test_audited(self):
        services.credit_unit_trust_interest(self.month, self.treasurer)
        self.assertTrue(AuditLog.objects.filter(new_value__event="UNIT_TRUST_INTEREST_CREDITED").exists())


class WithdrawalTests(SavingsCase):
    def setUp(self):
        super().setUp()
        services.credit_unit_trust_interest(self.month, self.treasurer)

    def test_balance_after_a_request_and_a_payment(self):
        w = services.request_withdrawal(self.m1, Decimal("12000"), self.u1)
        b = services.interest_balance(self.m1)
        self.assertEqual((b["earned"], b["pending"], b["available"]), (Decimal("30000"), Decimal("12000"), Decimal("18000")))
        services.pay_withdrawal(w, self.treasurer, "MOBILE_MONEY", "TX1")
        b = services.interest_balance(self.m1)
        self.assertEqual((b["withdrawn"], b["pending"], b["available"]), (Decimal("12000"), Decimal("0"), Decimal("18000")))

    def test_cannot_withdraw_more_than_available_or_for_someone_else(self):
        with self.assertRaises(services.InvestmentError):
            services.request_withdrawal(self.m1, Decimal("30001"), self.u1)
        with self.assertRaises(services.InvestmentError):
            services.request_withdrawal(self.m1, Decimal("0"), self.u1)
        with self.assertRaises(PermissionDenied):
            services.request_withdrawal(self.m1, Decimal("1000"), self.u2)
        with self.assertRaises(PermissionDenied):
            services.request_withdrawal(self.m1, Decimal("1000"), self.treasurer)

    def test_reject_needs_a_reason_and_returns_the_money(self):
        w = services.request_withdrawal(self.m1, Decimal("30000"), self.u1)
        with self.assertRaises(services.InvestmentError):
            services.reject_withdrawal(w, self.treasurer, "")
        services.reject_withdrawal(w, self.treasurer, "Wrong account")
        self.assertEqual(services.interest_balance(self.m1)["available"], Decimal("30000"))

    def test_member_cancels_their_own_request_only(self):
        w = services.request_withdrawal(self.m1, Decimal("5000"), self.u1)
        with self.assertRaises(PermissionDenied):
            services.cancel_withdrawal(w, self.u2)
        services.cancel_withdrawal(w, self.u1)
        self.assertEqual(services.interest_balance(self.m1)["available"], Decimal("30000"))

    def test_a_request_is_dealt_with_only_once(self):
        w = services.request_withdrawal(self.m1, Decimal("5000"), self.u1)
        services.pay_withdrawal(w, self.admin, "CASH")
        with self.assertRaises(services.InvestmentError):
            services.pay_withdrawal(w, self.admin, "CASH")

    def test_a_paid_withdrawal_reduces_the_derived_cash_and_the_amount_owed(self):
        from apps.reports.services import financial_position

        before = financial_position()
        self.assertEqual(before["other_savings_owed"], Decimal("40000"))
        services.pay_withdrawal(services.request_withdrawal(self.m1, Decimal("10000"), self.u1), self.treasurer, "CASH")
        after = financial_position()
        self.assertEqual(after["derived_cash"], before["derived_cash"] - Decimal("10000"))
        self.assertEqual(after["other_savings_owed"], Decimal("30000"))

    def test_deleting_needs_a_reason_and_keeps_a_copy(self):
        w = services.request_withdrawal(self.m1, Decimal("5000"), self.u1)
        with self.assertRaises(services.InvestmentError):
            services.delete_withdrawal(w, self.treasurer, "")
        services.delete_withdrawal(w, self.treasurer, "entered by mistake")
        self.assertFalse(InterestWithdrawal.objects.exists())
        self.assertTrue(AuditLog.objects.filter(new_value__event="INTEREST_WITHDRAWAL_DELETED").exists())

    def test_deleting_credited_interest_is_blocked_after_a_withdrawal(self):
        credit = services.InterestCredit.objects.get()
        services.request_withdrawal(self.m1, Decimal("30000"), self.u1)
        with self.assertRaises(services.InvestmentError):
            services.delete_interest_credit(credit, self.admin, "oops")


class MemberViewTests(SavingsCase):
    def setUp(self):
        super().setUp()
        services.credit_unit_trust_interest(self.month, self.treasurer)

    def test_summary_shows_the_estimate_and_what_each_extra_100k_adds(self):
        s = services.other_savings(self.m1)
        self.assertEqual(s["my_deposits"], Decimal("300000"))
        self.assertEqual(s["estimate_monthly"], Decimal("30000"))
        self.assertEqual(s["per_100k"], Decimal("10000"))
        self.assertEqual(s["balance"]["available"], Decimal("30000"))

    def test_the_member_page_has_the_balance_motivation_and_withdraw_form(self):
        self.client.force_login(self.u1)
        page = self.client.get("/investments/other-savings/")
        self.assertContains(page, "Available to withdraw")
        self.assertContains(page, "Save more, earn more")
        self.assertContains(page, "UGX 100,000")
        self.assertContains(page, "Request withdrawal")
        self.assertNotContains(page, "Management")

    def test_a_member_sees_only_their_own_figures(self):
        self.client.force_login(self.u2)
        page = self.client.get("/investments/other-savings/")
        self.assertNotContains(page, self.m1.full_name)

    def test_member_requests_and_treasurer_pays_over_http(self):
        self.client.force_login(self.u1)
        self.client.post("/investments/other-savings/withdraw/", {"amount": "10000"})
        w = InterestWithdrawal.objects.get()
        self.client.force_login(self.treasurer)
        page = self.client.get("/investments/other-savings/")
        self.assertContains(page, "Withdrawal requests (1)")
        self.client.post(f"/investments/other-savings/pay/{w.pk}/", {"payment_method": "CASH", "reference": "R1"})
        w.refresh_from_db()
        self.assertEqual(w.status, "PAID")

    def test_management_credits_a_month_from_the_page(self):
        MemberInterest.objects.all().delete()
        services.InterestCredit.objects.all().delete()
        self.client.force_login(self.treasurer)
        r = self.client.post("/investments/other-savings/credit/", {"month": self.month.isoformat()})
        self.assertEqual(r.status_code, 302)
        self.assertTrue(services.InterestCredit.objects.filter(month=self.month).exists())

    def test_a_chairperson_can_see_but_not_credit_or_pay(self):
        self.client.force_login(self.chair)
        self.assertContains(self.client.get("/investments/other-savings/"), "Each member")
        self.client.post("/investments/other-savings/credit/", {"month": self.month.isoformat()})  # already credited: no change
        self.assertEqual(services.InterestCredit.objects.count(), 1)

    def test_no_em_dashes(self):
        self.client.force_login(self.treasurer)
        self.assertNotContains(self.client.get("/investments/other-savings/"), "—")
