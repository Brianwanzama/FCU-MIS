from django.contrib.auth.decorators import login_required
from django.http import FileResponse, Http404
from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from .models import DocumentTemplate


@login_required
def document_library(request):
    """FR-17.2 — any authenticated user can view/download the current templates."""
    templates = DocumentTemplate.objects.filter(is_current=True)
    return render(request, "documents/library.html", {"templates": templates})


@login_required
def download_document(request, pk):
    doc = DocumentTemplate.objects.filter(pk=pk, is_current=True).first()
    if doc is None or not doc.file:
        raise Http404("That document is not currently available.")
    return FileResponse(doc.file.open("rb"), as_attachment=True, filename=doc.file.name.split("/")[-1])


@login_required
@require_POST
def delete_document(request, pk):
    """Remove a template from the library (Administrator or Secretary)."""

    if not request.user.has_secretariat_access:
        raise PermissionDenied("Only the Administrator or Secretary can delete documents.")

    from apps.auditlog.models import AuditLog
    from apps.auditlog.utils import log_action

    doc = get_object_or_404(DocumentTemplate, pk=pk)
    reason = (request.POST.get("reason") or "").strip()

    if not reason:
        messages.error(request, "Give a reason for deleting the document.")
        return redirect("documents:library")

    log_action(
        AuditLog.Action.DELETE,
        instance=doc,
        actor=request.user,
        old_value={"type": doc.doc_type, "version": doc.version_label},
        new_value={"event": "DOCUMENT_DELETED", "reason": reason},
    )
    stored = doc.file
    doc.delete()
    if stored:
        stored.delete(save=False)

    messages.success(request, "Document deleted.")
    return redirect("documents:library")
