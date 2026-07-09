from django.contrib.auth.decorators import login_required
from django.http import FileResponse, Http404
from django.shortcuts import render

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
