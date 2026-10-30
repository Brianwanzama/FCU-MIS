"""
Loans made outside the system, deleting loan records, and the automatic daily recovery.
"""
from datetime import timedelta
from decimal import Decimal

from django.core.exceptions import PermissionDenied
from django.test import override_settings
from django.utils import timezone

from apps.auditlog.models import AuditLog
from apps.contributions.models import Contribution
from apps.members.models import Member

from . import services
from . import tests as base
from .models import Loan, LoanApplication, LoanRepayment, RecoveryRun


class OffsystemLoanTests(base.LoanTestCase):
    def add(self, **kw):
        args = dict(
            actor=self.treasurer, member=self.m1, principal=Decimal("300000"), months=3,
            disbursed_on=timezone.localdate() - timedelta(days=20),
        )
        args.update(kw)
        return services.record_offsystem_loan(**args)

    def test_it_builds_the_schedule_and_balance_like_any_loan(self):
        loan = self.add()
        self.assertTrue(loan.is_offsystem)
        self.assertIsNone(loan.application)
        self.assertEqual(loan.reference, f"OFF-{loan.pk:05d}")
        self.assertEqual(loan.installments.count(), 3)
        self.assertEqual(loan.total_due, Decimal("330488"))
        self.assertEqual(loan.outstanding, loan.total_due)
        self.assertEqual(loan.display_status(), "Current")

    def test_money_already_repaid_leaves_only_the_balance(self):
        loan = self.add(already_repaid=Decimal("110163"))
        self.assertEqual(loan.repayments.count(), 1)
        self.assertEqual(loan.outstanding, loan.total_due - Decimal("110163"))

    def test_repaying_everything_settles_it(self):
        loan = self.add()
        loan_total = loan.total_due
        loan2 = self.add(already_repaid=loan_total)
        self.assertEqual(loan2.status, Loan.Status.SETTLED)

    def test_it_counts_against_the_members_loan_limit_balance(self):
        before = services.outstanding_loan_balance(self.m1)
        loan = self.add()
        self.assertEqual(services.outstanding_loan_balance(self.m1), before + loan.outstanding)

    def test_chairperson_and_administrator_may_add_but_members_and_secretary_may_not(self):
        self.assertTrue(self.add(actor=self.chair))
        self.assertTrue(self.add(actor=self.admin))
        for user in (self.u1, self.secretary):
            with self.assertRaises(PermissionDenied):
                self.add(actor=user)

    def test_bad_input_is_refused(self):
        with self.assertRaises(services.LoanError):
            self.add(principal=Decimal("0"))
        with self.assertRaises(services.LoanError):
            self.add(disbursed_on=timezone.localdate() + timedelta(days=1))
        with self.assertRaises(services.LoanError):
            self.add(already_repaid=Decimal("99999999"))
        with self.assertRaises(services.LoanError):
            self.add(months=99)

    def test_it_is_audited(self):
        loan = self.add()
        self.assertTrue(
            AuditLog.objects.filter(model_name="Loan", object_id=str(loan.pk),
                                    new_value__event="OFFSYSTEM_LOAN_ADDED").exists()
        )

    def test_an_overdue_offsystem_loan_is_recovered_from_savings(self):
        loan = self.add(disbursed_on=timezone.localdate() - timedelta(days=200), months=2)
        self.assertIn(loan, services.overdue_loans())
        with override_settings(FCU_LOAN_AUTO_RECOVERY_ENABLED=True):
            outcome = services.recover_loan_from_savings(loan)
        self.assertGreater(outcome["recovered"], 0)

    def test_the_form_page_and_post_work_for_the_treasurer(self):
        self.client.force_login(self.treasurer)
        self.assertEqual(self.client.get("/loans/offsystem/new/").status_code, 200)
        r = self.client.post("/loans/offsystem/new/", {
            "member": self.m1.pk, "principal": "200000", "period_months": 2,
            "interest_rate_percent": "5", "disbursed_on": (timezone.localdate() - timedelta(days=5)).isoformat(),
            "payment_method": "CASH", "already_repaid": "0",
        })
        self.assertEqual(r.status_code, 302)
        self.assertTrue(Loan.objects.filter(is_offsystem=True, principal=Decimal("200000")).exists())
        self.assertEqual(self.client.get("/loans/register/").status_code, 200)

    def test_a_member_cannot_open_the_form(self):
        self.client.force_login(self.u1)
        self.assertEqual(self.client.get("/loans/offsystem/new/").status_code, 403)


