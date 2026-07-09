"""
Seeds the two governing documents as the initial DocumentTemplate versions
(FR-17), reading them from the tracked seed_files/ directory bundled with this
app so a fresh `migrate` on any environment is self-contained — no manual
shell steps needed to make the Document Library functional out of the box.
"""
import pathlib

from django.core.files import File
from django.db import migrations

SEED_DIR = pathlib.Path(__file__).resolve().parent.parent / "seed_files"

SEED_DOCS = [
    ("LOAN_APPLICATION_FORM", "loan_application_form_v1.pdf", "v1.0"),
    ("MEMBER_DECLARATION", "member_declaration_v1.pdf", "v1.0"),
]


def seed_documents(apps, schema_editor):
    DocumentTemplate = apps.get_model("documents", "DocumentTemplate")
    for doc_type, filename, version_label in SEED_DOCS:
        if DocumentTemplate.objects.filter(doc_type=doc_type).exists():
            continue  # don't clobber a real upload with the seed on re-run
        source_path = SEED_DIR / filename
        if not source_path.exists():
            continue  # tolerate a missing seed file rather than failing the whole migrate
        with open(source_path, "rb") as f:
            obj = DocumentTemplate(
                doc_type=doc_type,
                version_label=version_label,
                effective_from="2026-06-18",  # date the Manual v3.0 itself was approved
                is_current=True,
            )
            obj.file.save(filename, File(f), save=True)


def unseed_documents(apps, schema_editor):
    DocumentTemplate = apps.get_model("documents", "DocumentTemplate")
    DocumentTemplate.objects.filter(version_label="v1.0").delete()


class Migration(migrations.Migration):
    dependencies = [
        ("documents", "0001_initial"),
    ]
    operations = [
        migrations.RunPython(seed_documents, unseed_documents),
    ]
