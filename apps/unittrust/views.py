from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views import View

from .forms import DistributionDraftForm, InvestmentForm, ReasonForm, TransactionForm
from .models import Investment, InvestmentTransaction, ProfitDistribution, ProfitStatement
from .services import (
    InvestmentError,
    approve_distribution,
    cancel_distribution,
    capture_profit,
    close_investment,
    contribution_shares,
    create_investment,
    delete_profit_statement,
    delete_distribution,
    delete_investment,
    delete_transaction,
    draft_distribution,
    mark_distribution_paid,
    member_investment_summary,
    member_profit,
    portfolio,
    position,
    profit_figures,
    publish_profit,
    record_transaction,
    reverse_transaction,
    unpublish_profit,
)


def _messages(exc):
    return " ".join(exc.messages) if hasattr(exc, "messages") else str(exc)


def _shares_with_value(book):
    """Every member's share of the pool, with what that share of the investments is worth."""

    data = contribution_shares()
    for row in data["rows"]:
        row["portfolio_share"] = (book["market_value"] * row["ratio"]).quantize(Decimal("1"))
    return data


class PortfolioView(LoginRequiredMixin, View):
    """
    FCU's investments, open to every member (Manual 10: transparency): what is held,
    what it is worth and what it has earned. Transactions are for officers only.
    """

    def get(self, request):

        user = request.user

        from apps.reports.charts import investment_returns, portfolio_allocation

        book = portfolio()
        allocation = portfolio_allocation(book)
        allocation["empty"] = "" if allocation["values"] else "No investments are held yet."
        returns = investment_returns(book)
        returns["empty"] = "" if returns["values"] else "Nothing has been invested yet."

        return render(
            request,
            "unittrust/portfolio.html",
            {
                "charts": {"allocation": allocation, "returns": returns},
                "book": book,
                "mine": member_investment_summary(user.member) if user.member_id else None,
                "shares": _shares_with_value(book) if user.has_financial_view_access else None,
            },
        )


class InvestmentCreateView(LoginRequiredMixin, View):
    def dispatch(self, request, *args, **kwargs):

        if request.user.is_authenticated and not request.user.has_financial_write_access:
            raise PermissionDenied("You do not have permission to record investments.")

        return super().dispatch(request, *args, **kwargs)

    def get(self, request):
        return render(request, "unittrust/investment_form.html", {"form": InvestmentForm(initial={"opened_on": timezone.localdate()})})

    def post(self, request):

        form = InvestmentForm(request.POST)

        if form.is_valid():
            try:
                investment = create_investment(request.user, **form.cleaned_data)
            except InvestmentError as exc:
                form.add_error(None, _messages(exc))
            else:
                messages.success(request, f"{investment.name} added.")
                return redirect("unittrust:detail", pk=investment.pk)

        return render(request, "unittrust/investment_form.html", {"form": form})


class InvestmentDetailView(LoginRequiredMixin, View):
    def get(self, request, pk):

        investment = get_object_or_404(Investment, pk=pk)
        user = request.user

        return render(
            request,
            "unittrust/investment_detail.html",
            {
                "investment": investment,
                "pos": position(investment),
                "can_see_transactions": user.has_financial_view_access,
                "transactions": investment.transactions.select_related("recorded_by", "reversed_by")
                if user.has_financial_view_access else [],
                "can_record": user.has_financial_write_access and investment.status == "ACTIVE",
                "can_reverse": user.has_chairperson_access,
                "can_delete": user.can_delete_records,
                "form": TransactionForm(initial={"transaction_date": timezone.localdate()}),
                "reason_form": ReasonForm(),
            },
        )


class TransactionCreateView(LoginRequiredMixin, View):
    http_method_names = ["post"]

    def post(self, request, pk):

        investment = get_object_or_404(Investment, pk=pk)
        form = TransactionForm(request.POST)

        if not form.is_valid():
            messages.error(request, "Check the transaction details.")
            return redirect("unittrust:detail", pk=pk)

        try:
            record_transaction(
                investment, form.cleaned_data["kind"], form.cleaned_data["amount"],
                form.cleaned_data["transaction_date"], request.user,
                reference=form.cleaned_data["reference"], notes=form.cleaned_data["notes"],
            )
        except InvestmentError as exc:
            messages.error(request, _messages(exc))
        else:
            messages.success(request, "Transaction recorded.")

        return redirect("unittrust:detail", pk=pk)


