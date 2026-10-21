from django.urls import path

from . import views

app_name = "reports"

urlpatterns = [
    path("", views.ReportIndexView.as_view(), name="index"),
    path("cycle/<int:pk>/", views.CycleReportView.as_view(), name="cycle"),
    path("cycle/<int:pk>/pdf/", views.CycleReportPDFView.as_view(), name="cycle_pdf"),
    path("cycle/<int:pk>/excel/", views.CycleReportExcelView.as_view(), name="cycle_excel"),
    path("export/contributions/", views.ContributionsExportView.as_view(), name="export_contributions"),
    path("export/loans/", views.LoansExportView.as_view(), name="export_loans"),
    path("export/investments/", views.InvestmentsExportView.as_view(), name="export_investments"),
]
