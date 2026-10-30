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

    @override_settings(SECRET_KEY="change-me-to-a-random-50-char-string")
    def test_fails_with_the_sample_key_from_env_example(self):
        User.objects.create_user(username="a", password="x", role=User.Role.ADMINISTRATOR)
        ok, text = self.run_preflight()
        self.assertFalse(ok)
        self.assertIn("sample value", text)

    @override_settings(ALLOWED_HOSTS=["*"])
    def test_fails_with_wildcard_hosts(self):
        User.objects.create_user(username="a", password="x", role=User.Role.ADMINISTRATOR)
        self.assertFalse(self.run_preflight()[0])

    def test_strict_mode_fails_on_warnings(self):
        User.objects.create_user(username="a", password="x", role=User.Role.ADMINISTRATOR)
        self.assertFalse(self.run_preflight(strict=True)[0])

    def test_reports_the_recovery_gate(self):
        User.objects.create_user(username="a", password="x", role=User.Role.ADMINISTRATOR)
        self.assertIn("recovery is ON", self.run_preflight()[1])
        with override_settings(FCU_LOAN_AUTO_RECOVERY_ENABLED=False):
            self.assertIn("recovery is OFF", self.run_preflight()[1])


class SeedDemoTests(TestCase):
    def test_refuses_without_debug_unless_forced(self):
        with self.assertRaises(CommandError):
            call_command("seed_demo", stdout=StringIO())
        self.assertEqual(User.objects.count(), 0)

    def test_creates_one_account_per_role_and_sample_data(self):
        from apps.contributions.models import Contribution
        from apps.cycles.models import Cycle

        call_command("seed_demo", "--force", stdout=StringIO())
        roles = set(User.objects.values_list("role", flat=True))
        self.assertEqual(roles, {"ADMINISTRATOR", "TREASURER", "CHAIRPERSON", "SECRETARY", "MEMBER"})
        self.assertTrue(Cycle.objects.filter(status="ACTIVE").exists())
        self.assertTrue(Contribution.objects.exists())
        self.assertTrue(self.client.login(username="demo_member", password="Demo-pass-2026!"))

    def test_running_twice_changes_nothing(self):
        call_command("seed_demo", "--force", stdout=StringIO())
        counts = (User.objects.count(),)
        call_command("seed_demo", "--force", stdout=StringIO())
        self.assertEqual((User.objects.count(),), counts)


class DashboardLoanCardTests(TestCase):
    def test_dashboard_no_longer_says_loans_are_coming(self):
        from apps.members.models import Member

        u = User.objects.create_user(
            username="t", password="pw", role=User.Role.TREASURER, member=Member.objects.first()
        )
        self.client.login(username="t", password="pw")
        r = self.client.get("/dashboard/")
        self.assertEqual(r.status_code, 200)
        self.assertNotContains(r, "coming next")
        self.assertContains(r, "Applications awaiting a decision")

    def test_member_sees_their_own_loan_balance_card(self):
        from apps.members.models import Member

        User.objects.create_user(username="m", password="pw", role=User.Role.MEMBER, member=Member.objects.first())
        self.client.login(username="m", password="pw")
        r = self.client.get("/dashboard/")
        self.assertContains(r, "My loan balance")
        self.assertNotContains(r, "Applications awaiting a decision")


class SecretKeyFallbackTests(TestCase):
    """Production must start safely even when DJANGO_SECRET_KEY was never set properly."""

    def settings_of(self, **env):
        import os
        import subprocess
        import sys

        from django.conf import settings as dj

        base = {k: v for k, v in os.environ.items() if not k.startswith(("DJANGO_", "DATABASE_URL"))}
        base.update({"DJANGO_SETTINGS_MODULE": "config.settings.prod", **env})
        code = (
            "import django; django.setup(); from django.conf import settings as s; "
            "print(len(s.SECRET_KEY), s.SECRET_KEY_IS_DERIVED, s.SECRET_KEY)"
        )
        done = subprocess.run(
            [sys.executable, "-c", code], cwd=dj.BASE_DIR, env=base, capture_output=True, text=True
        )
        return done

    def test_sample_key_with_a_database_url_derives_a_safe_key(self):
        r = self.settings_of(DJANGO_SECRET_KEY="change-me-to-a-random-50-char-string",
                             DATABASE_URL="postgres://u:s3cretpw@db.internal:5432/x")
        self.assertEqual(r.returncode, 0, r.stderr)
        length, derived, key = r.stdout.split()
        self.assertEqual((int(length), derived), (64, "True"))
        self.assertNotIn("change-me", key)

    def test_derived_key_is_stable_and_depends_on_the_database_address(self):
        a1 = self.settings_of(DJANGO_SECRET_KEY="x", DATABASE_URL="postgres://u:one@db:5432/x").stdout
        a2 = self.settings_of(DJANGO_SECRET_KEY="x", DATABASE_URL="postgres://u:one@db:5432/x").stdout
        b = self.settings_of(DJANGO_SECRET_KEY="x", DATABASE_URL="postgres://u:two@db:5432/x").stdout
        self.assertEqual(a1, a2)
        self.assertNotEqual(a1, b)

    def test_a_real_key_is_used_as_given(self):
        import secrets

        key = secrets.token_urlsafe(60)
        r = self.settings_of(DJANGO_SECRET_KEY=key, DATABASE_URL="postgres://u:p@db:5432/x")
        self.assertEqual(r.stdout.split(), [str(len(key)), "False", key])

    def test_refuses_to_start_with_neither_a_key_nor_a_database(self):
        r = self.settings_of(
            DJANGO_SECRET_KEY="change-me-to-a-random-50-char-string",
            DATABASE_URL="postgres://postgres:devpassword@localhost:5432/fcu_mis",  # the development default
        )
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("DJANGO_SECRET_KEY", r.stderr)

    @override_settings(**{**PRODUCTION_LIKE, "SECRET_KEY_IS_DERIVED": True})
    def test_preflight_warns_when_the_key_is_derived(self):
        User.objects.create_user(username="a", password="x", role=User.Role.ADMINISTRATOR)
        out = StringIO()
        call_command("preflight", stdout=out)
        self.assertIn("derived from the database address", out.getvalue())
