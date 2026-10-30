"""Extra roles, signing in as a member, and choosing a new password."""
from django.test import TestCase
from django.urls import reverse

from apps.members.models import Member

from .models import User


def make_user(name, role, member=None, **extra):
    return User.objects.create_user(username=name, password="Old-password-123", role=role, member=member, **extra)


class ExtraRoleTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.members = list(Member.objects.order_by("member_code")[:4])
        cls.admin = make_user("adm", User.Role.ADMINISTRATOR, cls.members[0], is_staff=True, is_superuser=True)
        cls.person = make_user("person", User.Role.MEMBER, cls.members[1])

    def test_extra_roles_add_permissions(self):
        self.assertFalse(self.person.has_financial_write_access)
        self.person.extra_roles = [User.Role.TREASURER]
        self.assertTrue(self.person.has_financial_write_access)
        self.assertTrue(self.person.is_treasurer)
        self.assertFalse(self.person.is_plain_member)
        self.assertTrue(self.person.can_delete_records)
        self.assertIn("Treasurer", self.person.roles_display)

    def test_an_administrator_can_assign_extra_roles(self):
        self.client.force_login(self.admin)
        r = self.client.post(
            reverse("accounts:change_role", args=[self.person.pk]),
            {"role": "MEMBER", "extra_roles": ["TREASURER", "SECRETARY"]},
        )
        self.assertEqual(r.status_code, 302)
        self.person.refresh_from_db()
        self.assertEqual(sorted(self.person.extra_roles), ["SECRETARY", "TREASURER"])
        self.assertTrue(self.person.has_financial_write_access)

    def test_junk_roles_are_ignored(self):
        self.client.force_login(self.admin)
        self.client.post(reverse("accounts:change_role", args=[self.person.pk]),
                         {"role": "MEMBER", "extra_roles": ["NOPE", "CHAIRPERSON"]})
        self.person.refresh_from_db()
        self.assertEqual(self.person.extra_roles, ["CHAIRPERSON"])

    def test_the_last_administrator_cannot_be_removed_through_roles(self):
        self.client.force_login(self.admin)
        self.client.post(reverse("accounts:change_role", args=[self.admin.pk]), {"role": "MEMBER"})
        self.admin.refresh_from_db()
        self.assertEqual(self.admin.role, User.Role.ADMINISTRATOR)

    def test_the_users_page_loads(self):
        self.client.force_login(self.admin)
        self.assertContains(self.client.get(reverse("accounts:user_management")), "Sign in as this user")


class SignInAsTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.members = list(Member.objects.order_by("member_code")[:3])
        cls.admin = make_user("adm", User.Role.ADMINISTRATOR, cls.members[0], is_staff=True, is_superuser=True)
        cls.other_admin = make_user("adm2", User.Role.ADMINISTRATOR, cls.members[2], is_staff=True, is_superuser=True)
        cls.person = make_user("person", User.Role.MEMBER, cls.members[1])

    def test_administrator_can_sign_in_as_a_member_and_return(self):
        self.client.force_login(self.admin)
        r = self.client.post(reverse("accounts:impersonate_start", args=[self.person.pk]))
        self.assertEqual(r.status_code, 302)
        page = self.client.get("/dashboard/")
        self.assertEqual(page.wsgi_request.user.pk, self.person.pk)
        self.assertContains(page, "Back to my account")
        self.client.post(reverse("accounts:impersonate_stop"))
        self.assertEqual(self.client.get("/dashboard/").wsgi_request.user.pk, self.admin.pk)

    def test_a_member_cannot_sign_in_as_someone_else(self):
        self.client.force_login(self.person)
        r = self.client.post(reverse("accounts:impersonate_start", args=[self.admin.pk]))
        self.assertEqual(r.status_code, 403)

    def test_one_administrator_cannot_sign_in_as_another(self):
        self.client.force_login(self.admin)
        self.client.post(reverse("accounts:impersonate_start", args=[self.other_admin.pk]))
        self.assertEqual(self.client.get("/dashboard/").wsgi_request.user.pk, self.admin.pk)

    def test_it_requires_post(self):
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(reverse("accounts:impersonate_start", args=[self.person.pk])).status_code, 405)

    def test_it_is_audited(self):
        from apps.auditlog.models import AuditLog

        self.client.force_login(self.admin)
        self.client.post(reverse("accounts:impersonate_start", args=[self.person.pk]))
        self.assertTrue(AuditLog.objects.filter(new_value__event="IMPERSONATION_STARTED").exists())


class PasswordChangeTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.members = list(Member.objects.order_by("member_code")[:3])
        cls.admin = make_user("adm", User.Role.ADMINISTRATOR, cls.members[0], is_staff=True, is_superuser=True)
        cls.person = make_user("person", User.Role.MEMBER, cls.members[1])

    def test_a_member_can_change_their_own_password(self):
        self.client.login(username="person", password="Old-password-123")
        r = self.client.post(reverse("accounts:password_change"), {
            "old_password": "Old-password-123", "new_password1": "Brand-new-pass-4455", "new_password2": "Brand-new-pass-4455"})
        self.assertRedirects(r, "/dashboard/", fetch_redirect_response=False)
        self.client.logout()
        self.assertTrue(self.client.login(username="person", password="Brand-new-pass-4455"))

    def test_a_flagged_user_is_sent_to_the_change_page_until_they_set_one(self):
        self.person.must_change_password = True
        self.person.save()
        self.client.login(username="person", password="Old-password-123")
        r = self.client.get("/dashboard/")
        self.assertRedirects(r, reverse("accounts:password_change"), fetch_redirect_response=False)
        self.assertEqual(self.client.get(reverse("accounts:password_change")).status_code, 200)
        self.client.post(reverse("accounts:password_change"), {
            "old_password": "Old-password-123", "new_password1": "Brand-new-pass-4455", "new_password2": "Brand-new-pass-4455"})
        self.person.refresh_from_db()
        self.assertFalse(self.person.must_change_password)
        self.assertEqual(self.client.get("/dashboard/").status_code, 200)

    def test_admin_temporary_password_forces_a_change(self):
        self.client.force_login(self.admin)
        r = self.client.post(reverse("accounts:temporary_password", args=[self.person.pk]), follow=True)
        self.assertContains(r, "Temporary password for person")
        self.person.refresh_from_db()
        self.assertTrue(self.person.must_change_password)
        self.assertFalse(self.person.check_password("Old-password-123"))

    def test_ask_everyone_to_change_flags_all_but_the_admin(self):
        self.client.force_login(self.admin)
        self.client.post(reverse("accounts:require_password_change_all"))
        self.person.refresh_from_db()
        self.admin.refresh_from_db()
        self.assertTrue(self.person.must_change_password)
        self.assertFalse(self.admin.must_change_password)

    def test_only_an_administrator_can_set_temporary_passwords(self):
        self.client.force_login(self.person)
        r = self.client.post(reverse("accounts:temporary_password", args=[self.admin.pk]))
        self.assertEqual(r.status_code, 403)


class DeleteMemberAndDocumentTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.members = list(Member.objects.order_by("member_code")[:3])
        cls.admin = make_user("adm", User.Role.ADMINISTRATOR, cls.members[0], is_staff=True, is_superuser=True)
        cls.secretary = make_user("sec", User.Role.SECRETARY, cls.members[1])

    def test_a_member_with_financial_records_cannot_be_deleted(self):
        from apps.contributions.models import Contribution

        # Use a real member who has financial history: delete must be refused.
        from datetime import date
        from decimal import Decimal
        from apps.cycles import services as cs
        from apps.cycles.models import Cycle
        from apps.contributions import services as ctr

        cycle = cs.create_cycle(Cycle(cycle_number=1, start_date=date(2026, 1, 1), duration_months=6), actor=self.admin)
        cs.activate_cycle(cycle, actor=self.admin)
        target = self.members[2]
        ctr.record_contribution(Contribution(
            member=target, cycle=cycle, contribution_type="MONTHLY", amount=Decimal("100000"),
            period_month=date(2026, 1, 1), payment_date=date(2026, 1, 5), payment_method="CASH",
            recorded_by=self.admin), actor=self.admin)
        self.client.force_login(self.admin)
        r = self.client.post(f"/members/{target.member_code}/delete/", {"reason": "test"}, follow=True)
        self.assertContains(r, "has financial records")
        self.assertTrue(Member.objects.filter(pk=target.pk).exists())

    def test_only_an_administrator_may_delete_a_member(self):
        self.client.force_login(self.secretary)
        r = self.client.post(f"/members/{self.members[2].member_code}/delete/", {"reason": "x"})
        self.assertEqual(r.status_code, 403)


class MemberFormTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.members = list(Member.objects.order_by("member_code")[:2])
        cls.admin = make_user("adm", User.Role.ADMINISTRATOR, cls.members[0], is_staff=True, is_superuser=True)
        cls.person = make_user("person", User.Role.MEMBER, cls.members[1])

    def test_new_member_page_has_save_and_cancel(self):
        self.client.force_login(self.admin)
        page = self.client.get("/members/new/")
        self.assertContains(page, "Cancel")
        self.assertContains(page, "Add member")

    def test_administrator_adds_and_edits_a_member(self):
        self.client.force_login(self.admin)
        r = self.client.post("/members/new/", {
            "full_name": "Test New", "email": "new.person@example.org", "phone": "0700111222",
            "national_id_number": "", "joined_date": "2026-01-05"})
        member = Member.objects.get(email="new.person@example.org")
        self.assertRedirects(r, f"/members/{member.member_code}/")
        self.client.post(f"/members/{member.member_code}/edit/", {
            "full_name": "Test Renamed", "email": "new.person@example.org", "phone": "0700111222",
            "national_id_number": "", "joined_date": "2026-01-05"})
        member.refresh_from_db()
        self.assertEqual(member.full_name, "Test Renamed")

    def test_a_plain_member_cannot_add_members(self):
        self.client.force_login(self.person)
        self.assertEqual(self.client.get("/members/new/").status_code, 403)

    def test_no_em_dashes_in_the_pages(self):
        self.client.force_login(self.admin)
        for url in ("/dashboard/", "/members/", "/members/new/", "/accounts/users/", "/loans/register/"):
            self.assertNotContains(self.client.get(url), "—", msg_prefix=url)
