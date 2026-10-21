"""
Disbursement, repayments, overdue detection and automatic recovery (Manual 4.5-4.7).
"""
from datetime import timedelta
from decimal import Decimal
from io import StringIO

from django.core.exceptions import PermissionDenied
from django.core.management import call_command
from django.db import IntegrityError, transaction
from django.test import override_settings
from django.utils import timezone

from apps.auditlog.models import AuditLog
from apps.contributions import services as cs
from apps.contributions.models import Contribution
from apps.members.models import Member
from apps.members.services import member_available_savings, member_statement

from . import services
from . import tests as base
from .models import Loan, LoanApplication, LoanRepayment

pdf = base.pdf


class LendingCase(base.LoanTestCase):
    def make_loan(self, amount="300000", months=3):
        self.verified_undertaking()
        app = self.apply(amount=amount, months=months)
        services.approve_application(app, self.admin)
        return services.disburse_loan(app, self.treasurer, "CASH", "slip-1")

    def after_deadline(self, loan):
        return loan.final_due_date + timedelta(days=1)


class DisbursementTests(LendingCase):
    def test_disbursement_creates_the_loan_and_schedule(self):
        loan = self.make_loan("300000")
        self.assertEqual(loan.principal, Decimal("300000"))
        self.assertEqual(loan.installments.count(), 3)
        self.assertEqual(sum(i.principal_due for i in loan.installments.all()), Decimal("300000"))
        self.assertEqual(loan.installments.first().amount_due, Decimal("110163"))
        self.assertEqual(loan.application.status, LoanApplication.Status.DISBURSED)
        self.assertEqual(loan.outstanding, loan.total_due)
        self.assertEqual(loan.display_status(), "Current")

    def test_schedule_runs_from_the_disbursement_date(self):
        self.verified_undertaking()
        app = self.apply(amount="300000", months=1)
        services.approve_application(app, self.admin)
        loan = services.disburse_loan(app, self.treasurer, "BANK")
        self.assertEqual(loan.installments.get().due_date, services.add_months(loan.disbursed_on, 1))

    def test_only_treasurer_or_administrator_can_disburse(self):
        self.verified_undertaking()
        app = self.apply(amount="300000")
        services.approve_application(app, self.admin)
        with self.assertRaises(PermissionDenied):
            services.disburse_loan(app, self.chair, "CASH")
        with self.assertRaises(PermissionDenied):
            services.disburse_loan(app, self.u1, "CASH")

    def test_cannot_disburse_before_approval_or_twice(self):
        self.verified_undertaking()
        app = self.apply(amount="300000")
        with self.assertRaises(services.LoanError):
            services.disburse_loan(app, self.treasurer, "CASH")
        services.approve_application(app, self.admin)
        services.disburse_loan(app, self.treasurer, "CASH")
        with self.assertRaises(services.LoanError):
            services.disburse_loan(app, self.treasurer, "CASH")

    def test_disbursement_is_audited(self):
        loan = self.make_loan()
        e = AuditLog.objects.filter(new_value__event="LOAN_DISBURSED").get()
        self.assertEqual(e.actor, self.treasurer)
        self.assertEqual(e.new_value["principal"], "300000.00")
        self.assertEqual(e.new_value["reference"], "slip-1")

    def test_a_member_cannot_borrow_again_while_a_loan_is_unpaid(self):
        self.make_loan("100000")
        r = services.check_eligibility(self.m1, amount=Decimal("50000"), months=1)
        self.assertFalse({c["key"]: c["ok"] for c in r["checks"]}["no_loan_owed"])
        self.assertFalse(r["eligible"])

    def test_loan_limit_is_reduced_by_what_is_owed(self):
        loan = self.make_loan("300000")
        self.assertEqual(services.loan_limit(self.m1)["limit"], Decimal("720000") - loan.outstanding)

    def test_approved_but_unpaid_application_blocks_a_second_one(self):
        self.verified_undertaking()
        app = self.apply(amount="100000")
        services.approve_application(app, self.admin)
        with self.assertRaises(services.LoanError):
            self.apply(amount="100000")


