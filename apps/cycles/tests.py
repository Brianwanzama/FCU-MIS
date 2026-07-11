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

    def test_unit_target_is_per_member_target_times_active_members(self):
        active = Member.objects.filter(status=Member.Status.ACTIVE).count()
        self.assertEqual(active, 19)  # the real FCU roster

        c = services.create_cycle(make_cycle(1, date(2026, 1, 1)))
        self.assertEqual(c.per_member_target, Decimal("630000.00"))
        self.assertEqual(c.unit_target, Decimal("11970000.00"))  # 630,000 x 19

    def test_unit_target_moves_when_a_member_goes_inactive(self):
        c = services.create_cycle(make_cycle(1, date(2026, 1, 1)))
        Member.objects.filter(member_code="FCU019").update(status=Member.Status.INACTIVE)
        self.assertEqual(c.unit_target, Decimal("11340000.00"))  # 630,000 x 18
