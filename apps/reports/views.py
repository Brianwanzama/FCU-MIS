from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, render
from django.views import View

from apps.contributions.models import Contribution
from apps.cycles.models import Cycle
from apps.loans.models import Loan
from apps.unittrust.models import InvestmentTransaction
from apps.unittrust.services import portfolio

from . import excel
from .charts import member_progress, member_status, monthly_deposits, portfolio_allocation
from .pdf import render_cycle_pdf
from .services import cycle_report

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


class ReportAccessMixin(LoginRequiredMixin):
    """Reports show every member's figures, so they are for the officers who may see them."""

    def dispatch(self, request, *args, **kwargs):

        if request.user.is_authenticated and not request.user.has_financial_view_access:
            raise PermissionDenied("You do not have permission to view reports.")

        return super().dispatch(request, *args, **kwargs)


def _file(content, content_type, name):
    response = HttpResponse(content, content_type=content_type)
    response["Content-Disposition"] = f'attachment; filename="{name}"'

    return response


def _reportable_cycles():
    return Cycle.objects.exclude(status=Cycle.Status.UPCOMING).order_by("-start_date")


class ReportIndexView(ReportAccessMixin, View):
    def get(self, request):
        return render(request, "reports/index.html", {"cycles": _reportable_cycles()})


class CycleReportView(ReportAccessMixin, View):
    def get(self, request, pk):

        cycle = get_object_or_404(_reportable_cycles(), pk=pk)

        report = cycle_report(cycle)

        charts = {
            "monthly": monthly_deposits(cycle),
            "status": member_status(report["members"]),
            "progress": member_progress(report["members"]),
            "allocation": portfolio_allocation({"positions": report["investments"]["positions"]}),
        }
        charts["allocation"]["empty"] = "" if charts["allocation"]["values"] else "No investments are held yet."

        return render(request, "reports/cycle_report.html", {"report": report, "cycle": cycle, "charts": charts})


class CycleReportPDFView(ReportAccessMixin, View):
    def get(self, request, pk):

        cycle = get_object_or_404(_reportable_cycles(), pk=pk)

        return _file(
            render_cycle_pdf(cycle_report(cycle)), "application/pdf",
            f"FCU-financial-report-cycle-{cycle.cycle_number}.pdf",
        )


class CycleReportExcelView(ReportAccessMixin, View):
    def get(self, request, pk):

        cycle = get_object_or_404(_reportable_cycles(), pk=pk)

        return _file(
            excel.cycle_workbook(cycle_report(cycle)), XLSX,
            f"FCU-financial-report-cycle-{cycle.cycle_number}.xlsx",
        )


class ContributionsExportView(ReportAccessMixin, View):
    def get(self, request):

        contributions = Contribution.objects.select_related(
            "member", "cycle", "settles_cycle", "recorded_by"
        ).order_by("payment_date", "id")

        cycle = request.GET.get("cycle")

        if cycle and cycle.isdigit():
            contributions = contributions.filter(cycle_id=int(cycle))

        return _file(excel.contributions_workbook(contributions), XLSX, "FCU-contributions.xlsx")


class LoansExportView(ReportAccessMixin, View):
    def get(self, request):

        loans = Loan.objects.select_related("member", "application").prefetch_related(
            "installments", "repayments__recorded_by"
        )

        return _file(excel.loans_workbook(list(loans)), XLSX, "FCU-loans.xlsx")


class InvestmentsExportView(ReportAccessMixin, View):
    def get(self, request):

        transactions = InvestmentTransaction.objects.select_related("investment", "recorded_by").order_by(
            "transaction_date", "id"
        )

        return _file(excel.investments_workbook(portfolio(), transactions), XLSX, "FCU-investments.xlsx")