class RepaymentTests(LendingCase):
    def setUp(self):
        self.loan = self.make_loan("300000")
        self.today = timezone.localdate()

    def pay(self, amount, **kw):
        return services.record_repayment(self.loan, Decimal(amount), self.today, "CASH", self.treasurer, **kw)

    def test_repayments_clear_the_oldest_instalment_first(self):
        self.pay("120000")
        first, second, third = self.loan.installments.all()
        self.assertEqual(first.amount_paid, Decimal("110163"))
        self.assertEqual(second.amount_paid, Decimal("9837"))
        self.assertEqual(third.amount_paid, Decimal("0"))
        self.assertEqual(self.loan.outstanding, self.loan.total_due - Decimal("120000"))

    def test_paying_everything_settles_the_loan(self):
        self.pay(self.loan.outstanding)
        self.loan.refresh_from_db()
        self.assertEqual(self.loan.status, Loan.Status.SETTLED)
        self.assertEqual(self.loan.outstanding, Decimal("0"))
        self.assertEqual(self.loan.display_status(), "Settled")

    def test_settled_loan_frees_the_member_to_borrow(self):
        self.pay(self.loan.outstanding)
        r = services.check_eligibility(self.m1, amount=Decimal("100000"), months=1)
        self.assertTrue({c["key"]: c["ok"] for c in r["checks"]}["no_loan_owed"])

    def test_cannot_overpay_or_pay_nothing(self):
        with self.assertRaises(services.LoanError):
            self.pay(self.loan.outstanding + 1)
        with self.assertRaises(services.LoanError):
            self.pay("0")

    def test_dates_are_checked(self):
        with self.assertRaises(services.LoanError):
            services.record_repayment(self.loan, Decimal("1000"), self.today + timedelta(days=1), "CASH", self.treasurer)
        with self.assertRaises(services.LoanError):
            services.record_repayment(self.loan, Decimal("1000"), self.loan.disbursed_on - timedelta(days=1), "CASH", self.treasurer)

    def test_only_treasurer_or_administrator_can_record(self):
        for user in (self.chair, self.u1):
            with self.assertRaises(PermissionDenied):
                services.record_repayment(self.loan, Decimal("1000"), self.today, "CASH", user)
        services.record_repayment(self.loan, Decimal("1000"), self.today, "CASH", self.admin)

    def test_repayment_is_audited(self):
        self.pay("50000", reference="mm-77")
        e = AuditLog.objects.filter(new_value__event="LOAN_REPAYMENT_RECORDED").get()
        self.assertEqual(e.new_value["amount"], "50000")
        self.assertEqual(e.new_value["reference"], "mm-77")

    def test_reversal_restores_the_balance_and_reopens_a_settled_loan(self):
        r = self.pay(self.loan.outstanding)
        services.reverse_repayment(r, self.chair, "Cheque bounced")
        self.loan.refresh_from_db()
        self.assertEqual(self.loan.status, Loan.Status.ACTIVE)
        self.assertEqual(self.loan.outstanding, self.loan.total_due)
        r.refresh_from_db()
        self.assertIsNotNone(r.reversed_at)  # kept, not deleted
        self.assertTrue(LoanRepayment.objects.filter(pk=r.pk).exists())

    def test_reversal_rules(self):
        r = self.pay("50000")
        with self.assertRaises(PermissionDenied):
            services.reverse_repayment(r, self.treasurer, "x")  # Treasurer cannot
        with self.assertRaises(services.LoanError):
            services.reverse_repayment(r, self.chair, "")  # reason needed
        admin_recorded = services.record_repayment(self.loan, Decimal("1000"), self.today, "CASH", self.admin)
        with self.assertRaises(services.LoanError):
            services.reverse_repayment(admin_recorded, self.admin, "own entry")  # not your own
        services.reverse_repayment(r, self.admin, "Wrong member")
        with self.assertRaises(services.LoanError):
            services.reverse_repayment(r, self.chair, "again")

    def test_reversal_is_audited(self):
        r = self.pay("50000")
        services.reverse_repayment(r, self.chair, "Wrong member")
        e = AuditLog.objects.filter(new_value__event="LOAN_REPAYMENT_REVERSED").get()
        self.assertEqual(e.new_value["reason"], "Wrong member")