class DeleteLoanRecordTests(base.LoanTestCase):
    def make_loan(self):
        self.verified_undertaking()
        app = self.apply(amount="300000", months=3)
        services.approve_application(app, self.admin)
        return app, services.disburse_loan(app, self.treasurer, "CASH", "slip")

    def test_deleting_a_repayment_restores_the_balance(self):
        _, loan = self.make_loan()
        repayment = services.record_repayment(loan, Decimal("50000"), timezone.localdate(), "CASH", self.treasurer)
        owed = Loan.objects.get(pk=loan.pk).outstanding
        services.delete_repayment(repayment, self.treasurer, "entered twice")
        self.assertFalse(LoanRepayment.objects.filter(pk=repayment.pk).exists())
        self.assertEqual(Loan.objects.get(pk=loan.pk).outstanding, owed + Decimal("50000"))
        self.assertTrue(AuditLog.objects.filter(new_value__event="LOAN_REPAYMENT_DELETED").exists())

    def test_a_reason_and_permission_are_required(self):
        _, loan = self.make_loan()
        repayment = services.record_repayment(loan, Decimal("50000"), timezone.localdate(), "CASH", self.treasurer)
        with self.assertRaises(services.LoanError):
            services.delete_repayment(repayment, self.treasurer, " ")
        for user in (self.u1, self.secretary):
            with self.assertRaises(PermissionDenied):
                services.delete_repayment(repayment, user, "x")

    def test_deleting_a_loan_reopens_its_application(self):
        app, loan = self.make_loan()
        services.delete_loan(loan, self.chair, "never actually paid out")
        self.assertFalse(Loan.objects.filter(pk=loan.pk).exists())
        app.refresh_from_db()
        self.assertEqual(app.status, LoanApplication.Status.APPROVED)

    def test_an_application_with_a_loan_cannot_be_deleted_until_the_loan_is(self):
        app, loan = self.make_loan()
        with self.assertRaises(services.LoanError):
            services.delete_application(app, self.treasurer, "oops")
        services.delete_loan(loan, self.treasurer, "undo")
        services.delete_application(app, self.treasurer, "duplicate")
        self.assertFalse(LoanApplication.objects.filter(pk=app.pk).exists())

    def test_deleting_a_recovery_restores_the_members_savings(self):
        from apps.members.services import member_available_savings

        _, loan = self.make_loan()
        before = member_available_savings(self.m1)
        with override_settings(FCU_LOAN_AUTO_RECOVERY_ENABLED=True):
            outcome = services.recover_loan_from_savings(loan, today=loan.final_due_date + timedelta(days=1))
        self.assertGreater(outcome["recovered"], 0)
        recovery = LoanRepayment.objects.get(loan=loan, kind="SAVINGS_OFFSET")
        services.delete_repayment(recovery, self.admin, "wrong day")
        self.assertEqual(member_available_savings(self.m1), before)

    def test_delete_buttons_work_over_http(self):
        _, loan = self.make_loan()
        self.client.force_login(self.treasurer)
        page = self.client.get(f"/loans/loan/{loan.pk}/")
        self.assertContains(page, "Delete loan")
        r = self.client.post(f"/loans/loan/{loan.pk}/delete/", {"reason": "test"})
        self.assertRedirects(r, "/loans/register/")


class DailyRecoveryTests(base.LoanTestCase):
    def overdue_loan(self):
        self.verified_undertaking()
        app = self.apply(amount="300000", months=3)
        services.approve_application(app, self.admin)
        loan = services.disburse_loan(app, self.treasurer, "CASH", "slip")
        return loan, loan.final_due_date + timedelta(days=1)

    def test_recovery_is_on_by_default(self):
        from django.conf import settings

        self.assertTrue(settings.FCU_LOAN_AUTO_RECOVERY_ENABLED)

    def test_the_daily_check_recovers_once_a_day(self):
        loan, day = self.overdue_loan()
        run = services.run_daily_recovery_if_due(today=day)
        self.assertIsNotNone(run)
        self.assertEqual(run.loans_checked, 1)
        self.assertGreater(run.total_recovered, 0)
        self.assertEqual(run.error, "")
        self.assertIsNone(services.run_daily_recovery_if_due(today=day))  # already done today
        self.assertEqual(RecoveryRun.objects.filter(run_date=day).count(), 1)
        self.assertEqual(LoanRepayment.objects.filter(loan=loan, kind="SAVINGS_OFFSET").count(), 1)

    @override_settings(FCU_LOAN_AUTO_RECOVERY_ENABLED=False)
    def test_switched_off_it_only_looks(self):
        loan, day = self.overdue_loan()
        run = services.run_daily_recovery_if_due(today=day)
        self.assertEqual(run.total_recovered, 0)
        self.assertFalse(LoanRepayment.objects.filter(loan=loan, kind="SAVINGS_OFFSET").exists())

    @override_settings(FCU_LOAN_RECOVERY_ON_REQUEST=True)
    def test_the_first_signed_in_visit_of_the_day_triggers_it(self):
        from apps.loans import middleware

        middleware._checked_on = None
        self.client.force_login(self.admin)
        self.client.get("/dashboard/")
        self.assertTrue(RecoveryRun.objects.filter(run_date=timezone.localdate()).exists())
        middleware._checked_on = None
