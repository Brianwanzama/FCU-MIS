from django.db import migrations


def backfill(apps, schema_editor):
    """
    Cycles activated before membership records existed get one built from the
    members who are Active now - the best information available. It is exact
    for a cycle that is still running and an approximation for any already closed.
    """
    Cycle = apps.get_model("cycles", "Cycle")
    CycleMembership = apps.get_model("cycles", "CycleMembership")
    Member = apps.get_model("members", "Member")

    for cycle in Cycle.objects.exclude(status="UPCOMING"):
        for member in Member.objects.filter(status="ACTIVE"):
            CycleMembership.objects.get_or_create(
                cycle=cycle, member=member, defaults={"joined_on": cycle.start_date}
            )


class Migration(migrations.Migration):

    dependencies = [
        ("cycles", "0002_cycle_membership"),
        ("members", "0004_member_inactive_reason_member_withdrawal_reason_and_more"),
    ]

    operations = [migrations.RunPython(backfill, migrations.RunPython.noop)]
