from django.core.management.base import BaseCommand, CommandError

from apps.loans.services import LoanError, recovery_enabled, run_recovery


class Command(BaseCommand):
    help = (
        "Find loans past their agreed repayment period and recover what is owed from "
        "the member's savings (Manual 4.6). Schedule this to run daily; it needs nobody "
        "to be logged in and is safe to run more than once a day (the system also runs the "
        "same check by itself the first time anyone signs in each day). Real recoveries "
        "happen unless FCU_LOAN_AUTO_RECOVERY_ENABLED is switched off; use --dry-run to "
        "see what would be recovered."
    )

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="Report only; change nothing.")

    def handle(self, *args, **options):
        dry_run = options["dry_run"]

        if not dry_run and not recovery_enabled():
            self.stdout.write(
                "Automatic loan recovery is not enabled (FCU_LOAN_AUTO_RECOVERY_ENABLED). "
                "Showing a dry run instead."
            )
            dry_run = True

        try:
            results = run_recovery(dry_run=dry_run)
        except LoanError as exc:
            raise CommandError("; ".join(exc.messages))

        if not results:
            self.stdout.write("No overdue loans.")
            return

        for r in results:
            loan = r["loan"]
            if dry_run:
                p = r["preview"]
                self.stdout.write(
                    f"{loan.reference} {loan.member.member_code}: owes UGX {p['outstanding']:,.0f}, "
                    f"savings UGX {p['savings']:,.0f}, would recover UGX {p['recoverable']:,.0f}"
                )
            elif r["recovered"]:
                self.stdout.write(
                    f"{loan.reference} {loan.member.member_code}: recovered UGX {r['recovered']:,.0f}, "
                    f"still owed UGX {r['outstanding_after']:,.0f}"
                )
            else:
                self.stdout.write(f"{loan.reference} {loan.member.member_code}: {r['skipped']}")
