"""
`python manage.py preflight` - is this installation ready to be used by members?

Run it after every deploy (Railway: open a shell on the service) and before go-live.
It prints a checklist and exits with an error if anything that must be right is wrong.
"""

import os
import tempfile

from django.conf import settings
from django.core import checks
from django.core.management.base import BaseCommand, CommandError
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

OK, WARN, FAIL = "ok", "warn", "FAIL"


class Command(BaseCommand):
    help = "Check that this installation is configured safely and is ready for use."

    def add_arguments(self, parser):
        parser.add_argument("--strict", action="store_true", help="Treat warnings as failures too.")

    def handle(self, *args, **options):
        results = []

        def add(level, label, detail=""):
            results.append((level, label, detail))

        production = not settings.DEBUG

        # --- configuration ------------------------------------------------------
        add(OK if production else WARN, "DEBUG is off", "" if production else "DEBUG is on: never run members on this.")

        key = settings.SECRET_KEY
        placeholder = key.lower().startswith(("unsafe", "change-me", "django-insecure", "changeme", "secret"))
        strong = len(key) >= 50 and not placeholder
        derived = getattr(settings, "SECRET_KEY_IS_DERIVED", False)

        if derived:
            add(
                WARN, "Secret key is derived from the database address",
                "No valid DJANGO_SECRET_KEY was set. This is safe but set a real one when you can.",
            )
        else:
            add(
                OK if strong else FAIL, "Secret key is long, random and not a sample value",
                "" if strong else "Set DJANGO_SECRET_KEY to a new random value (the sample in .env.example is public).",
            )

        hosts = settings.ALLOWED_HOSTS
        add(OK if hosts and "*" not in hosts else FAIL, "ALLOWED_HOSTS is set and not a wildcard", ", ".join(hosts))

        for name in ("SESSION_COOKIE_SECURE", "CSRF_COOKIE_SECURE", "SECURE_SSL_REDIRECT"):
            on = getattr(settings, name, False)
            add(OK if on else (FAIL if production else WARN), f"{name} is on")

        console_mail = "console" in settings.EMAIL_BACKEND
        add(
            WARN if console_mail else OK, "Email is sent for real",
            "Emails only print to the log, so password resets will not reach members. Set BREVO_API_KEY and DEFAULT_FROM_EMAIL." if console_mail else "",
        )

        # --- Django's own deployment checks ------------------------------------
        problems = [
            m for m in checks.run_checks(include_deployment_checks=True)
            if m.level >= checks.WARNING and m.id not in ("security.W008",)
        ]
        add(OK if not problems else (FAIL if production else WARN), "Django deployment checks", "; ".join(f"{m.id} {m.msg}" for m in problems))

        # --- database ------------------------------------------------------------
        try:
            connection.ensure_connection()
            pending = MigrationExecutor(connection).migration_plan(
                MigrationExecutor(connection).loader.graph.leaf_nodes()
            )
            add(OK if not pending else FAIL, "Database reachable and migrations applied",
                f"{len(pending)} migration(s) not applied: run migrate" if pending else "")
        except Exception as exc:
            add(FAIL, "Database reachable", str(exc))
            self.finish(results, options)
            return

        # --- files -------------------------------------------------------------
        for label, path in (
            ("Private uploads folder is writable", settings.PRIVATE_MEDIA_ROOT),
            ("Media folder is writable", settings.MEDIA_ROOT),
        ):
            add(*self.writable(label, path))

        static_ok = os.path.isdir(settings.STATIC_ROOT) and os.listdir(settings.STATIC_ROOT)
        add(OK if static_ok else (FAIL if production else WARN), "Static files collected", "" if static_ok else "Run collectstatic.")

        persistent = bool(os.environ.get("FCU_PRIVATE_MEDIA_ROOT"))
        add(
            OK if persistent or not production else WARN, "Uploads folder set explicitly",
            "" if persistent or not production else
            "Set FCU_PRIVATE_MEDIA_ROOT to a persistent volume, or uploaded forms are lost on every deploy.",
        )

        # --- data ---------------------------------------------------------------
        from apps.accounts.models import User
        from apps.cycles.models import Cycle
        from apps.documents.models import DocumentTemplate
        from apps.members.models import Member

        admins = User.objects.filter(role=User.Role.ADMINISTRATOR, is_active=True)
        add(OK if admins.exists() else FAIL, "An active Administrator account exists")

        linked = admins.filter(member__isnull=False).exists()
        add(OK if linked else WARN, "An Administrator is linked to a member",
            "" if linked else "Activate FCU001 or FCU004 at /accounts/activate/.")

        for doc_type, label in DocumentTemplate.DocType.choices:
            has = DocumentTemplate.objects.filter(doc_type=doc_type, is_current=True).exists()
            add(OK if has else WARN, f"{label} is in the Document Library")

        no_email = Member.objects.filter(email__isnull=True, status=Member.Status.ACTIVE).count()
        add(OK if no_email == 0 else WARN, "Every active member has an email address",
            f"{no_email} member(s) cannot activate an account yet." if no_email else "")

        add(OK if Cycle.objects.filter(status=Cycle.Status.ACTIVE).exists() else WARN, "A cycle is active")

        recovery = "ON" if settings.FCU_LOAN_AUTO_RECOVERY_ENABLED else "OFF (dry run only)"
        add(OK, f"Automatic loan recovery is {recovery}", settings.FCU_LOAN_RECOVERY_POLICY_REF)

        self.finish(results, options)

    def writable(self, label, path):
        try:
            os.makedirs(path, exist_ok=True)
            with tempfile.TemporaryFile(dir=path):
                pass
        except OSError as exc:
            return FAIL, label, f"{path}: {exc}"

        return OK, label, str(path)

    def finish(self, results, options):
        marks = {OK: self.style.SUCCESS("  ok  "), WARN: self.style.WARNING(" warn "), FAIL: self.style.ERROR(" FAIL ")}

        for level, label, detail in results:
            self.stdout.write(f"[{marks[level]}] {label}" + (f" - {detail}" if detail else ""))

        failed = [r for r in results if r[0] == FAIL]
        warned = [r for r in results if r[0] == WARN]

        self.stdout.write("")
        self.stdout.write(f"{len(results) - len(failed) - len(warned)} ok, {len(warned)} warning(s), {len(failed)} failure(s)")

        if failed or (options["strict"] and warned):
            raise CommandError("Preflight found problems that need fixing.")