class OverdueAndRecoveryTests(LendingCase):
    def test_a_loan_is_overdue_only_after_its_agreed_period(self):
        loan = self.make_loan("300000")
        self.assertEqual(services.overdue_loans(loan.final_due_date), [])
        self.assertEqual(services.overdue_loans(self.after_deadline(loan)), [loan])

    def test_a_missed_instalment_is_overdue_but_not_recoverable(self):
        loan = self.make_loan("300000")
        first = loan.installments.first().due_date
        self.assertEqual(loan.display_status(first + timedelta(days=1)), "Overdue")
        out = services.recover_loan_from_savings(loan, today=first + timedelta(days=1))
        self.assertEqual(out["recovered"], Decimal("0"))

    def test_full_recovery_when_savings_cover_the_debt(self):
        loan = self.make_loan("300000")
        owed = loan.outstanding
        before = member_available_savings(self.m1)
        out = services.recover_loan_from_savings(loan, today=self.after_deadline(loan))
        loan.refresh_from_db()
        self.assertEqual(out["recovered"], owed)
        self.assertEqual(loan.status, Loan.Status.SETTLED)
        self.assertEqual(loan.outstanding, Decimal("0"))
        self.assertEqual(member_available_savings(self.m1), before - owed)

    def test_recovery_is_a_separate_transaction_and_keeps_the_deposits(self):
        loan = self.make_loan("300000")
        deposits = list(Contribution.objects.filter(member=self.m1).values_list("pk", "amount"))
        services.recover_loan_from_savings(loan, today=self.after_deadline(loan))
        self.assertEqual(list(Contribution.objects.filter(member=self.m1).values_list("pk", "amount")), deposits)
        offset = LoanRepayment.objects.get(kind="SAVINGS_OFFSET")
        self.assertEqual(offset.loan, loan)
        self.assertIsNone(offset.recorded_by)  # automatic

    def test_partial_recovery_leaves_the_rest_outstanding(self):
        loan = self.make_loan("720000")
        out = services.recover_loan_from_savings(loan, today=self.after_deadline(loan))
        loan.refresh_from_db()
        self.assertEqual(out["recovered"], Decimal("600000"))  # all deposits
        self.assertEqual(loan.status, Loan.Status.DEFAULTED)
        self.assertEqual(loan.outstanding, loan.total_due - Decimal("600000"))
        self.assertEqual(member_available_savings(self.m1), Decimal("0"))
        self.assertEqual(loan.display_status(), "Defaulted")

    def test_emergency_contributions_are_never_recovered(self):
        loan = self.make_loan("720000")
        services.recover_loan_from_savings(loan, today=self.after_deadline(loan))
        emergency = Contribution.objects.get(member=self.m1, contribution_type="EMERGENCY")
        self.assertEqual(emergency.amount, Decimal("30000"))
        self.assertIsNone(emergency.reversed_at)
        self.assertEqual(cs.member_balance(self.m1, self.cycle)["emergency_paid"], Decimal("30000"))

    def test_savings_from_all_cycles_are_used(self):
        loan = self.make_loan("300000")
        self.assertEqual(services.recovery_preview(loan)["savings"], Decimal("600000"))

    def test_running_twice_never_recovers_twice(self):
        loan = self.make_loan("720000")
        day = self.after_deadline(loan)
        services.recover_loan_from_savings(loan, today=day)
        again = services.recover_loan_from_savings(loan, today=day)
        self.assertEqual(again["recovered"], Decimal("0"))
        self.assertEqual(LoanRepayment.objects.filter(kind="SAVINGS_OFFSET").count(), 1)

    def test_database_forbids_two_live_recoveries_in_one_day(self):
        loan = self.make_loan("720000")
        day = self.after_deadline(loan)
        services.recover_loan_from_savings(loan, today=day)
        with self.assertRaises(IntegrityError), transaction.atomic():
            LoanRepayment.objects.create(
                loan=loan, kind="SAVINGS_OFFSET", amount=Decimal("1"), payment_date=day,
                recovery_date=day, cycle=self.cycle,
            )

    def test_recovery_is_audited_with_before_and_after(self):
        loan = self.make_loan("300000")
        owed = loan.outstanding
        services.recover_loan_from_savings(loan, today=self.after_deadline(loan))
        e = AuditLog.objects.filter(new_value__event="LOAN_RECOVERED_FROM_SAVINGS").get()
        self.assertEqual(Decimal(e.new_value["recovered"]), owed)
        self.assertEqual(Decimal(e.new_value["outstanding_after"]), Decimal("0"))
        self.assertEqual(Decimal(e.new_value["savings_before"]), Decimal("600000"))
        self.assertEqual(Decimal(e.new_value["savings_after"]), Decimal("600000") - owed)
        self.assertIn("Manual", e.new_value["policy"])
        self.assertTrue(e.new_value["automatic"])
        self.assertTrue(AuditLog.objects.filter(new_value__event="LOAN_DEFAULTED").exists())

    def test_with_no_savings_left_nothing_more_is_taken_until_new_deposits_arrive(self):
        loan = self.make_loan("720000")
        day = self.after_deadline(loan)
        services.recover_loan_from_savings(loan, today=day)  # takes all 600,000
        next_day = day + timedelta(days=1)
        nothing = services.recover_loan_from_savings(loan, today=next_day)
        self.assertEqual(nothing["recovered"], Decimal("0"))
        self.assertIn("no savings", nothing["skipped"])
        self.deposit(self.m1, "50000")
        more = services.recover_loan_from_savings(loan, today=next_day + timedelta(days=1))
        self.assertEqual(more["recovered"], Decimal("50000"))

    # ---- the scheduled job and the policy gate -------------------------------------
    def test_automatic_recovery_is_off_by_default_and_refuses_to_run(self):
        loan = self.make_loan("300000")
        with self.assertRaises(services.LoanError):
            services.run_recovery(today=self.after_deadline(loan))
        self.assertEqual(LoanRepayment.objects.filter(kind="SAVINGS_OFFSET").count(), 0)

    def test_dry_run_reports_without_changing_anything(self):
        loan = self.make_loan("300000")
        results = services.run_recovery(today=self.after_deadline(loan), dry_run=True)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["preview"]["recoverable"], loan.outstanding)
        self.assertEqual(LoanRepayment.objects.filter(kind="SAVINGS_OFFSET").count(), 0)

    @override_settings(FCU_LOAN_AUTO_RECOVERY_ENABLED=True)
    def test_enabled_run_recovers_and_a_rerun_is_harmless(self):
        loan = self.make_loan("300000")
        day = self.after_deadline(loan)
        first = services.run_recovery(today=day)
        second = services.run_recovery(today=day)
        self.assertEqual(first[0]["recovered"], first[0]["loan"].total_due)
        self.assertEqual(second, [])  # settled loans are no longer overdue
        self.assertEqual(LoanRepayment.objects.filter(kind="SAVINGS_OFFSET").count(), 1)

    def test_management_command_defaults_to_a_dry_run_when_disabled(self):
        self.make_loan("300000")
        out = StringIO()
        call_command("recover_overdue_loans", stdout=out)
        self.assertEqual(LoanRepayment.objects.filter(kind="SAVINGS_OFFSET").count(), 0)

    @override_settings(FCU_LOAN_AUTO_RECOVERY_ENABLED=True)
    def test_management_command_runs_with_nobody_logged_in(self):
        loan = self.make_loan("300000")
        late = self.after_deadline(loan)
        # The command uses "today"; make the loan genuinely overdue by moving its schedule back.
        loan.installments.update(due_date=timezone.localdate() - timedelta(days=30))
        out = StringIO()
        call_command("recover_overdue_loans", stdout=out)
        self.assertIn("recovered", out.getvalue())
        loan.refresh_from_db()
        self.assertEqual(loan.status, Loan.Status.SETTLED)
        self.assertIsNone(LoanRepayment.objects.get(kind="SAVINGS_OFFSET").recorded_by)

    # ---- effects on the member ------------------------------------------------------
    def test_recovery_makes_the_member_inactive_until_they_pay_up(self):
        loan = self.make_loan("720000")
        services.recover_loan_from_savings(loan, today=self.after_deadline(loan))
        self.m1.refresh_from_db()
        self.assertEqual(self.m1.status, Member.Status.INACTIVE)
        self.assertEqual(self.m1.inactive_reason, Member.InactiveReason.LOAN_RECOVERY)
        self.deposit(self.m1, "300000")
        self.m1.refresh_from_db()
        self.assertEqual(self.m1.status, Member.Status.ACTIVE)
        self.assertEqual(self.m1.inactive_reason, "")

    def test_recovery_reduces_the_cycle_contribution_position_not_the_deposits(self):
        loan = self.make_loan("720000")
        services.recover_loan_from_savings(loan, today=self.after_deadline(loan))
        summary = cs.member_contribution_summary(self.m1, self.cycle)
        self.assertEqual(summary["monthly_paid"], Decimal("600000"))
        self.assertEqual(summary["monthly_offsets"], Decimal("600000"))
        self.assertEqual(summary["monthly_balance"], Decimal("600000"))  # target 600k, credit 0

    def test_statement_shows_the_offset(self):
        loan = self.make_loan("720000")
        services.recover_loan_from_savings(loan, today=self.after_deadline(loan))
        s = member_statement(self.m1, self.cycle)
        self.assertEqual(len(s["offsets"]), 1)
        self.assertEqual(s["deposits_offsets"], Decimal("600000"))
        self.assertEqual(s["deposits_paid"], Decimal("600000"))  # originals untouched

    def test_withdrawn_member_is_not_touched_by_recovery_status(self):
        loan = self.make_loan("720000")
        Member.objects.filter(pk=self.m1.pk).update(status=Member.Status.WITHDRAWN)
        services.recover_loan_from_savings(loan, today=self.after_deadline(loan))
        self.m1.refresh_from_db()
        self.assertEqual(self.m1.status, Member.Status.WITHDRAWN)

    def test_reversing_a_recovery_restores_savings_and_the_debt(self):
        loan = self.make_loan("300000")
        services.recover_loan_from_savings(loan, today=self.after_deadline(loan))
        offset = LoanRepayment.objects.get(kind="SAVINGS_OFFSET")
        services.reverse_repayment(offset, self.chair, "Recovered in error")
        loan.refresh_from_db()
        self.assertEqual(member_available_savings(self.m1), Decimal("600000"))
        self.assertEqual(loan.outstanding, loan.total_due)
        self.assertEqual(loan.status, Loan.Status.DEFAULTED)  # still past its period

    def test_closing_the_cycle_warns_about_unsettled_loans(self):
        self.make_loan("300000")
        text = " ".join(self.cycle.blocking_issues_for_close())
        self.assertIn("not settled", text)


