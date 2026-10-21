"""
Loan application process: Manual v3.0 section 4, uploads, approval and access.
"""
import shutil
import tempfile
from datetime import date
from decimal import Decimal

from django.core.exceptions import PermissionDenied
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.utils import timezone

from apps.accounts.models import User
from apps.auditlog.models import AuditLog
from apps.contributions import services as cs
from apps.contributions.models import Contribution
from apps.cycles import services as cycle_services
from apps.cycles.models import Cycle, _add_months
from apps.documents.models import DocumentTemplate
from apps.members.models import Member

from . import services
from .models import LoanApplication, SignedUndertaking

PDF = b"%PDF-1.4 test file"
TMP = tempfile.mkdtemp(prefix="fcu-private-")


def pdf(name="form.pdf", content=PDF):
    return SimpleUploadedFile(name, content, content_type="application/pdf")


@override_settings(PRIVATE_MEDIA_ROOT=TMP)
class LoanTestCase(TestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(TMP, ignore_errors=True)

    @classmethod
    def setUpTestData(cls):
        mk = lambda name, role, member=None: User.objects.create_user(
            username=name, password="pw", role=role, member=member
        )
        members = list(Member.objects.order_by("member_code")[:4])
        cls.m1, cls.m2, cls.m3, cls.m4 = members
        cls.admin = mk("admin", User.Role.ADMINISTRATOR)
        cls.treasurer = mk("treasurer", User.Role.TREASURER)
        cls.chair = mk("chair", User.Role.CHAIRPERSON, cls.m3)
        cls.secretary = mk("secretary", User.Role.SECRETARY)
        cls.u1 = mk("u1", User.Role.MEMBER, cls.m1)
        cls.u2 = mk("u2", User.Role.MEMBER, cls.m2)

        # A cycle that began two months ago, so today falls in its third month
        # and a 3-month loan still finishes inside it.
        first = timezone.localdate().replace(day=1)
        cls.cycle = cycle_services.create_cycle(
            Cycle(cycle_number=1, start_date=_add_months(first, -2), duration_months=6),
            actor=cls.admin,
        )
        cycle_services.activate_cycle(cls.cycle, actor=cls.admin)

        # m1 has paid enough to be up to date and to borrow against: 600,000 deposits
        cls.deposit(cls, cls.m1, "600000")
        cls.deposit(cls, cls.m1, "30000", kind="EMERGENCY")

    def deposit(self, member, amount, kind="MONTHLY"):
        return cs.record_contribution(
            Contribution(
                member=member, cycle=self.cycle, contribution_type=kind, amount=Decimal(amount),
                period_month=self.cycle.start_date, payment_date=timezone.localdate(),
                payment_method="CASH", recorded_by=self.admin,
            ),
            actor=self.admin,
        )

    def verified_undertaking(self, member=None):
        u = services.upload_undertaking(member or self.m1, pdf("decl.pdf"), self.u1)
        return services.review_undertaking(u, self.chair, verified=True)

    def apply(self, amount="500000", months=3, **kw):
        return services.submit_application(
            self.m1, Decimal(amount), months, pdf("loan.pdf"), self.u1, **kw
        )


class ScheduleTests(LoanTestCase):
    def test_equal_instalments_clear_the_loan_exactly(self):
        rows = services.loan_schedule(Decimal("300000"), 3, date(2026, 1, 15), Decimal("5"))
        self.assertEqual(len(rows), 3)
        self.assertEqual(sum(r["principal"] for r in rows), Decimal("300000"))
        self.assertEqual(rows[-1]["balance"], Decimal("0"))
        self.assertEqual(rows[0]["instalment"], Decimal("110163"))
        self.assertEqual(rows[0]["interest"], Decimal("15000"))  # 5% of 300,000
        self.assertEqual(rows[1]["interest"], Decimal("10242"))  # 5% of the reduced balance

    def test_one_month_loan_is_principal_plus_one_months_interest(self):
        rows = services.loan_schedule(Decimal("200000"), 1, date(2026, 1, 15), Decimal("5"))
        self.assertEqual(rows[0]["instalment"], Decimal("210000"))

    def test_month_end_dates_are_clamped(self):
        rows = services.loan_schedule(Decimal("300000"), 3, date(2026, 11, 30), Decimal("5"))
        self.assertEqual([r["due_date"] for r in rows], [date(2026, 12, 30), date(2027, 1, 30), date(2027, 2, 28)])

    def test_instalments_fall_monthly(self):
        rows = services.loan_schedule(Decimal("300000"), 3, date(2026, 1, 15), Decimal("5"))
        self.assertEqual([r["due_date"] for r in rows], [date(2026, 2, 15), date(2026, 3, 15), date(2026, 4, 15)])

    def test_repayment_must_finish_inside_the_cycle(self):
        # cycle runs to 31 Dec 2026
        cycle = Cycle(start_date=date(2026, 7, 1), duration_months=6)
        cycle.end_date = date(2026, 12, 31)
        self.assertEqual(services.max_repayment_months(cycle, date(2026, 9, 15)), 3)
        self.assertEqual(services.max_repayment_months(cycle, date(2026, 10, 15)), 2)
        self.assertEqual(services.max_repayment_months(cycle, date(2026, 11, 15)), 1)
        self.assertEqual(services.max_repayment_months(cycle, date(2026, 12, 15)), 0)


class PaymentWindowTests(LoanTestCase):
    def test_a_month_is_overdue_only_after_the_10th_of_the_next_month(self):
        cycle = Cycle.objects.get(pk=self.cycle.pk)
        cycle.start_date = date(2026, 1, 1)
        cycle.save()
        cycle.refresh_from_db()
        # January's window closes on 10 February.
        on_the_10th = cs.contribution_arrears_to_date(self.m2, cycle, date(2026, 2, 10))
        on_the_11th = cs.contribution_arrears_to_date(self.m2, cycle, date(2026, 2, 11))
        self.assertEqual(on_the_10th["months_overdue"], 0)
        self.assertEqual(on_the_11th["months_overdue"], 1)
        self.assertEqual(on_the_11th["deposit_arrears"], Decimal("100000"))


class EligibilityTests(LoanTestCase):
    def passed(self, result):
        return {c["key"]: c["ok"] for c in result["checks"]}

    def test_a_member_who_is_up_to_date_with_a_declaration_is_eligible(self):
        services.upload_undertaking(self.m1, pdf("d.pdf"), self.u1)
        r = services.check_eligibility(self.m1, amount=Decimal("500000"), months=3)
        self.assertTrue(r["eligible"], r["checks"])

    def test_limit_is_100_percent_of_accumulated_deposits_not_emergency(self):
        info = services.loan_limit(self.m1)
        self.assertEqual(info["pool"], Decimal("600000"))
        self.assertEqual(info["limit"], Decimal("600000"))

    def test_amount_above_the_limit_blocks(self):
        services.upload_undertaking(self.m1, pdf("d.pdf"), self.u1)
        r = services.check_eligibility(self.m1, amount=Decimal("600001"), months=3)
        self.assertFalse(r["eligible"])
        self.assertFalse(self.passed(r)["amount"])
        r = services.check_eligibility(self.m1, amount=Decimal("600000"), months=3)
        self.assertTrue(r["eligible"])

    def test_inactive_member_cannot_borrow(self):
        services.upload_undertaking(self.m1, pdf("d.pdf"), self.u1)
        Member.objects.filter(pk=self.m1.pk).update(status=Member.Status.INACTIVE)
        self.m1.refresh_from_db()
        r = services.check_eligibility(self.m1, amount=Decimal("100000"), months=1)
        self.assertFalse(r["eligible"])
        self.assertFalse(self.passed(r)["active"])

    def test_no_declaration_blocks(self):
        r = services.check_eligibility(self.m1, amount=Decimal("100000"), months=1)
        self.assertFalse(self.passed(r)["undertaking"])
        self.assertFalse(r["eligible"])

    def test_declaration_uploaded_with_the_application_counts(self):
        r = services.check_eligibility(self.m1, amount=Decimal("100000"), months=1, assume_undertaking=True)
        self.assertTrue(self.passed(r)["undertaking"])

    def test_overdue_deposits_block(self):
        r = services.check_eligibility(self.m2, today=date(2030, 1, 1))  # nothing paid by m2
        self.assertFalse(self.passed(r)["deposits_paid"])

    def test_overdue_emergency_is_only_a_warning(self):
        self.deposit(self.m4, "600000")  # deposits cleared, emergency not paid
        services.upload_undertaking(self.m4, pdf("d.pdf"), self.u1)
        r = services.check_eligibility(self.m4, amount=Decimal("100000"), months=1, today=self.cycle.start_date.replace(day=28))
        checks = {c["key"]: c for c in r["checks"]}
        if not checks["emergency_paid"]["ok"]:
            self.assertFalse(checks["emergency_paid"]["blocking"])
        self.assertTrue(r["eligible"], r["checks"])

    def test_period_must_fit_the_cycle(self):
        services.upload_undertaking(self.m1, pdf("d.pdf"), self.u1)
        r = services.check_eligibility(self.m1, amount=Decimal("100000"), months=4)
        self.assertFalse(self.passed(r)["period"])

    def test_no_cycle_means_no_loan(self):
        cycle_services.close_cycle(self.cycle, actor=self.admin)
        r = services.check_eligibility(self.m1)
        self.assertFalse(self.passed(r)["cycle"])


class UploadValidationTests(LoanTestCase):
    def test_accepts_pdf_jpg_png(self):
        services.validate_upload(pdf())
        services.validate_upload(SimpleUploadedFile("a.jpg", b"\xff\xd8\xff\xe0data"))
        services.validate_upload(SimpleUploadedFile("a.png", b"\x89PNG\r\n\x1a\n"))

    def test_rejects_other_types_and_disguised_files(self):
        for upload in [
            SimpleUploadedFile("a.exe", b"MZ..."),
            SimpleUploadedFile("a.pdf", b"<html>not a pdf</html>"),
            SimpleUploadedFile("noextension", PDF),
        ]:
            with self.assertRaises(services.LoanError):
                services.validate_upload(upload)

    @override_settings(FCU_UPLOAD_MAX_MB=1)
    def test_rejects_oversized_files(self):
        with self.assertRaises(services.LoanError):
            services.validate_upload(SimpleUploadedFile("a.pdf", PDF + b"0" * (1024 * 1024)))


class SubmissionTests(LoanTestCase):
    def test_submission_stores_files_privately_and_snapshots_the_checks(self):
        a = self.apply(undertaking_upload=pdf("decl.pdf"))
        self.assertEqual(a.status, LoanApplication.Status.SUBMITTED)
        self.assertEqual(a.amount_requested, Decimal("500000"))
        self.assertTrue(a.eligibility_snapshot["eligible"])
        self.assertEqual(a.eligibility_snapshot["limit"], "600000")
        self.assertTrue(a.application_form.path.startswith(TMP))
        self.assertEqual(a.undertaking.status, SignedUndertaking.Status.PENDING)

    def test_submission_is_audited(self):
        a = self.apply(undertaking_upload=pdf("decl.pdf"))
        e = AuditLog.objects.filter(new_value__event="LOAN_APPLICATION_SUBMITTED").get()
        self.assertEqual(e.actor, self.u1)
        self.assertEqual(e.object_id, str(a.pk))

    def test_ineligible_member_cannot_submit_and_nothing_is_stored(self):
        with self.assertRaises(services.LoanError):
            services.submit_application(self.m1, Decimal("999999"), 3, pdf(), self.u1, undertaking_upload=pdf("d.pdf"))
        self.assertEqual(LoanApplication.objects.count(), 0)
        self.assertEqual(SignedUndertaking.objects.count(), 0)

    def test_declaration_is_required_with_the_first_application(self):
        with self.assertRaises(services.LoanError):
            self.apply()

    def test_later_applications_reuse_the_declaration_on_file(self):
        self.verified_undertaking()
        a = self.apply()
        self.assertEqual(a.undertaking.status, SignedUndertaking.Status.VERIFIED)

    def test_only_one_open_application_at_a_time(self):
        self.verified_undertaking()
        self.apply()
        with self.assertRaises(services.LoanError):
            self.apply(amount="100000")

    def test_member_can_apply_again_after_withdrawing(self):
        self.verified_undertaking()
        a = self.apply()
        services.withdraw_application(a, self.u1)
        self.apply(amount="100000")

    def test_only_the_applicant_can_withdraw(self):
        self.verified_undertaking()
        a = self.apply()
        with self.assertRaises(PermissionDenied):
            services.withdraw_application(a, self.u2)


class DecisionTests(LoanTestCase):
    def setUp(self):
        self.verified_undertaking()
        self.app = self.apply(amount="300000")

    def test_chairperson_and_administrator_can_approve(self):
        a = services.approve_application(self.app, self.admin, note="ok")
        self.assertEqual(a.status, LoanApplication.Status.APPROVED)
        self.assertEqual(a.amount_approved, Decimal("300000"))
        self.assertEqual(a.interest_rate_percent, Decimal("5"))
        self.assertEqual(len(a.approved_schedule), 3)
        self.assertEqual(a.installment_amount, Decimal("110163"))
        self.assertEqual(a.decided_by, self.admin)

    def test_treasurer_cannot_approve(self):
        with self.assertRaises(PermissionDenied):
            services.approve_application(self.app, self.treasurer)

    def test_member_cannot_approve(self):
        with self.assertRaises(PermissionDenied):
            services.approve_application(self.app, self.u2)

    def test_cannot_approve_your_own_application(self):
        # the Chairperson is also member m3 and applies for a loan of their own
        self.deposit(self.m3, "600000")
        u = services.upload_undertaking(self.m3, pdf("d.pdf"), self.chair)
        services.review_undertaking(u, self.admin, verified=True)
        own = services.submit_application(self.m3, Decimal("100000"), 1, pdf(), self.chair)
        with self.assertRaises(services.LoanError):
            services.approve_application(own, self.chair)
        services.approve_application(own, self.admin)  # someone else can

    def test_approval_needs_a_verified_declaration(self):
        SignedUndertaking.objects.filter(pk=self.app.undertaking_id).update(status="PENDING")
        with self.assertRaises(services.LoanError):
            services.approve_application(self.app, self.admin)

    def test_amount_approved_can_be_lower_but_not_higher(self):
        with self.assertRaises(services.LoanError):
            services.approve_application(self.app, self.admin, amount_approved=Decimal("400000"))
        a = services.approve_application(self.app, self.admin, amount_approved=Decimal("200000"))
        self.assertEqual(a.amount_approved, Decimal("200000"))

    def test_rejection_needs_a_reason_and_is_visible(self):
        with self.assertRaises(services.LoanError):
            services.reject_application(self.app, self.chair, "")
        a = services.reject_application(self.app, self.chair, "Insufficient liquidity")
        self.assertEqual(a.status, LoanApplication.Status.REJECTED)
        self.assertEqual(a.decision_note, "Insufficient liquidity")

    def test_a_decided_application_cannot_be_decided_again(self):
        services.approve_application(self.app, self.admin)
        with self.assertRaises(services.LoanError):
            services.reject_application(self.app, self.admin, "changed my mind")

    def test_decisions_are_audited(self):
        services.approve_application(self.app, self.admin, note="fine")
        e = AuditLog.objects.filter(new_value__event="LOAN_APPLICATION_APPROVED").get()
        self.assertEqual(e.actor, self.admin)
        self.assertEqual(e.new_value["amount_approved"], "300000")

    def test_approval_rechecks_eligibility(self):
        Member.objects.filter(pk=self.m1.pk).update(status=Member.Status.INACTIVE)
        with self.assertRaises(services.LoanError):
            services.approve_application(self.app, self.admin)


class UndertakingReviewTests(LoanTestCase):
    def test_officers_can_verify_but_members_cannot(self):
        u = services.upload_undertaking(self.m1, pdf(), self.u1)
        with self.assertRaises(PermissionDenied):
            services.review_undertaking(u, self.u2, verified=True)
        services.review_undertaking(u, self.secretary, verified=True)
        self.assertEqual(u.status, SignedUndertaking.Status.VERIFIED)

    def test_rejection_needs_a_note(self):
        u = services.upload_undertaking(self.m1, pdf(), self.u1)
        with self.assertRaises(services.LoanError):
            services.review_undertaking(u, self.chair, verified=False)

    def test_rejected_declaration_does_not_count_as_on_file(self):
        u = services.upload_undertaking(self.m1, pdf(), self.u1)
        services.review_undertaking(u, self.chair, verified=False, note="Unsigned")
        self.assertIsNone(services.current_undertaking(self.m1))


class ViewTests(LoanTestCase):
    def login(self, name):
        self.client.logout()
        self.client.login(username=name, password="pw")

    def test_member_sees_forms_to_download_and_their_status(self):
        self.login("u1")
        r = self.client.get("/loans/")
        self.assertEqual(r.status_code, 200)
        for doc in DocumentTemplate.objects.filter(is_current=True):
            self.assertContains(r, f"/documents/{doc.pk}/download/")
        self.assertContains(r, "Can I apply?")

    def test_each_member_sees_only_their_own_applications(self):
        self.verified_undertaking()
        self.apply()
        self.login("u2")
        self.assertNotContains(self.client.get("/loans/"), "LN-")

    def test_member_can_apply_through_the_form_with_uploads(self):
        self.login("u1")
        r = self.client.post(
            "/loans/apply/",
            {"amount": "400000", "period_months": "3", "application_form": pdf("loan.pdf"), "undertaking_file": pdf("decl.pdf")},
        )
        self.assertEqual(r.status_code, 302, getattr(r, "context", None) and r.context["form"].errors)
        a = LoanApplication.objects.get()
        self.assertRedirects(r, f"/loans/applications/{a.pk}/", fetch_redirect_response=False)

    def test_preview_shows_a_schedule_and_saves_nothing(self):
        self.login("u1")
        r = self.client.post("/loans/apply/", {"amount": "300000", "period_months": "3", "preview": "1"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(len(r.context["schedule"]), 3)
        self.assertEqual(LoanApplication.objects.count(), 0)

    def test_over_limit_application_is_refused_with_a_reason(self):
        self.login("u1")
        r = self.client.post(
            "/loans/apply/",
            {"amount": "900000", "period_months": "3", "application_form": pdf(), "undertaking_file": pdf("d.pdf")},
        )
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "above your limit")
        self.assertEqual(LoanApplication.objects.count(), 0)

    def test_fake_pdf_is_refused(self):
        self.login("u1")
        r = self.client.post(
            "/loans/apply/",
            {"amount": "100000", "period_months": "1",
             "application_form": SimpleUploadedFile("x.pdf", b"not a pdf"), "undertaking_file": pdf("d.pdf")},
        )
        self.assertEqual(r.status_code, 200)
        self.assertEqual(LoanApplication.objects.count(), 0)

    def test_application_and_file_access_control(self):
        self.verified_undertaking()
        a = self.apply()
        detail = f"/loans/applications/{a.pk}/"
        form_file = f"/loans/files/application/{a.pk}/"
        decl_file = f"/loans/files/undertaking/{a.undertaking_id}/"
        expectations = {
            "u1": (200, 200, 200),          # the applicant
            "u2": (403, 403, 403),          # another member
            "admin": (200, 200, 200),
            "chair": (200, 200, 200),
            "treasurer": (200, 200, 200),
            "secretary": (403, 403, 200),   # may verify declarations, not read applications
        }
        for name, (d, f, u) in expectations.items():
            self.login(name)
            self.assertEqual(self.client.get(detail).status_code, d, (name, "detail"))
            self.assertEqual(self.client.get(form_file).status_code, f, (name, "application file"))
            self.assertEqual(self.client.get(decl_file).status_code, u, (name, "declaration file"))

    def test_downloaded_file_is_the_uploaded_one(self):
        self.verified_undertaking()
        a = self.apply()
        self.login("u1")
        r = self.client.get(f"/loans/files/application/{a.pk}/")
        self.assertEqual(b"".join(r.streaming_content), PDF)
        self.assertEqual(r["X-Content-Type-Options"], "nosniff")

    def test_private_files_are_not_reachable_under_media(self):
        self.verified_undertaking()
        a = self.apply()
        self.login("admin")
        name = a.application_form.name
        self.assertEqual(self.client.get(f"/media/{name}").status_code, 404)

    def test_anonymous_is_sent_to_login(self):
        self.client.logout()
        for url in ("/loans/", "/loans/apply/", "/loans/applications/"):
            self.assertEqual(self.client.get(url).status_code, 302, url)

    def test_staff_lists_and_decisions(self):
        self.verified_undertaking()
        a = self.apply(amount="300000")
        self.login("treasurer")
        self.assertEqual(self.client.get("/loans/applications/").status_code, 200)
        self.client.post(f"/loans/applications/{a.pk}/approve/")  # Treasurer may not decide
        a.refresh_from_db()
        self.assertEqual(a.status, "SUBMITTED")
        self.login("chair")
        self.client.post(f"/loans/applications/{a.pk}/approve/", {"note": "go ahead"})
        a.refresh_from_db()
        self.assertEqual(a.status, "APPROVED")

    def test_member_sees_the_decision_and_reason(self):
        self.verified_undertaking()
        a = self.apply()
        services.reject_application(a, self.chair, "Insufficient liquidity this month")
        self.login("u1")
        r = self.client.get(f"/loans/applications/{a.pk}/")
        self.assertContains(r, "Insufficient liquidity this month")

    def test_decision_views_reject_get(self):
        self.verified_undertaking()
        a = self.apply()
        self.login("chair")
        self.assertEqual(self.client.get(f"/loans/applications/{a.pk}/approve/").status_code, 405)

    def test_declaration_review_page(self):
        u = services.upload_undertaking(self.m1, pdf(), self.u1)
        self.login("secretary")
        self.assertEqual(self.client.get("/loans/declarations/").status_code, 200)
        self.client.post(f"/loans/declarations/{u.pk}/verify/")
        u.refresh_from_db()
        self.assertEqual(u.status, "VERIFIED")
        self.login("u2")
        self.assertEqual(self.client.get("/loans/declarations/").status_code, 403)
