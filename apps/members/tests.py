"""
Member financial history: who can see what, and what the statement says.
"""
from datetime import date
from decimal import Decimal

from django.test import TestCase

from apps.accounts.models import User
from apps.contributions import services as cs
from apps.contributions.models import Contribution
from apps.cycles import services as cycle_services
from apps.cycles.models import Cycle

from .models import Member
from .services import member_available_savings, member_statement

M = Contribution.ContributionType


class MemberFinanceTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        mk = lambda name, role, member=None: User.objects.create_user(
            username=name, password="pw", role=role, member=member
        )
        members = list(Member.objects.order_by("member_code")[:3])
        cls.m1, cls.m2, cls.m3 = members
        cls.admin = mk("admin", User.Role.ADMINISTRATOR)
        cls.treasurer = mk("treasurer", User.Role.TREASURER)
        cls.chair = mk("chair", User.Role.CHAIRPERSON)
        cls.secretary = mk("secretary", User.Role.SECRETARY)
        cls.me = mk("me", User.Role.MEMBER, cls.m1)
        cls.other = mk("other", User.Role.MEMBER, cls.m2)

        cls.old = cycle_services.create_cycle(
            Cycle(cycle_number=1, start_date=date(2026, 1, 1), duration_months=6), actor=cls.admin
        )
        cycle_services.activate_cycle(cls.old, actor=cls.admin)
        cls.pay(cls, cls.old, cls.m1, M.MONTHLY, "500000", date(2026, 1, 1))
        cls.pay(cls, cls.old, cls.m1, M.EMERGENCY, "30000", date(2026, 1, 1))
        cycle_services.close_cycle(cls.old, actor=cls.admin)

        cls.new = cycle_services.create_cycle(
            Cycle(cycle_number=2, start_date=date(2026, 7, 1), duration_months=6), actor=cls.admin
        )
        cycle_services.activate_cycle(cls.new, actor=cls.admin)

    def pay(self, cycle, member, kind, amount, month, settles=None):
        return cs.record_contribution(
            Contribution(
                member=member, cycle=cycle, settles_cycle=settles, contribution_type=kind,
                amount=Decimal(amount), period_month=month, payment_date=date(2026, 1, 5),
                payment_method="CASH", recorded_by=self.admin,
            ),
            actor=self.admin,
        )


class AccessTests(MemberFinanceTestCase):
    def codes(self, username, path):
        self.client.logout()
        self.client.login(username=username, password="pw")
        return self.client.get(path).status_code

    def test_a_member_sees_only_their_own_pages(self):
        for suffix in ("", "statement/", "statement.pdf"):
            own = f"/members/{self.m1.member_code}/{suffix}"
            theirs = f"/members/{self.m2.member_code}/{suffix}"
            self.assertEqual(self.codes("me", own), 200, own)
            self.assertEqual(self.codes("me", theirs), 403, theirs)

    def test_office_bearers_see_everyone(self):
        for user in ("admin", "treasurer", "chair"):
            for suffix in ("", "statement/", "statement.pdf"):
                path = f"/members/{self.m1.member_code}/{suffix}"
                self.assertEqual(self.codes(user, path), 200, (user, path))

    def test_secretary_cannot_see_other_members_finances(self):
        self.assertEqual(self.codes("secretary", f"/members/{self.m1.member_code}/"), 403)

    def test_anonymous_is_sent_to_login(self):
        self.client.logout()
        r = self.client.get(f"/members/{self.m1.member_code}/")
        self.assertEqual(r.status_code, 302)
        self.assertIn("login", r["Location"])

    def test_my_finances_redirects_to_own_page(self):
        self.client.login(username="me", password="pw")
        r = self.client.get("/members/me/")
        self.assertRedirects(r, f"/members/{self.m1.member_code}/", fetch_redirect_response=False)

    def test_unknown_member_is_404_not_403(self):
        self.assertEqual(self.codes("admin", "/members/FCU999/"), 404)