class TransactionReverseView(LoginRequiredMixin, View):
    http_method_names = ["post"]

    def post(self, request, pk):

        entry = get_object_or_404(InvestmentTransaction, pk=pk)
        form = ReasonForm(request.POST)

        try:
            reverse_transaction(entry, request.user, form.cleaned_data["reason"] if form.is_valid() else "")
        except InvestmentError as exc:
            messages.error(request, _messages(exc))
        else:
            messages.success(request, "Transaction reversed.")

        return redirect("unittrust:detail", pk=entry.investment_id)


class TransactionDeleteView(LoginRequiredMixin, View):
    http_method_names = ["post"]

    def post(self, request, pk):

        entry = get_object_or_404(InvestmentTransaction, pk=pk)
        investment_id = entry.investment_id
        form = ReasonForm(request.POST)

        try:
            delete_transaction(entry, request.user, form.cleaned_data["reason"] if form.is_valid() else "")
        except InvestmentError as exc:
            messages.error(request, _messages(exc))
        else:
            messages.success(request, "Transaction deleted. The deletion is kept in the audit log.")

        return redirect("unittrust:detail", pk=investment_id)


class InvestmentDeleteView(LoginRequiredMixin, View):
    http_method_names = ["post"]

    def post(self, request, pk):

        investment = get_object_or_404(Investment, pk=pk)
        form = ReasonForm(request.POST)

        try:
            delete_investment(investment, request.user, form.cleaned_data["reason"] if form.is_valid() else "")
        except InvestmentError as exc:
            messages.error(request, _messages(exc))
            return redirect("unittrust:detail", pk=pk)

        messages.success(request, "Investment deleted. The deletion is kept in the audit log.")
        return redirect("unittrust:portfolio")


class DistributionDeleteView(LoginRequiredMixin, View):
    http_method_names = ["post"]

    def post(self, request, pk):

        distribution = get_object_or_404(ProfitDistribution, pk=pk)
        form = ReasonForm(request.POST)

        try:
            delete_distribution(distribution, request.user, form.cleaned_data["reason"] if form.is_valid() else "")
        except InvestmentError as exc:
            messages.error(request, _messages(exc))
            return redirect("unittrust:distribution", pk=pk)

        messages.success(request, "Profit distribution deleted. The deletion is kept in the audit log.")
        return redirect("unittrust:distributions")


class InvestmentCloseView(LoginRequiredMixin, View):
    http_method_names = ["post"]

    def post(self, request, pk):

        investment = get_object_or_404(Investment, pk=pk)

        try:
            close_investment(investment, request.user)
        except InvestmentError as exc:
            messages.error(request, _messages(exc))
        else:
            messages.success(request, "Investment closed.")

        return redirect("unittrust:detail", pk=pk)


class DistributionListView(LoginRequiredMixin, View):
    def get(self, request):

        if not request.user.has_financial_view_access:
            raise PermissionDenied("You do not have permission to view profit distributions.")

        return render(
            request,
            "unittrust/distribution_list.html",
            {
                "distributions": ProfitDistribution.objects.select_related("cycle", "created_by"),
                "form": DistributionDraftForm(),
                "can_draft": request.user.has_financial_write_access,
            },
        )

    def post(self, request):

        form = DistributionDraftForm(request.POST)

        if form.is_valid():
            try:
                distribution = draft_distribution(
                    request.user, form.cleaned_data["total_amount"], form.cleaned_data["description"]
                )
            except InvestmentError as exc:
                messages.error(request, _messages(exc))
            else:
                messages.success(request, "Draft created. Review who is eligible, then ask the Chairperson or Administrator to approve it.")
                return redirect("unittrust:distribution", pk=distribution.pk)
        else:
            messages.error(request, "Enter where the profit came from and the amount to share.")

        return redirect("unittrust:distributions")


