import tempfile
from io import StringIO

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings

from apps.accounts.models import User


class HealthCheckTests(TestCase):
    def test_health_check_is_public_and_says_ok(self):
        r = self.client.get("/healthz/")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json(), {"status": "ok"})

    def test_health_check_reveals_nothing_else(self):
        self.assertEqual(set(self.client.get("/healthz/").json()), {"status"})


STATIC_DIR = tempfile.mkdtemp(prefix="fcu-static-")
open(f"{STATIC_DIR}/app.css", "w").close()

PRODUCTION_LIKE = dict(
    SECRET_KEY="k" * 20 + "abcdefghijklmnopqrstuvwxyz0123456789!@#$%^&*()",
    ALLOWED_HOSTS=["mis.example.org"],
    SESSION_COOKIE_SECURE=True,
    CSRF_COOKIE_SECURE=True,
    SECURE_SSL_REDIRECT=True,
    SECURE_HSTS_SECONDS=31536000,
    SECURE_HSTS_INCLUDE_SUBDOMAINS=True,
    SECURE_HSTS_PRELOAD=True,
    STATIC_ROOT=STATIC_DIR,
)


@override_settings(**PRODUCTION_LIKE)
class PreflightTests(TestCase):
    def run_preflight(self, **kwargs):
        out = StringIO()
        try:
            call_command("preflight", stdout=out, **kwargs)
            return True, out.getvalue()
        except CommandError:
            return False, out.getvalue()

    def test_fails_without_an_administrator(self):
        ok, text = self.run_preflight()
        self.assertFalse(ok)
        self.assertIn("An active Administrator account exists", text)

    def test_passes_the_hard_checks_with_an_administrator(self):
        User.objects.create_user(username="a", password="x", role=User.Role.ADMINISTRATOR)
        ok, text = self.run_preflight()
        self.assertTrue(ok, text)

    @override_settings(SECRET_KEY="unsafe-development-key-change-this")
    def test_fails_with_the_development_secret_key(self):
        User.objects.create_user(username="a", password="x", role=User.Role.ADMINISTRATOR)
        ok, text = self.run_preflight()
        self.assertFalse(ok)
        self.assertIn("Secret key", text)

    @override_settings(ALLOWED_HOSTS=["*"])
    def test_fails_with_wildcard_hosts(self):
        User.objects.create_user(username="a", password="x", role=User.Role.ADMINISTRATOR)
        self.assertFalse(self.run_preflight()[0])

    def test_strict_mode_fails_on_warnings(self):
        User.objects.create_user(username="a", password="x", role=User.Role.ADMINISTRATOR)
        self.assertFalse(self.run_preflight(strict=True)[0])

    def test_reports_the_recovery_gate(self):
        User.objects.create_user(username="a", password="x", role=User.Role.ADMINISTRATOR)
        self.assertIn("recovery is OFF", self.run_preflight()[1])
        with override_settings(FCU_LOAN_AUTO_RECOVERY_ENABLED=True):
            self.assertIn("recovery is ON", self.run_preflight()[1])
