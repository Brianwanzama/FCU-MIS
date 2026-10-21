"""
Phase 2 verification.

These test the business rules that would actually cost FCU money or credibility
if they broke - not that Django can save a row.
"""
from datetime import date
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.urls import reverse

from apps.accounts.models import User
from apps.auditlog.models import AuditLog
from apps.members.models import Member

from . import services
from .models import Cycle, FinancialYear


def make_cycle(number, start, months=6, **kw):
    return Cycle(cycle_number=number, start_date=start, duration_months=months, **kw)


class DerivationTests(TestCase):
    def test_end_date_and_target_are_derived(self):
        c = services.create_cycle(make_cycle(1, date(2026, 1, 1)))
        self.assertEqual(c.end_date, date(2026, 6, 30))
        self.assertEqual(c.per_member_target, Decimal("630000.00"))
        self.assertEqual(c.name, "January-June 2026")
        self.assertEqual(c.financial_year.year, 2026)

    def test_second_half_year_cycle(self):
        c = services.create_cycle(make_cycle(2, date(2026, 7, 1)))
        self.assertEqual(c.end_date, date(2026, 12, 31))
        self.assertEqual(c.name, "July-December 2026")

    def test_february_end_is_handled(self):
        c = services.create_cycle(
            make_cycle(1, date(2024, 9, 1), months=6)
        )
        self.assertEqual(c.end_date, date(2025, 2, 28))  # leap-safe, spans year boundary

    def test_target_follows_amended_amounts(self):
        c = services.create_cycle(
            make_cycle(1, date(2026, 1, 1), monthly_savings_amount=Decimal("200000"))
        )
        self.assertEqual(c.per_member_target, Decimal("1230000.00"))


class BusinessRuleTests(TestCase):
    def test_cycles_cannot_overlap(self):
        services.create_cycle(make_cycle(1, date(2026, 1, 1)))
        with self.assertRaises(ValidationError) as ctx:
            services.create_cycle(make_cycle(2, date(2026, 4, 1)))
        self.assertIn("overlap", str(ctx.exception).lower())

    def test_cycle_number_is_unique(self):
        services.create_cycle(make_cycle(1, date(2026, 1, 1)))
        with self.assertRaises(ValidationError):
            services.create_cycle(make_cycle(1, date(2026, 7, 1)))

    def test_nonstandard_duration_requires_a_reason(self):
        with self.assertRaises(ValidationError) as ctx:
            services.create_cycle(make_cycle(1, date(2026, 1, 1), months=4))
        self.assertIn("duration_override_reason", ctx.exception.message_dict)

        c = services.create_cycle(
            make_cycle(1, date(2026, 1, 1), months=4,
                       duration_override_reason="Historical Cycle 1 ran only 4 months.")
        )
        self.assertEqual(c.duration_months, 4)
        self.assertEqual(c.per_member_target, Decimal("420000.00"))

    def test_only_one_active_cycle_at_the_database_level(self):
        c1 = services.create_cycle(make_cycle(1, date(2026, 1, 1)))
        c2 = services.create_cycle(make_cycle(2, date(2026, 7, 1)))
        services.activate_cycle(c1)

        # Bypass the service layer entirely - the DB itself must still refuse.
        c2.status = Cycle.Status.ACTIVE
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                c2.save()

    def test_activating_does_not_silently_close_the_previous_cycle(self):
        c1 = services.create_cycle(make_cycle(1, date(2026, 1, 1)))
        c2 = services.create_cycle(make_cycle(2, date(2026, 7, 1)))
        services.activate_cycle(c1)

        with self.assertRaises(ValidationError) as ctx:
            services.activate_cycle(c2)
        self.assertIn("still active", str(ctx.exception))

        c1.refresh_from_db()
        self.assertEqual(c1.status, Cycle.Status.ACTIVE)  # untouched


class LockingTests(TestCase):
    def setUp(self):
        self.cycle = services.create_cycle(make_cycle(1, date(2024, 1, 1)))
        services.activate_cycle(self.cycle)

    def test_closed_cycle_cannot_be_edited(self):
        services.close_cycle(self.cycle)
        self.cycle.refresh_from_db()
        self.assertTrue(self.cycle.is_locked)
        with self.assertRaises(ValidationError):
            services.update_cycle(self.cycle, services._snapshot(self.cycle))

    def test_reopen_requires_a_reason(self):
        services.close_cycle(self.cycle)
        with self.assertRaises(ValidationError):
            services.reopen_cycle(self.cycle, actor=None, reason="   ")

    def test_reopen_restores_active_and_counts(self):
        services.close_cycle(self.cycle)
        services.reopen_cycle(self.cycle, actor=None, reason="June contribution omitted in error.")
        self.cycle.refresh_from_db()
        self.assertEqual(self.cycle.status, Cycle.Status.ACTIVE)
        self.assertEqual(self.cycle.reopen_count, 1)
        self.assertIsNone(self.cycle.closed_at)

    def test_upcoming_cycle_cannot_be_closed(self):
        upcoming = services.create_cycle(make_cycle(2, date(2026, 7, 1)))
        with self.assertRaises(ValidationError):
            services.close_cycle(upcoming)


