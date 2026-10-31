import json
from unittest import mock

from django.core import mail
from django.core.mail import EmailMessage
from django.test import SimpleTestCase, override_settings

from apps.core.email_backends import BrevoEmailBackend


class _Response:
    def read(self):
        return b"{}"

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


@override_settings(BREVO_API_KEY="test-key", DEFAULT_FROM_EMAIL="FCU <fcu@example.org>")
class BrevoBackendTests(SimpleTestCase):
    def test_sends_the_message_over_https_with_the_key(self):
        with mock.patch("urllib.request.urlopen", return_value=_Response()) as urlopen:
            sent = BrevoEmailBackend().send_messages(
                [EmailMessage("Reset", "Open this link", to=["member@example.org"])]
            )
        self.assertEqual(sent, 1)
        request = urlopen.call_args[0][0]
        self.assertEqual(request.full_url, "https://api.brevo.com/v3/smtp/email")
        self.assertEqual(request.get_header("Api-key"), "test-key")
        body = json.loads(request.data)
        self.assertEqual(body["sender"], {"email": "fcu@example.org", "name": "FCU"})
        self.assertEqual(body["to"], [{"email": "member@example.org"}])
        self.assertEqual(body["subject"], "Reset")
        self.assertEqual(body["textContent"], "Open this link")

    def test_a_refusal_is_raised_so_it_is_logged(self):
        import io
        import urllib.error

        error = urllib.error.HTTPError("u", 401, "no", {}, io.BytesIO(b'{"message":"bad key"}'))
        with mock.patch("urllib.request.urlopen", side_effect=error):
            with self.assertRaises(urllib.error.HTTPError):
                BrevoEmailBackend().send_messages([EmailMessage("s", "b", to=["a@example.org"])])

    def test_fail_silently_returns_zero(self):
        with mock.patch("urllib.request.urlopen", side_effect=OSError("down")):
            sent = BrevoEmailBackend(fail_silently=True).send_messages([EmailMessage("s", "b", to=["a@example.org"])])
        self.assertEqual(sent, 0)

    @override_settings(BREVO_API_KEY="")
    def test_without_a_key_it_refuses_clearly(self):
        with self.assertRaises(RuntimeError):
            BrevoEmailBackend().send_messages([EmailMessage("s", "b", to=["a@example.org"])])


class DefaultBackendTests(SimpleTestCase):
    def test_tests_use_the_in_memory_outbox(self):
        mail.send_mail("s", "b", "a@example.org", ["b@example.org"])
        self.assertEqual(len(mail.outbox), 1)
