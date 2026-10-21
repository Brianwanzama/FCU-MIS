from django.urls import path

from . import views

app_name = "loans"

urlpatterns = [
    path("", views.MyLoansView.as_view(), name="mine"),
    path("apply/", views.ApplyView.as_view(), name="apply"),
    path("declaration/upload/", views.UndertakingUploadView.as_view(), name="undertaking_upload"),
    path("applications/", views.ApplicationListView.as_view(), name="applications"),
    path("applications/<int:pk>/", views.ApplicationDetailView.as_view(), name="detail"),
    path("applications/<int:pk>/withdraw/", views.ApplicationWithdrawView.as_view(), name="withdraw"),
    path("applications/<int:pk>/<str:decision>/", views.ApplicationDecisionView.as_view(), name="decision"),
    path("declarations/", views.UndertakingListView.as_view(), name="undertakings"),
    path("declarations/<int:pk>/<str:decision>/", views.UndertakingReviewView.as_view(), name="undertaking_review"),
    path("files/<str:kind>/<int:pk>/", views.FileDownloadView.as_view(), name="file"),
]