class DeletionTests(TestCase):
    def test_upcoming_never_activated_is_deletable(self):
        c = services.create_cycle(make_cycle(1, date(2026, 1, 1)))
        self.assertTrue(c.is_deletable()[0])
        services.delete_cycle(c)
        self.assertFalse(Cycle.objects.filter(pk=c.pk).exists())

    def test_active_cycle_is_not_deletable(self):
        c = services.create_cycle(make_cycle(1, date(2026, 1, 1)))
        services.activate_cycle(c)
        self.assertFalse(c.is_deletable()[0])
        with self.assertRaises(ValidationError):
            services.delete_cycle(c)

    def test_previously_activated_cycle_is_never_deletable(self):
        c = services.create_cycle(make_cycle(1, date(2024, 1, 1)))
        services.activate_cycle(c)
        services.close_cycle(c)
        self.assertFalse(c.is_deletable()[0])


class AuditTests(TestCase):
    def test_every_transition_is_logged(self):
        AuditLog.objects.all().delete()
        c = services.create_cycle(make_cycle(1, date(2024, 1, 1)))
        services.activate_cycle(c)
        services.close_cycle(c)
        services.reopen_cycle(c, actor=None, reason="Late contribution received after close.")

        entries = AuditLog.objects.filter(model_name="Cycle").order_by("id")
        self.assertEqual(entries.count(), 4)
        events = [e.new_value.get("event") for e in entries if e.new_value]
        self.assertIn("CYCLE_ACTIVATED", events)
        self.assertIn("CYCLE_CLOSED", events)
        self.assertIn("CYCLE_REOPENED", events)

        reopen = entries.last()
        self.assertEqual(reopen.new_value["reason"], "Late contribution received after close.")

    def test_amount_change_logs_a_policy_amendment(self):
        c = services.create_cycle(make_cycle(1, date(2026, 1, 1)))
        old = services._snapshot(c)
        c.monthly_savings_amount = Decimal("150000")
        services.update_cycle(c, old)
        self.assertTrue(
            AuditLog.objects.filter(
                model_name="Cycle", action=AuditLog.Action.SETTINGS_CHANGE
            ).exists()
        )

    def test_delete_is_logged_after_the_row_is_gone(self):
        c = services.create_cycle(make_cycle(1, date(2026, 1, 1)))
        services.delete_cycle(c)
        entry = AuditLog.objects.filter(action=AuditLog.Action.DELETE, model_name="Cycle").first()
        self.assertIsNotNone(entry)
        self.assertEqual(entry.old_value["cycle_number"], 1)


class RBACTests(TestCase):
    def setUp(self):
        self.member = Member.objects.create(full_name="Plain Member", joined_date=date(2023, 7, 1))
        self.admin_member = Member.objects.create(full_name="Admin Member", joined_date=date(2023, 7, 1))
        self.plain = User.objects.create_user(
            username="plain", password="a-long-test-password", role=User.Role.MEMBER, member=self.member
        )
        self.admin = User.objects.create_user(
            username="admin", password="a-long-test-password",
            role=User.Role.ADMINISTRATOR, member=self.admin_member,
        )
        self.cycle = services.create_cycle(make_cycle(1, date(2026, 1, 1)))

    def test_plain_member_is_forbidden_from_every_cycle_url(self):
        self.client.force_login(self.plain)
        for url in [
            reverse("cycles:list"),
            reverse("cycles:create"),
            reverse("cycles:detail", args=[self.cycle.pk]),
            reverse("cycles:edit", args=[self.cycle.pk]),
            reverse("cycles:activate", args=[self.cycle.pk]),
            reverse("cycles:close", args=[self.cycle.pk]),
            reverse("cycles:reopen", args=[self.cycle.pk]),
            reverse("cycles:delete", args=[self.cycle.pk]),
        ]:
            self.assertEqual(self.client.get(url).status_code, 403, url)

    def test_anonymous_is_redirected_to_login(self):
        self.assertEqual(self.client.get(reverse("cycles:list")).status_code, 302)

    def test_administrator_can_reach_and_use_the_module(self):
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(reverse("cycles:list")).status_code, 200)

        resp = self.client.post(reverse("cycles:activate", args=[self.cycle.pk]), follow=True)
        self.assertEqual(resp.status_code, 200)
        self.cycle.refresh_from_db()
        self.assertEqual(self.cycle.status, Cycle.Status.ACTIVE)
        self.assertEqual(self.cycle.activated_by, self.admin)

    def test_activate_is_post_only(self):
        self.client.force_login(self.admin)
        self.client.get(reverse("cycles:activate", args=[self.cycle.pk]))
        self.cycle.refresh_from_db()
        self.assertEqual(self.cycle.status, Cycle.Status.UPCOMING)

    def test_nav_shows_cycles_only_to_administrators(self):
        self.client.force_login(self.admin)
        self.assertContains(self.client.get(reverse("core:dashboard")), 'href="/cycles/"')

        self.client.force_login(self.plain)
        self.assertNotContains(self.client.get(reverse("core:dashboard")), 'href="/cycles/"')