class LendingViewTests(LendingCase):
    def login(self, name):
        self.client.logout()
        self.client.login(username=name, password="pw")

    def test_loan_page_access(self):
        loan = self.make_loan("300000")
        url = f"/loans/loan/{loan.pk}/"
        for name, code in [("u1", 200), ("u2", 403), ("admin", 200), ("chair", 200), ("treasurer", 200), ("secretary", 403)]:
            self.login(name)
            self.assertEqual(self.client.get(url).status_code, code, name)

    def test_member_sees_their_loan_and_status_on_the_loans_page(self):
        loan = self.make_loan("300000")
        self.login("u1")
        r = self.client.get("/loans/")
        self.assertContains(r, loan.reference)
        self.assertContains(r, "Current")

    def test_register_is_for_officers(self):
        self.make_loan("300000")
        self.login("u1")
        self.assertEqual(self.client.get("/loans/register/").status_code, 403)
        self.login("treasurer")
        self.assertEqual(self.client.get("/loans/register/").status_code, 200)

    def test_treasurer_disburses_and_records_a_repayment_through_the_pages(self):
        self.verified_undertaking()
        app = self.apply(amount="300000")
        services.approve_application(app, self.admin)
        self.login("treasurer")
        r = self.client.post(f"/loans/applications/{app.pk}/disburse/", {"payment_method": "CASH", "reference": "r1"})
        loan = Loan.objects.get()
        self.assertRedirects(r, f"/loans/loan/{loan.pk}/", fetch_redirect_response=False)
        self.client.post(
            f"/loans/loan/{loan.pk}/repay/",
            {"amount": "50000", "payment_date": timezone.localdate().isoformat(), "payment_method": "CASH"},
        )
        self.assertEqual(loan.total_paid, Decimal("50000"))

    def test_chairperson_cannot_disburse_or_record_repayments(self):
        self.verified_undertaking()
        app = self.apply(amount="300000")
        services.approve_application(app, self.admin)
        self.login("chair")
        self.client.post(f"/loans/applications/{app.pk}/disburse/", {"payment_method": "CASH"})
        self.assertEqual(Loan.objects.count(), 0)

    def test_reversal_through_the_page_needs_an_approver(self):
        loan = self.make_loan("300000")
        r = services.record_repayment(loan, Decimal("50000"), timezone.localdate(), "CASH", self.treasurer)
        self.login("treasurer")
        self.client.post(f"/loans/repayments/{r.pk}/reverse/", {"reason": "x"})
        r.refresh_from_db()
        self.assertIsNone(r.reversed_at)
        self.login("chair")
        self.client.post(f"/loans/repayments/{r.pk}/reverse/", {"reason": "Wrong member"})
        r.refresh_from_db()
        self.assertIsNotNone(r.reversed_at)

    def test_recovery_button_is_refused_while_the_policy_is_off(self):
        loan = self.make_loan("300000")
        loan.installments.update(due_date=timezone.localdate() - timedelta(days=30))
        self.login("admin")
        self.client.post("/loans/recovery/run/")
        self.assertEqual(LoanRepayment.objects.filter(kind="SAVINGS_OFFSET").count(), 0)

    @override_settings(FCU_LOAN_AUTO_RECOVERY_ENABLED=True)
    def test_recovery_button_works_for_the_chairperson_when_enabled(self):
        loan = self.make_loan("300000")
        loan.installments.update(due_date=timezone.localdate() - timedelta(days=30))
        self.login("treasurer")
        self.client.post("/loans/recovery/run/")
        self.assertEqual(LoanRepayment.objects.filter(kind="SAVINGS_OFFSET").count(), 0)  # Treasurer cannot
        self.login("chair")
        self.client.post("/loans/recovery/run/")
        self.assertEqual(LoanRepayment.objects.filter(kind="SAVINGS_OFFSET").count(), 1)

    def test_member_dashboard_shows_the_real_loan_balance(self):
        loan = self.make_loan("300000")
        self.login("u1")
        r = self.client.get(f"/members/{self.m1.member_code}/")
        self.assertEqual(r.context["summary"]["loan"]["balance"], loan.outstanding)

    def test_statement_pdf_with_a_recovery(self):
        loan = self.make_loan("720000")
        services.recover_loan_from_savings(loan, today=self.after_deadline(loan))
        self.login("u1")
        r = self.client.get(f"/members/{self.m1.member_code}/statement.pdf?cycle={self.cycle.pk}")
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.content.startswith(b"%PDF"))
