from django.test import TestCase

from apps.accounts.models import User


from django.core import mail
from django.core.mail.backends.base import BaseEmailBackend
from django.test import TestCase as _TestCase, override_settings as _override_settings


class _BrokenMailBackend(BaseEmailBackend):
    def send_messages(self, messages):
        raise OSError("mail server unreachable")


class PasswordResetFlowTests(_TestCase):
    """The reset pages used to crash with a server error after the form was submitted."""

    @classmethod
    def setUpTestData(cls):
        from apps.members.models import Member

        cls.member = Member.objects.exclude(email__isnull=True).first()
        cls.user = User.objects.create_user(
            username="resetme", password="Old-password-123", email=cls.member.email,
            role=User.Role.MEMBER, member=cls.member,
        )

    def test_request_page_loads_and_submitting_goes_to_the_done_page(self):
        self.assertEqual(self.client.get("/accounts/password-reset/").status_code, 200)
        r = self.client.post("/accounts/password-reset/", {"email": self.user.email})
        self.assertRedirects(r, "/accounts/password-reset/done/")
        self.assertEqual(self.client.get("/accounts/password-reset/done/").status_code, 200)

    def test_an_email_with_a_working_link_is_sent(self):
        self.client.post("/accounts/password-reset/", {"email": self.user.email})
        self.assertEqual(len(mail.outbox), 1)
        link = [w for w in mail.outbox[0].body.split() if "/accounts/reset/" in w][0]
        path = "/" + link.split("/", 3)[3]
        page = self.client.get(path, follow=True)
        self.assertEqual(page.status_code, 200)
        done = self.client.post(page.request["PATH_INFO"], {
            "new_password1": "A-brand-new-pass-9876", "new_password2": "A-brand-new-pass-9876"})
        self.assertRedirects(done, "/accounts/reset/done/")
        self.assertTrue(self.client.login(username="resetme", password="A-brand-new-pass-9876"))

    def test_unknown_email_gets_the_same_page_and_no_email(self):
        r = self.client.post("/accounts/password-reset/", {"email": "nobody@example.org"})
        self.assertRedirects(r, "/accounts/password-reset/done/")
        self.assertEqual(len(mail.outbox), 0)

    @_override_settings(EMAIL_BACKEND="apps.accounts.tests._BrokenMailBackend")
    def test_a_broken_mail_server_does_not_cause_a_server_error(self):
        r = self.client.post("/accounts/password-reset/", {"email": self.user.email})
        self.assertRedirects(r, "/accounts/password-reset/done/")
