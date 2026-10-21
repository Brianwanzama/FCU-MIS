"""
Financial-calculation tests: deposits and emergency contributions are separate
obligations, and targets come from the cycle configuration.
"""
from datetime import date
from decimal import Decimal

from django.core.exceptions import PermissionDenied
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

    def test_target_is_deposits_only_emergency_is_not_part_of_it(self):
        self.pay(M.MONTHLY, "500000")
        self.assertFalse(services.member_balance(self.member, self.cycle)["eligible"])
        self.pay(M.MONTHLY, "100000")
        b = services.member_balance(self.member, self.cycle)
        self.assertTrue(b["eligible"])
        self.assertEqual(b["emergency_balance"], Decimal("30000"))  # still owed, separately

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
        self.pay(self.partial, M.MONTHLY, "100000")

        cycle_services.close_cycle(self.cycle, actor=self.actor)

        self.assertEqual(self.status(self.full), Member.Status.ACTIVE)
        self.assertEqual(self.status(self.partial), Member.Status.INACTIVE)
        self.assertEqual(
            self.partial.inactive_reason, Member.InactiveReason.CONTRIBUTION_ARREARS
        )

    def test_emergency_contributions_are_not_part_of_the_target(self):
        self.pay(self.full, M.MONTHLY, "600000")  # no emergency paid at all
        cycle_services.close_cycle(self.cycle, actor=self.actor)
        self.assertEqual(self.status(self.full), Member.Status.ACTIVE)

    def test_emergency_alone_does_not_meet_the_target(self):
        self.pay(self.partial, M.EMERGENCY, "30000")
        cycle_services.close_cycle(self.cycle, actor=self.actor)
        self.assertEqual(self.status(self.partial), Member.Status.INACTIVE)

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


class ArrearsSettlementTests(TestCase):
    """Paying off a closed cycle's arrears updates status automatically."""

    @classmethod
    def setUpTestData(cls):
        cls.actor = User.objects.create_user(
            username="admin2", password="x", role=User.Role.ADMINISTRATOR
        )
        cls.old = cycle_services.create_cycle(
            Cycle(cycle_number=1, start_date=date(2026, 1, 1), duration_months=6),
            actor=cls.actor,
        )
        cycle_services.activate_cycle(cls.old, actor=cls.actor)
        cls.member = Member.objects.order_by("member_code").first()
        cls.withdrawn = Member.objects.order_by("member_code")[1]
        Member.objects.filter(pk=cls.withdrawn.pk).update(status=Member.Status.WITHDRAWN)
        cls.record(cls, cls.old, "500000")  # 100,000 short
        cls.paid_in_full = Member.objects.order_by("member_code")[2]
        cls.record(cls, cls.old, "600000", member=cls.paid_in_full)
        cycle_services.close_cycle(cls.old, actor=cls.actor)
        cls.new = cycle_services.create_cycle(
            Cycle(cycle_number=2, start_date=date(2026, 7, 1), duration_months=6),
            actor=cls.actor,
        )
        cycle_services.activate_cycle(cls.new, actor=cls.actor)

    def record(self, cycle, amount, settles=None, member=None, month=date(2026, 1, 1)):
        return services.record_contribution(
            Contribution(
                member=member or self.member, cycle=cycle, settles_cycle=settles,
                contribution_type=M.MONTHLY, amount=Decimal(amount),
                period_month=month, payment_date=date(2026, 1, 5),
                payment_method="CASH", recorded_by=self.actor,
            ),
            actor=self.actor,
        )

    def status(self):
        self.member.refresh_from_db()
        return self.member.status

    def test_member_who_missed_the_cycle_is_inactive(self):
        self.assertEqual(self.status(), Member.Status.INACTIVE)

    def test_paying_the_arrears_reactivates_the_member_automatically(self):
        self.record(self.new, "100000", settles=self.old)
        self.assertEqual(self.status(), Member.Status.ACTIVE)
        b = services.member_balance(self.member, self.old)
        self.assertEqual(b["monthly_balance"], Decimal("0"))

    def test_part_payment_of_arrears_keeps_the_member_inactive(self):
        self.record(self.new, "60000", settles=self.old)
        self.assertEqual(self.status(), Member.Status.INACTIVE)
        self.record(self.new, "40000", settles=self.old)
        self.assertEqual(self.status(), Member.Status.ACTIVE)

    def test_reversing_the_arrears_payment_makes_the_member_inactive_again(self):
        c = self.record(self.new, "100000", settles=self.old)
        self.assertEqual(self.status(), Member.Status.ACTIVE)
        chair = User.objects.create_user(
            username="chair0", password="x", role=User.Role.CHAIRPERSON
        )
        r = services.request_reversal(c, self.actor, "Paid in error")
        services.approve_reversal(r, chair)
        self.assertEqual(self.status(), Member.Status.INACTIVE)

    def test_arrears_payment_does_not_count_towards_the_new_cycle(self):
        self.record(self.new, "100000", settles=self.old)
        b = services.member_balance(self.member, self.new)
        self.assertEqual(b["monthly_paid"], Decimal("0"))
        self.assertEqual(services.cycle_contribution_summary(self.new)["collected"], Decimal("0"))

    def test_cannot_pay_more_than_the_arrears(self):
        with self.assertRaises(services.ContributionError):
            self.record(self.new, "150000", settles=self.old)

    def test_cannot_settle_a_cycle_with_no_arrears(self):
        other = self.paid_in_full
        with self.assertRaises(services.ContributionError):
            self.record(self.new, "1000", settles=self.old, member=other)

    def test_cannot_settle_the_active_cycle(self):
        with self.assertRaises(Exception):
            self.record(self.new, "1000", settles=self.new, month=date(2026, 7, 1))

    def test_a_withdrawn_member_is_not_reactivated_by_paying_arrears(self):
        from apps.members.services import reassess_after_arrears_payment

        reassess_after_arrears_payment(self.withdrawn, self.old, actor=self.actor)
        self.withdrawn.refresh_from_db()
        self.assertEqual(self.withdrawn.status, Member.Status.WITHDRAWN)