class DistributionDetailView(LoginRequiredMixin, View):
    def get(self, request, pk):

        if not request.user.has_financial_view_access:
            raise PermissionDenied("You do not have permission to view profit distributions.")

        distribution = get_object_or_404(ProfitDistribution.objects.select_related("cycle", "created_by", "approved_by"), pk=pk)
        user = request.user
        draft = distribution.status == ProfitDistribution.Status.DRAFT

        return render(
            request,
            "unittrust/distribution_detail.html",
            {
                "distribution": distribution,
                "lines": distribution.lines.select_related("member"),
                "can_approve": draft and user.has_chairperson_access and distribution.created_by_id != user.pk,
                "can_pay": distribution.status == "APPROVED" and user.has_financial_write_access,
                "can_cancel": distribution.status in ("DRAFT", "APPROVED"),
                "reason_form": ReasonForm(),
            },
        )


class DistributionActionView(LoginRequiredMixin, View):
    http_method_names = ["post"]

    def post(self, request, pk, action):

        distribution = get_object_or_404(ProfitDistribution, pk=pk)

        try:
            if action == "approve":
                approve_distribution(distribution, request.user, request.POST.get("resolution_reference", ""))
                messages.success(request, "Approved. The shares are now fixed.")
            elif action == "pay":
                mark_distribution_paid(distribution, request.user)
                messages.success(request, "Marked as paid.")
            elif action == "cancel":
                cancel_distribution(distribution, request.user, request.POST.get("reason", ""))
                messages.success(request, "Cancelled.")
            else:
                raise PermissionDenied
        except InvestmentError as exc:
            messages.error(request, _messages(exc))

        return redirect("unittrust:distribution", pk=pk)



class ProfitView(LoginRequiredMixin, View):
    """
    Management (Administrator, Treasurer, Chairperson) see the profit of a cycle by source and
    every member's share. A member sees only their own share, and only of published statements.
    """

    def get(self, request):
        from apps.cycles.models import Cycle

        user = request.user

        if not user.has_financial_view_access:
            return render(request, "unittrust/profit_member.html", {
                "mine": member_profit(user.member) if user.member_id else None})

        cycles = Cycle.objects.exclude(status=Cycle.Status.UPCOMING).order_by("-start_date")
        chosen = request.GET.get("cycle")
        cycle = cycles.filter(pk=chosen).first() if chosen else None
        cycle = cycle or cycles.filter(status=Cycle.Status.ACTIVE).first() or cycles.first()

        statement = (
            ProfitStatement.objects.filter(cycle=cycle)
            .select_related("captured_by", "published_by").first() if cycle else None
        )

        return render(request, "unittrust/profit.html", {
            "cycles": cycles,
            "cycle": cycle,
            "live": profit_figures(cycle) if cycle else None,
            "statement": statement,
            "shares": statement.shares.select_related("member") if statement else [],
            "can_capture": user.has_financial_write_access,
            "can_publish": user.has_chairperson_access,
            "can_delete": user.can_delete_records,
            "mine": member_profit(user.member) if user.member_id else None,
        })


class ProfitActionView(LoginRequiredMixin, View):
    http_method_names = ["post"]

    def post(self, request, pk, action):
        from apps.cycles.models import Cycle

        cycle = get_object_or_404(Cycle, pk=pk)
        back = redirect(f"/investments/profit/?cycle={cycle.pk}")

        try:
            if action == "capture":
                statement = capture_profit(cycle, request.user)
                messages.success(
                    request,
                    "Profit captured. It is visible to management; publish it for members to see their share.",
                )
            else:
                statement = get_object_or_404(ProfitStatement, cycle=cycle)
                if action == "publish":
                    publish_profit(statement, request.user)
                    messages.success(request, "Published. Every member can now see their own share.")
                elif action == "unpublish":
                    unpublish_profit(statement, request.user)
                    messages.success(request, "Withdrawn from members.")
                elif action == "delete":
                    delete_profit_statement(statement, request.user, request.POST.get("reason", ""))
                    messages.success(request, "Profit statement deleted. A copy is kept in the audit log.")
                else:
                    raise PermissionDenied
        except InvestmentError as exc:
            messages.error(request, _messages(exc))

        return back
