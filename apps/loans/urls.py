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
    path("applications/<int:pk>/disburse/", views.DisburseView.as_view(), name="disburse"),
    path("applications/<int:pk>/<str:decision>/", views.ApplicationDecisionView.as_view(), name="decision"),
    path("register/", views.LoanRegisterView.as_view(), name="register"),
    path("loan/<int:pk>/", views.LoanDetailView.as_view(), name="loan"),
    path("loan/<int:pk>/repay/", views.RepaymentCreateView.as_view(), name="repay"),
    path("repayments/<int:pk>/reverse/", views.RepaymentReverseView.as_view(), name="repayment_reverse"),
    path("offsystem/new/", views.OffsystemLoanCreateView.as_view(), name="offsystem_new"),
    path("loan/<int:pk>/delete/", views.LoanDeleteView.as_view(), name="loan_delete"),
    path("repayments/<int:pk>/delete/", views.RepaymentDeleteView.as_view(), name="repayment_delete"),
    path("applications/<int:pk>/delete/", views.ApplicationDeleteView.as_view(), name="application_delete"),
    path("declarations/<int:pk>/delete/", views.UndertakingDeleteView.as_view(), name="undertaking_delete"),
    path("recovery/run/", views.RecoveryRunView.as_view(), name="recovery_run"),
    path("declarations/", views.UndertakingListView.as_view(), name="undertakings"),
    path("declarations/<int:pk>/<str:decision>/", views.UndertakingReviewView.as_view(), name="undertaking_review"),
    path("files/<str:kind>/<int:pk>/", views.FileDownloadView.as_view(), name="file"),
]
