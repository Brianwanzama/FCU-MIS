from django.conf import settings
from django.db import models


class DocumentTemplate(models.Model):
    """
    FR-17 — the official Loan Application Form and Member Declaration & Commitment
    Agreement, stored as versioned, downloadable templates. Distribution only:
    the system does not collect e-signatures or attach signed copies in v1.0 (A12).
    """

    class DocType(models.TextChoices):
        LOAN_APPLICATION_FORM = "LOAN_APPLICATION_FORM", "Loan Application Form"
        MEMBER_DECLARATION = "MEMBER_DECLARATION", "Member Declaration & Commitment Agreement"

    doc_type = models.CharField(max_length=32, choices=DocType.choices)
    file = models.FileField(upload_to="document_templates/")
    version_label = models.CharField(max_length=20, help_text='e.g. "v1.0"')
    is_current = models.BooleanField(
        default=True,
        help_text="Only one template per doc_type should be current; uploading a new one supersedes the last.",
    )
    effective_from = models.DateField()
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-effective_from", "-created_at"]

    def __str__(self):
        return f"{self.get_doc_type_display()} ({self.version_label})"

    def save(self, *args, **kwargs):
        if self.is_current:
            # Supersede any previous current version of the same document (FR-17.3).
            DocumentTemplate.objects.filter(doc_type=self.doc_type, is_current=True).exclude(
                pk=self.pk
            ).update(is_current=False)
        super().save(*args, **kwargs)
