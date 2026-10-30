from django.urls import path

from .views import (
    MemberDeleteView,
    MemberDetailView,
    MemberFormView,
    MemberListView,
    MemberStatementExcelView,
    MemberStatementPDFView,
    MemberStatementView,
    MyFinancesView,
)

app_name = "members"

urlpatterns = [
    path("", MemberListView.as_view(), name="list"),
    path("new/", MemberFormView.as_view(), name="new"),
    path("me/", MyFinancesView.as_view(), name="mine"),
    path("<slug:member_code>/statement/", MemberStatementView.as_view(), name="statement"),
    path("<slug:member_code>/statement.pdf", MemberStatementPDFView.as_view(), name="statement_pdf"),
    path("<slug:member_code>/statement.xlsx", MemberStatementExcelView.as_view(), name="statement_excel"),
    path("<slug:member_code>/edit/", MemberFormView.as_view(), name="edit"),
    path("<slug:member_code>/delete/", MemberDeleteView.as_view(), name="delete"),
    path("<slug:member_code>/", MemberDetailView.as_view(), name="detail"),
]
