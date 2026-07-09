from django.urls import path

from . import views

app_name = "documents"

urlpatterns = [
    path("", views.document_library, name="library"),
    path("<int:pk>/download/", views.download_document, name="download"),
]
