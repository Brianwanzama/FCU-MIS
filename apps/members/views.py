from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied
from django.db.models import Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect
from django.views import View
from django.views.generic import DetailView, ListView, TemplateView

from apps.contributions.models import Contribution
from apps.cycles.models import Cycle
from apps.cycles.services import get_active_cycle

from .models import Member
from .services import (
    member_available_savings,
    member_financial_summary,
    member_statement,
    statement_cycles,
)
from .statement_pdf import render_statement_pdf


class MemberListView(LoginRequiredMixin, ListView):
    """
    Displays the FCU Member Register.
    """

    model = Member
    template_name = "members/member_list.html"
    context_object_name = "members"
    ordering = ["member_code"]


class MemberFinanceAccessMixin(LoginRequiredMixin):
    """
    A member may open only their own financial pages. The Administrator,
    Treasurer and Chairperson may open anyone's. Everyone else gets a 403.
    """

    def get_member(self):
        member = get_object_or_404(Member, member_code=self.kwargs["member_code"])

        if not self.request.user.can_view_member_finances(member):
            raise PermissionDenied("Members may only view their own financial records.")

        return member

    def selected_cycle(self):
        """The cycle from ?cycle=<id>, else the active cycle, else the latest."""

        cycles = statement_cycles()
        requested = self.request.GET.get("cycle")

        if requested and requested.isdigit():
            cycle = cycles.filter(pk=int(requested)).first()
            if cycle:
                return cycle

        return get_active_cycle() or cycles.first()


class MemberDetailView(MemberFinanceAccessMixin, DetailView):
    """
    Displays the Member Financial Dashboard for a chosen cycle.
    """

    model = Member
    template_name = "members/member_detail.html"
    context_object_name = "member"

    def get_object(self, queryset=None):
        return self.get_member()

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        cycle = self.selected_cycle()

        context["current_cycle"] = cycle
        context["cycles"] = statement_cycles()
        context["viewing_own"] = self.request.user.member_id == self.object.pk

        if cycle:
            context["summary"] = member_financial_summary(self.object, cycle)
            context["transactions"] = (
                Contribution.objects.filter(
                    Q(cycle=cycle) | Q(settles_cycle=cycle), member=self.object
                )
                .select_related("cycle", "recorded_by")
                .order_by("-payment_date", "-created_at")
            )
        else:
            context["summary"] = None
            context["transactions"] = Contribution.objects.none()

        context["available_savings"] = member_available_savings(self.object)

        if cycle:
            from apps.reports.charts import monthly_deposits

            context["chart_monthly"] = monthly_deposits(cycle, member=self.object)

        return context


class MyFinancesView(LoginRequiredMixin, View):
    """Send a signed-in member to their own financial page."""

    def get(self, request):

        if request.user.member_id is None:
            messages.info(request, "This account is not linked to a member record.")
            return redirect("core:dashboard")

        return redirect("members:detail", member_code=request.user.member.member_code)


class MemberStatementView(MemberFinanceAccessMixin, TemplateView):
    """Printable statement for one member and one cycle."""

    template_name = "members/member_statement.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        member = self.get_member()
        cycle = self.selected_cycle()

        context["member"] = member
        context["cycles"] = statement_cycles()
        context["statement"] = member_statement(member, cycle) if cycle else None

        return context


class MemberStatementPDFView(MemberFinanceAccessMixin, View):
    """Download the statement as a PDF."""

    def get(self, request, member_code):

        member = self.get_member()
        cycle = self.selected_cycle()

        if cycle is None:
            messages.info(request, "There is no cycle to produce a statement for yet.")
            return redirect("members:detail", member_code=member.member_code)

        pdf = render_statement_pdf(member_statement(member, cycle))

        response = HttpResponse(pdf, content_type="application/pdf")
        response["Content-Disposition"] = (
            f'attachment; filename="FCU-statement-{member.member_code}-cycle-{cycle.cycle_number}.pdf"'
        )

        return response


class MemberStatementExcelView(MemberFinanceAccessMixin, View):
    """Download the statement as an Excel workbook."""

    def get(self, request, member_code):

        from apps.reports.excel import statement_workbook

        member = self.get_member()
        cycle = self.selected_cycle()

        if cycle is None:
            messages.info(request, "There is no cycle to produce a statement for yet.")
            return redirect("members:detail", member_code=member.member_code)

        response = HttpResponse(
            statement_workbook(member_statement(member, cycle)),
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        response["Content-Disposition"] = (
            f'attachment; filename="FCU-statement-{member.member_code}-cycle-{cycle.cycle_number}.xlsx"'
        )

        return response