class StatementTests(MemberFinanceTestCase):
    def test_statement_figures_for_the_closed_cycle(self):
        s = member_statement(self.m1, self.old)
        self.assertEqual(s["deposit_target"], Decimal("600000"))
        self.assertEqual(s["deposits_paid"], Decimal("500000"))
        self.assertEqual(s["deposits_outstanding"], Decimal("100000"))
        self.assertEqual(s["emergency_paid"], Decimal("30000"))
        self.assertEqual(s["emergency_outstanding"], Decimal("0"))
        self.assertFalse(s["target_met"])
        self.assertEqual(len(s["rows"]), 2)
        self.assertEqual(s["rows"][-1]["running_deposits"], Decimal("500000"))
        self.assertEqual(s["rows"][-1]["running_emergency"], Decimal("30000"))

    def test_each_cycle_has_its_own_history(self):
        self.assertEqual(len(member_statement(self.m1, self.new)["rows"]), 0)
        self.assertEqual(member_statement(self.m1, self.new)["deposits_paid"], Decimal("0"))

    def test_arrears_payment_counts_for_the_old_cycle_only(self):
        self.pay(self.new, self.m1, M.MONTHLY, "100000", date(2026, 1, 1), settles=self.old)
        old = member_statement(self.m1, self.old)
        new = member_statement(self.m1, self.new)
        self.assertEqual(old["deposits_paid"], Decimal("600000"))
        self.assertTrue(old["target_met"])
        self.assertEqual(new["deposits_paid"], Decimal("0"))
        # listed in both, but only counted in the old cycle
        self.assertEqual(len(old["rows"]), 3)
        self.assertEqual(len(new["rows"]), 1)
        self.assertFalse(new["rows"][0]["counts_here"])
        self.assertIn("Cycle 1", new["rows"][0]["note"])

    def test_reversed_payment_is_shown_but_not_counted(self):
        c = self.pay(self.new, self.m1, M.MONTHLY, "100000", date(2026, 7, 1))
        r = cs.request_reversal(c, self.admin, "Duplicate")
        cs.approve_reversal(r, self.chair)
        s = member_statement(self.m1, self.new)
        self.assertEqual(s["deposits_paid"], Decimal("0"))
        self.assertEqual(len(s["rows"]), 1)
        self.assertTrue(s["rows"][0]["reversed"])
        self.assertEqual(s["rows"][0]["running_deposits"], Decimal("0"))

    def test_available_savings_spans_cycles_and_excludes_emergency(self):
        self.pay(self.new, self.m1, M.MONTHLY, "100000", date(2026, 7, 1))
        self.assertEqual(member_available_savings(self.m1), Decimal("600000"))  # 500k + 100k

    def test_available_savings_exclude_reversed_payments(self):
        c = self.pay(self.new, self.m1, M.MONTHLY, "100000", date(2026, 7, 1))
        r = cs.request_reversal(c, self.admin, "x")
        cs.approve_reversal(r, self.chair)
        self.assertEqual(member_available_savings(self.m1), Decimal("500000"))


class PageTests(MemberFinanceTestCase):
    def test_dashboard_defaults_to_active_cycle_and_switches_with_the_selector(self):
        self.client.login(username="me", password="pw")
        r = self.client.get(f"/members/{self.m1.member_code}/")
        self.assertEqual(r.context["current_cycle"], self.new)
        r = self.client.get(f"/members/{self.m1.member_code}/?cycle={self.old.pk}")
        self.assertEqual(r.context["current_cycle"], self.old)
        self.assertEqual(r.context["summary"]["contributions"]["monthly_paid"], Decimal("500000"))

    def test_bad_cycle_parameter_falls_back_safely(self):
        self.client.login(username="me", password="pw")
        for value in ("abc", "99999", "-1", ""):
            r = self.client.get(f"/members/{self.m1.member_code}/?cycle={value}")
            self.assertEqual(r.status_code, 200, value)
            self.assertEqual(r.context["current_cycle"], self.new)

    def test_statement_page_shows_the_figures(self):
        self.client.login(username="me", password="pw")
        r = self.client.get(f"/members/{self.m1.member_code}/statement/?cycle={self.old.pk}")
        self.assertContains(r, "UGX 500,000")
        self.assertContains(r, "Not yet met")

    def test_pdf_download(self):
        self.client.login(username="me", password="pw")
        r = self.client.get(f"/members/{self.m1.member_code}/statement.pdf?cycle={self.old.pk}")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r["Content-Type"], "application/pdf")
        self.assertTrue(r.content.startswith(b"%PDF"))
        self.assertIn("attachment", r["Content-Disposition"])
        self.assertIn(self.m1.member_code, r["Content-Disposition"])

    def test_pdf_survives_markup_characters_in_user_text(self):
        c = self.pay(self.new, self.m1, M.MONTHLY, "100000", date(2026, 7, 1))
        Contribution.objects.filter(pk=c.pk).update(reference="<b>& slip </i>")
        self.client.login(username="me", password="pw")
        r = self.client.get(f"/members/{self.m1.member_code}/statement.pdf?cycle={self.new.pk}")
        self.assertEqual(r.status_code, 200)

    def test_register_hides_view_links_for_other_members(self):
        self.client.login(username="me", password="pw")
        html = self.client.get("/members/").content.decode()
        self.assertIn(f"/members/{self.m1.member_code}/", html)
        self.assertNotIn(f"/members/{self.m2.member_code}/", html)
        self.assertNotIn("New Member", html)


class MemberPageTextTests(MemberFinanceTestCase):
    def test_header_cards_report_deposits_not_a_mixed_total(self):
        self.client.login(username="me", password="pw")
        r = self.client.get(f"/members/{self.m1.member_code}/")
        self.assertContains(r, "Deposit Target")
        self.assertContains(r, "Deposits Paid")
        self.assertNotContains(r, "Expected Contributions")

    def test_no_dead_buttons_or_placeholder_text(self):
        self.client.login(username="me", password="pw")
        r = self.client.get(f"/members/{self.m1.member_code}/")
        self.assertNotContains(r, "will appear here")
        self.assertNotContains(r, "disabled>")

    def test_quick_actions_follow_the_role(self):
        self.client.login(username="me", password="pw")
        r = self.client.get(f"/members/{self.m1.member_code}/")
        self.assertNotContains(r, "Record Contribution")
        self.assertNotContains(r, "Edit Member")
        self.client.login(username="admin", password="pw")
        r = self.client.get(f"/members/{self.m1.member_code}/")
        self.assertContains(r, "Record Contribution")
        self.assertContains(r, "Edit Member")