class EditAndReversalTests(TestCase):
    """Editing and reversing contributions: permissions, audit trail, balances."""

    @classmethod
    def setUpTestData(cls):
        mk = lambda name, role: User.objects.create_user(username=name, password="pw", role=role)
        cls.admin = mk("admin", User.Role.ADMINISTRATOR)
        cls.treasurer = mk("treasurer", User.Role.TREASURER)
        cls.chair = mk("chair", User.Role.CHAIRPERSON)
        cls.secretary = mk("secretary", User.Role.SECRETARY)
        cls.plain = mk("plain", User.Role.MEMBER)
        cls.cycle = cycle_services.create_cycle(
            Cycle(cycle_number=1, start_date=date(2026, 1, 1), duration_months=6),
            actor=cls.admin,
        )
        cycle_services.activate_cycle(cls.cycle, actor=cls.admin)
        cls.member = Member.objects.order_by("member_code").first()

    def make(self, amount="100000"):
        return services.record_contribution(
            Contribution(
                member=self.member, cycle=self.cycle, contribution_type=M.MONTHLY,
                amount=Decimal(amount), period_month=date(2026, 1, 1),
                payment_date=date(2026, 1, 5), payment_method="CASH",
                recorded_by=self.treasurer,
            ),
            actor=self.treasurer,
        )

    def paid(self):
        return services.member_balance(self.member, self.cycle)["monthly_paid"]

    # ---- permissions -------------------------------------------------
    def test_permission_matrix(self):
        expect = {
            "admin": (True, True, True, True),
            "treasurer": (True, True, True, False),
            "chair": (True, True, True, True),
            "secretary": (False, False, False, False),
            "plain": (False, False, False, False),
        }
        for name, (view, edit, request, approve) in expect.items():
            u = User.objects.get(username=name)
            self.assertEqual(
                (u.has_financial_view_access, u.can_edit_contributions,
                 u.can_request_reversal, u.can_approve_reversal),
                (view, edit, request, approve), name,
            )

    def test_chairperson_does_not_record_but_treasurer_does(self):
        self.assertFalse(self.chair.has_financial_write_access)
        self.assertTrue(self.treasurer.has_financial_write_access)

    # ---- editing -----------------------------------------------------
    def test_edit_logs_old_new_user_and_reason_and_recalculates(self):
        c = self.make("100000")
        old = services._snapshot(c)
        c.amount = Decimal("80000")
        services.update_contribution(c, old, actor=self.treasurer, reason="Typo in amount")
        self.assertEqual(self.paid(), Decimal("80000"))

        from apps.auditlog.models import AuditLog
        e = AuditLog.objects.filter(model_name="Contribution", object_id=str(c.pk), action="UPDATE").latest("pk")
        self.assertEqual(e.old_value["amount"], "100000.00")
        self.assertEqual(e.new_value["amount"], "80000.00")
        self.assertEqual(e.new_value["reason"], "Typo in amount")
        self.assertEqual(e.actor, self.treasurer)
        self.assertIsNotNone(e.timestamp)

    def test_edit_requires_a_reason(self):
        c = self.make()
        with self.assertRaises(services.ContributionError):
            services.update_contribution(c, services._snapshot(c), actor=self.treasurer, reason="  ")

    def test_secretary_cannot_edit(self):
        c = self.make()
        with self.assertRaises(PermissionDenied):
            services.update_contribution(c, services._snapshot(c), actor=self.secretary, reason="x")

    def test_chairperson_can_edit(self):
        c = self.make()
        c.remarks = "checked"
        services.update_contribution(c, services._snapshot(c), actor=self.chair, reason="Add remark")

    def test_closed_cycle_contribution_cannot_be_edited(self):
        c = self.make()
        cycle_services.close_cycle(self.cycle, actor=self.admin)
        c.refresh_from_db()
        with self.assertRaises(services.ContributionError):
            services.update_contribution(c, services._snapshot(c), actor=self.admin, reason="x")

    # ---- reversal ----------------------------------------------------
    def test_reversal_does_nothing_until_approved(self):
        c = self.make("100000")
        services.request_reversal(c, self.treasurer, "Duplicate entry")
        self.assertEqual(self.paid(), Decimal("100000"))

    def test_approved_reversal_removes_it_from_balances_but_keeps_the_record(self):
        c = self.make("100000")
        r = services.request_reversal(c, self.treasurer, "Duplicate entry")
        services.approve_reversal(r, self.chair, "Confirmed")
        c.refresh_from_db()
        self.assertTrue(c.is_reversed)
        self.assertEqual(self.paid(), Decimal("0"))
        self.assertTrue(Contribution.objects.filter(pk=c.pk).exists())
        self.assertEqual(services.cycle_contribution_summary(self.cycle)["collected"], Decimal("0"))

    def test_reversal_is_recoverable_in_the_audit_history(self):
        from apps.auditlog.models import AuditLog

        c = self.make("100000")
        r = services.request_reversal(c, self.treasurer, "Duplicate entry")
        services.approve_reversal(r, self.admin)
        events = [
            e.new_value.get("event")
            for e in AuditLog.objects.filter(model_name="Contribution", object_id=str(c.pk))
        ]
        self.assertIn("REVERSAL_REQUESTED", events)
        self.assertIn("CONTRIBUTION_REVERSED", events)
        reversed_entry = AuditLog.objects.filter(
            new_value__event="CONTRIBUTION_REVERSED", object_id=str(c.pk)
        ).get()
        self.assertEqual(reversed_entry.old_value["amount"], "100000.00")  # original kept

    def test_treasurer_cannot_approve(self):
        c = self.make()
        r = services.request_reversal(c, self.treasurer, "x")
        with self.assertRaises(PermissionDenied):
            services.approve_reversal(r, self.treasurer)

    def test_requester_cannot_approve_their_own_request(self):
        c = self.make()
        r = services.request_reversal(c, self.admin, "x")
        with self.assertRaises(services.ContributionError):
            services.approve_reversal(r, self.admin)

    def test_rejected_reversal_leaves_the_payment_and_allows_editing_again(self):
        c = self.make("100000")
        r = services.request_reversal(c, self.treasurer, "x")
        services.reject_reversal(r, self.chair, "Payment is genuine")
        self.assertEqual(self.paid(), Decimal("100000"))
        c.amount = Decimal("90000")
        services.update_contribution(c, services._snapshot(c), actor=self.treasurer, reason="ok")

    def test_rejecting_needs_a_note(self):
        c = self.make()
        r = services.request_reversal(c, self.treasurer, "x")
        with self.assertRaises(services.ContributionError):
            services.reject_reversal(r, self.chair, "")

    def test_no_second_request_or_edit_while_pending(self):
        c = self.make()
        services.request_reversal(c, self.treasurer, "x")
        with self.assertRaises(services.ContributionError):
            services.request_reversal(c, self.chair, "again")
        with self.assertRaises(services.ContributionError):
            services.update_contribution(c, services._snapshot(c), actor=self.treasurer, reason="x")

    def test_a_decided_request_cannot_be_decided_twice(self):
        c = self.make()
        r = services.request_reversal(c, self.treasurer, "x")
        services.approve_reversal(r, self.chair)
        with self.assertRaises(services.ContributionError):
            services.approve_reversal(r, self.admin)

    def test_reversal_requires_a_reason(self):
        c = self.make()
        with self.assertRaises(services.ContributionError):
            services.request_reversal(c, self.treasurer, "")

    # ---- through the web interface -------------------------------------
    def test_views_enforce_roles(self):
        c = self.make()
        urls = [
            f"/contributions/{c.pk}/",
            f"/contributions/{c.pk}/edit/",
            f"/contributions/{c.pk}/reverse/",
            "/contributions/reversals/",
        ]
        for name, allowed in [("chair", True), ("treasurer", True), ("secretary", False), ("plain", False)]:
            self.client.logout()
            self.client.login(username=name, password="pw")
            for url in urls:
                code = self.client.get(url).status_code
                self.assertEqual(code, 200 if allowed else 302, (name, url, code))

    def test_only_writers_can_open_the_record_form(self):
        self.client.login(username="chair", password="pw")
        self.assertEqual(self.client.get("/contributions/new/").status_code, 302)
        self.client.login(username="treasurer", password="pw")
        self.assertEqual(self.client.get("/contributions/new/").status_code, 200)

    def test_edit_through_the_form_requires_a_reason_and_saves(self):
        c = self.make("100000")
        self.client.login(username="treasurer", password="pw")
        data = {
            "amount": "80000", "period_month": "2026-01-01", "payment_date": "2026-01-05",
            "payment_method": "CASH", "reference": "", "remarks": "", "reason": "",
        }
        r = self.client.post(f"/contributions/{c.pk}/edit/", data)
        self.assertEqual(r.status_code, 200)  # form re-shown with an error
        data["reason"] = "Typo"
        r = self.client.post(f"/contributions/{c.pk}/edit/", data)
        self.assertEqual(r.status_code, 302)
        c.refresh_from_db()
        self.assertEqual(c.amount, Decimal("80000"))

    def test_reversal_flow_through_the_views(self):
        c = self.make("100000")
        self.client.login(username="treasurer", password="pw")
        self.client.post(f"/contributions/{c.pk}/reverse/", {"reason": "Duplicate"})
        rev = c.reversals.get()
        # the Treasurer cannot approve
        self.client.post(f"/contributions/reversals/{rev.pk}/approve/")
        rev.refresh_from_db()
        self.assertEqual(rev.status, "PENDING")
        # the Chairperson can
        self.client.login(username="chair", password="pw")
        self.client.post(f"/contributions/reversals/{rev.pk}/approve/", {"note": "ok"})
        rev.refresh_from_db()
        self.assertEqual(rev.status, "APPROVED")
        c.refresh_from_db()
        self.assertTrue(c.is_reversed)

    def test_decision_views_reject_get_requests(self):
        c = self.make()
        rev = services.request_reversal(c, self.treasurer, "x")
        self.client.login(username="chair", password="pw")
        self.assertEqual(
            self.client.get(f"/contributions/reversals/{rev.pk}/approve/").status_code, 405
        )