class UnitTargetTests(TestCase):
    """
    The 19 seeded members (members.0003_seed_initial_members) are already in the
    test database, so this asserts against the real seeded roster rather than
    inventing a second one.
    """

    def test_unit_targets_are_per_member_targets_times_active_members(self):
        active = Member.objects.filter(status=Member.Status.ACTIVE).count()
        self.assertEqual(active, 19)  # the real FCU roster

        c = services.create_cycle(make_cycle(1, date(2026, 1, 1)))
        self.assertEqual(c.per_member_target, Decimal("630000.00"))
        self.assertEqual(c.unit_deposit_target, Decimal("11400000"))  # 600,000 x 19
        self.assertEqual(c.unit_emergency_target, Decimal("570000"))  # 30,000 x 19

    def test_unit_targets_move_when_a_member_goes_inactive(self):
        c = services.create_cycle(make_cycle(1, date(2026, 1, 1)))
        Member.objects.filter(member_code="FCU019").update(status=Member.Status.INACTIVE)
        self.assertEqual(c.unit_deposit_target, Decimal("10800000"))  # 600,000 x 18
        self.assertEqual(c.unit_emergency_target, Decimal("540000"))  # 30,000 x 18


class MembershipRecordTests(TestCase):
    """Targets follow the cycle's own membership record, not today's headcount."""

    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_user(
            username="cyc_admin", password="x", role=User.Role.ADMINISTRATOR
        )

    def cycle(self, number=1, start=date(2026, 1, 1)):
        return services.create_cycle(make_cycle(number, start), actor=self.admin)

    def test_activation_records_the_active_members(self):
        c = self.cycle()
        services.activate_cycle(c, actor=self.admin)
        self.assertEqual(c.memberships.count(), 19)
        self.assertEqual(c.eligible_member_count, 19)

    def test_later_headcount_changes_do_not_rewrite_the_cycle_targets(self):
        c = self.cycle()
        services.activate_cycle(c, actor=self.admin)
        Member.objects.filter(member_code="FCU019").update(status=Member.Status.INACTIVE)
        self.assertEqual(c.unit_deposit_target, Decimal("11400000"))  # still 19 x 600,000

    def test_closed_cycle_targets_stay_fixed(self):
        c = self.cycle()
        services.activate_cycle(c, actor=self.admin)
        services.close_cycle(c, actor=self.admin)
        Member.objects.filter(member_code__in=["FCU001", "FCU002"]).update(
            status=Member.Status.WITHDRAWN
        )
        c.refresh_from_db()
        self.assertEqual(c.unit_deposit_target, Decimal("11400000"))

    def test_upcoming_cycle_projects_the_current_headcount(self):
        c = self.cycle()
        self.assertEqual(c.eligible_member_count, 19)
        Member.objects.filter(member_code="FCU019").update(status=Member.Status.INACTIVE)
        self.assertEqual(c.eligible_member_count, 18)

    def test_a_member_who_joins_mid_cycle_owes_the_full_amount(self):
        c = self.cycle()
        services.activate_cycle(c, actor=self.admin)
        Member.objects.create(full_name="New Joiner", joined_date=date(2026, 3, 1))
        self.assertEqual(c.eligible_member_count, 20)
        self.assertEqual(c.unit_deposit_target, Decimal("600000") * 20)

    def test_a_member_who_withdraws_stops_counting_but_keeps_their_record(self):
        c = self.cycle()
        services.activate_cycle(c, actor=self.admin)
        m = Member.objects.get(member_code="FCU019")
        m.status = Member.Status.WITHDRAWN
        m.save()
        self.assertEqual(c.eligible_member_count, 18)
        row = c.memberships.get(member=m)
        self.assertIsNotNone(row.left_on)

    def test_closing_only_assesses_the_cycles_members_in_the_warning(self):
        c = self.cycle()
        services.activate_cycle(c, actor=self.admin)
        from apps.members.services import members_failing_cycle_target

        self.assertEqual(len(members_failing_cycle_target(c)), 19)  # nobody has paid


class DateChangeAndCloseChecksTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_user(
            username="dc_admin", password="x", role=User.Role.ADMINISTRATOR
        )
        cls.chair = User.objects.create_user(
            username="dc_chair", password="x", role=User.Role.CHAIRPERSON
        )
        cls.cycle = services.create_cycle(make_cycle(1, date(2026, 1, 1)), actor=cls.admin)
        services.activate_cycle(cls.cycle, actor=cls.admin)
        cls.member = Member.objects.order_by("member_code").first()

    def pay(self, month):
        from apps.contributions import services as cs
        from apps.contributions.models import Contribution

        return cs.record_contribution(
            Contribution(
                member=self.member, cycle=self.cycle, contribution_type="MONTHLY",
                amount=Decimal("100000"), period_month=month, payment_date=date(2026, 1, 5),
                payment_method="CASH", recorded_by=self.admin,
            ),
            actor=self.admin,
        )

    def move_start(self, new_start):
        old = services._snapshot(Cycle.objects.get(pk=self.cycle.pk))
        self.cycle.start_date = new_start
        services.update_cycle(self.cycle, old, actor=self.admin)

    def test_moving_the_dates_flags_payments_that_fall_outside(self):
        self.pay(date(2026, 1, 1))
        self.pay(date(2026, 6, 1))
        self.assertEqual(self.cycle.out_of_range_payments().count(), 0)
        self.move_start(date(2026, 3, 1))  # now covers Mar-Aug: January falls out
        self.assertEqual(self.cycle.out_of_range_payments().count(), 1)

    def test_date_change_is_audited_with_the_flagged_payments(self):
        c = self.pay(date(2026, 1, 1))
        self.move_start(date(2026, 3, 1))
        from apps.auditlog.models import AuditLog

        e = AuditLog.objects.filter(new_value__event="CYCLE_DATES_CHANGED").latest("pk")
        self.assertEqual(e.new_value["payments_flagged_for_review"], [c.pk])
        self.assertEqual(e.old_value["start_date"], "2026-01-01")

    def test_targets_recalculate_when_the_duration_changes(self):
        old = services._snapshot(Cycle.objects.get(pk=self.cycle.pk))
        self.cycle.duration_months = 6
        self.cycle.monthly_savings_amount = Decimal("120000")
        services.update_cycle(self.cycle, old, actor=self.admin)
        self.assertEqual(self.cycle.member_deposit_target, Decimal("720000"))
        self.assertEqual(self.cycle.unit_deposit_target, Decimal("720000") * 19)

    def test_close_is_blocked_while_a_reversal_is_pending(self):
        from apps.contributions import services as cs

        c = self.pay(date(2026, 1, 1))
        cs.request_reversal(c, self.admin, "Duplicate")
        self.assertTrue(self.cycle.hard_blockers_for_close())
        with self.assertRaises(services.CycleTransitionError):
            services.close_cycle(self.cycle, actor=self.admin)

    def test_close_works_once_the_reversal_is_decided(self):
        from apps.contributions import services as cs

        c = self.pay(date(2026, 1, 1))
        r = cs.request_reversal(c, self.admin, "Duplicate")
        cs.approve_reversal(r, self.chair)
        services.close_cycle(self.cycle, actor=self.admin)
        self.assertEqual(self.cycle.status, Cycle.Status.CLOSED)

    def test_close_warnings_list_outstanding_amounts_and_flagged_payments(self):
        self.pay(date(2026, 1, 1))
        self.move_start(date(2026, 3, 1))
        text = " ".join(self.cycle.blocking_issues_for_close())
        self.assertIn("Deposits outstanding", text)
        self.assertIn("outside this cycle's dates", text)

    def test_cycle_detail_and_close_pages_render(self):
        self.pay(date(2026, 1, 1))
        self.move_start(date(2026, 3, 1))
        self.client.login(username="dc_admin", password="x")
        self.assertContains(self.client.get(f"/cycles/{self.cycle.pk}/"), "outside this cycle")
        self.assertEqual(self.client.get(f"/cycles/{self.cycle.pk}/close/").status_code, 200)
