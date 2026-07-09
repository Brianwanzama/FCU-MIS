"""
Seeds the 19 founding members exactly as supplied, with corrected emails per
SRS v1.1 Appendix A. FCU001 and FCU004 are flagged as designated administrators
(Brian Wanzama, Anon Mpamizo) so they're auto-granted the Administrator role
the moment they activate their account.

Emails are genuinely blank (None) for members whose email wasn't supplied --
never a fake placeholder domain (A14). Those members cannot self-activate an
account (FR-2.2) until an Administrator adds their real email.

Justin Ainembabazi (ainembabazijustin@gmail.com) was explicitly confirmed NOT
a member and is deliberately not seeded here.

joined_date assumption unchanged: no individual join dates were supplied, so
every seeded member defaults to 2023-07-01 (FCU's founding month) pending
correction via Member Management.
"""
from django.db import migrations

# (code, name, email_or_None, is_designated_admin)
SEED_MEMBERS = [
    ("FCU001", "WANZAMA BRIAN", "brianwanzama39@gmail.com", True),
    ("FCU002", "NABAASA FRANCIS", "francisnabaasa508@gmail.com", False),
    ("FCU003", "MUGUME FRANK", None, False),
    ("FCU004", "MPAMIZO ANON", "anonmpamizo@gmail.com", True),
    ("FCU005", "NAMAGEMBE CAROL", None, False),
    ("FCU006", "GGAYI JAMES", "james12ggayi@gmail.com", False),
    ("FCU007", "MAYEKU PETER", "mayeku.peter22@gmail.com", False),
    ("FCU008", "AHEBWA DICKSON", "ahebwadickson01@gmail.com", False),
    ("FCU009", "NABAGALA CISSY", None, False),
    ("FCU010", "ISAAC BUA TONNY", None, False),
    ("FCU011", "EREDU ALOYSIOUS", "aloysiuseredu735@gmail.com", False),
    ("FCU012", "NAKAYIZA AIDAH", "nassejjeaidah@gmail.com", False),
    ("FCU013", "BAISWIKE APOPHIA", "baiswikeapophia@gmail.com", False),
    ("FCU014", "ABAASA PRAISE", "praisabaasa@gmail.com", False),
    ("FCU015", "MANDELA EDSON", None, False),
    ("FCU016", "KAMUGISHA NELSON", "nelsonkamugisha057@gmail.com", False),
    ("FCU017", "BUKOSERA SYLVIA", None, False),
    ("FCU018", "OWEK SAMUEL", "sammie2me@gmail.com", False),
    ("FCU019", "NKAMUSIIMA ANITAH", "nkamusiimaannitah07@gmail.com", False),
]


def seed_members(apps, schema_editor):
    Member = apps.get_model("members", "Member")
    for code, name, email, is_admin in SEED_MEMBERS:
        Member.objects.update_or_create(
            member_code=code,
            defaults=dict(
                full_name=name,
                email=email,
                joined_date="2023-07-01",
                status="ACTIVE",
                is_designated_admin=is_admin,
            ),
        )


def unseed_members(apps, schema_editor):
    Member = apps.get_model("members", "Member")
    Member.objects.filter(member_code__in=[c for c, *_ in SEED_MEMBERS]).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("members", "0002_alter_member_email"),
    ]
    operations = [
        migrations.RunPython(seed_members, unseed_members),
    ]
