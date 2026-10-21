"""
`python manage.py seed_demo` - demo logins and sample data for trying the system out.

For a laptop or a staging copy only. It refuses to run when DEBUG is off (production)
unless you pass --force, because it creates accounts with a known password.
"""

from datetime import date
from decimal import Decimal

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from apps.accounts.models import User
from apps.contributions import services as contribution_services
from apps.contributions.models import Contribution
from apps.cycles import services as cycle_services
from apps.cycles.models import Cycle, _add_months
from apps.members.models import Member

ACCOUNTS = [
    ("demo_admin", User.Role.ADMINISTRATOR, "FCU001"),
    ("demo_treasurer", User.Role.TREASURER, "FCU002"),
    ("demo_chair", User.Role.CHAIRPERSON, "FCU003"),
    ("demo_secretary", User.Role.SECRETARY, "FCU004"),
    ("demo_member", User.Role.MEMBER, "FCU005"),
    ("demo_member2", User.Role.MEMBER, "FCU006"),
]


class Command(BaseCommand):
    help = "Create demo accounts (one per role), an active cycle and a few contributions."

    def add_arguments(self, parser):
        parser.add_argument("--password", default="Demo-pass-2026!", help="Password for every demo account.")
        parser.add_argument("--force", action="store_true", help="Allow running with DEBUG off.")

    def handle(self, *args, **options):
        if not settings.DEBUG and not options["force"]:
            raise CommandError(
                "Refusing to create demo accounts with a known password while DEBUG is off "
                "(production). Use --force only on a throwaway staging copy."
            )

        password = options["password"]
        created = []

        for username, role, code in ACCOUNTS:
            member = Member.objects.filter(member_code=code).first()

            if member is None or User.objects.filter(member=member).exists():
                continue

            if User.objects.filter(username=username).exists():
                continue

            User.objects.create_user(username=username, password=password, role=role, member=member)
            created.append((username, role, code))

        admin = User.objects.get(username="demo_admin") if User.objects.filter(username="demo_admin").exists() else None
        cycle = cycle_services.get_active_cycle()

        if cycle is None and admin is not None:
            first = timezone.localdate().replace(day=1)
            cycle = cycle_services.create_cycle(
                Cycle(cycle_number=(Cycle.objects.count() + 1), start_date=_add_months(first, -2), duration_months=6),
                actor=admin,
            )
            cycle_services.activate_cycle(cycle, actor=admin)

        if cycle is not None and admin is not None and not Contribution.objects.filter(cycle=cycle).exists():
            today = timezone.localdate()
            for code, deposits in (("FCU005", "300000"), ("FCU006", "200000"), ("FCU001", "600000")):
                member = Member.objects.get(member_code=code)
                for kind, amount in (("MONTHLY", deposits), ("EMERGENCY", "15000")):
                    contribution_services.record_contribution(
                        Contribution(
                            member=member, cycle=cycle, contribution_type=kind, amount=Decimal(amount),
                            period_month=cycle.start_date, payment_date=today,
                            payment_method="CASH", recorded_by=admin,
                        ),
                        actor=admin,
                    )

        self.stdout.write(self.style.SUCCESS("Demo data ready."))
        self.stdout.write(f"Password for every demo account: {password}\n")
        for username, role, code in ACCOUNTS:
            exists = User.objects.filter(username=username).exists()
            self.stdout.write(f"  {username:<16} {role:<14} member {code}  {'' if exists else '(not created: member missing or already has an account)'}")
