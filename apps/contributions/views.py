from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied
from django.db.models import Exists, OuterRef, Q
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse, reverse_lazy
from django.views import View
from django.views.generic import CreateView, DetailView, FormView, ListView, UpdateView

from apps.auditlog.models import AuditLog
from apps.cycles.models import Cycle

from .forms import (
    ContributionEditForm,
    ContributionForm,
    ReversalDecisionForm,
    ReversalRequestForm,
)
from .models import Contribution, ContributionReversal
from .services import (
    ContributionError,
    _snapshot,
    approve_reversal,
    cycle_contribution_summary,
    record_contribution,
    reject_reversal,
    request_reversal,
    update_contribution,
)


class FinancialAccessMixin(LoginRequiredMixin):
    """
    Who may open this page. Administrators, Treasurers and Chairpersons can
    view every contribution; stricter actions set `permission` to the User
    helper that must be true (e.g. "has_financial_write_access" to record).
    """

    permission = "has_financial_view_access"

    def dispatch(self, request, *args, **kwargs):

        if not request.user.is_authenticated:
            return self.handle_no_permission()

        if not getattr(request.user, self.permission):
            messages.error(
                request,
                "You do not have permission to access this page.",
            )
            return redirect("core:dashboard")

        return super().dispatch(request, *args, **kwargs)


class ContributionListView(FinancialAccessMixin, ListView):
    """
    Displays the Contribution Register.
    """

    model = Contribution
    template_name = "contributions/contribution_list.html"
    context_object_name = "contributions"
    paginate_by = 20

    def get_queryset(self):

        queryset = (
            Contribution.objects
            .select_related(
                "member",
                "cycle",
                "recorded_by",
            )
            .annotate(
                has_pending_reversal=Exists(
                    ContributionReversal.objects.filter(
                        contribution=OuterRef("pk"),
                        status=ContributionReversal.Status.PENDING,
                    )
                )
            )
            .order_by("-payment_date", "-id")
        )

        search = self.request.GET.get("search")

        if search:

            queryset = queryset.filter(
                Q(member__member_code__icontains=search)
                |
                Q(member__full_name__icontains=search)
            )

        cycle = self.request.GET.get("cycle")

        if cycle:
            queryset = queryset.filter(cycle_id=cycle)

        contribution_type = self.request.GET.get("type")

        if contribution_type:
            queryset = queryset.filter(
                contribution_type=contribution_type
            )

        payment_method = self.request.GET.get("method")

        if payment_method:
            queryset = queryset.filter(
                payment_method=payment_method
            )

        return queryset

    def get_context_data(self, **kwargs):

        context = super().get_context_data(**kwargs)

        active_cycle = (
            Cycle.objects
            .filter(status=Cycle.Status.ACTIVE)
            .first()
        )

        context["active_cycle"] = active_cycle
        context["cycles"] = Cycle.objects.order_by("-start_date")

        context["search"] = self.request.GET.get("search", "")
        context["selected_cycle"] = self.request.GET.get("cycle", "")
        context["selected_type"] = self.request.GET.get("type", "")
        context["selected_method"] = self.request.GET.get("method", "")

        if active_cycle:

            context["summary"] = cycle_contribution_summary(
                active_cycle
            )

        else:

            context["summary"] = {
                "expected": 0,
                "collected": 0,
                "outstanding": 0,
                "collection_rate": 0,
            }

        return context


class ContributionCreateView(FinancialAccessMixin, CreateView):
    """
    Record a new contribution.
    """

    permission = "has_financial_write_access"

    model = Contribution
    form_class = ContributionForm
    template_name = "contributions/contribution_form.html"

    success_url = reverse_lazy("contributions:list")

    def get_active_cycle(self):

        return (
            Cycle.objects
            .filter(status=Cycle.Status.ACTIVE)
            .first()
        )

    def get_context_data(self, **kwargs):

        context = super().get_context_data(**kwargs)
        context["active_cycle"] = self.get_active_cycle()

        return context

    def form_valid(self, form):

        active_cycle = self.get_active_cycle()

        if active_cycle is None:

            messages.error(
                self.request,
                "There is no ACTIVE cycle."
            )

            return redirect("core:dashboard")

        contribution = form.save(commit=False)

        contribution.cycle = active_cycle
        contribution.recorded_by = self.request.user

        try:

            record_contribution(
                contribution,
                actor=self.request.user,
            )

        except ContributionError as exc:

            form.add_error(None, exc)

            return self.form_invalid(form)

        except Exception as exc:

            form.add_error(None, str(exc))

            return self.form_invalid(form)

        messages.success(
            self.request,
            "Contribution recorded successfully."
        )

        return redirect(self.success_url)


class ContributionDetailView(FinancialAccessMixin, DetailView):
    """One contribution, its reversal state and its full audit history."""

    model = Contribution
    template_name = "contributions/contribution_detail.html"
    context_object_name = "contribution"

    def get_context_data(self, **kwargs):

        context = super().get_context_data(**kwargs)
        contribution = self.object

        context["history"] = AuditLog.objects.filter(
            model_name="Contribution",
            object_id=str(contribution.pk),
        ).select_related("actor").order_by("-timestamp")
        context["reversals"] = contribution.reversals.select_related(
            "requested_by", "decided_by"
        )
        context["pending_reversal"] = contribution.pending_reversal
        context["is_changeable"] = _is_changeable(contribution)

        return context


def _is_changeable(contribution):
    return not (
        contribution.is_reversed
        or contribution.pending_reversal
        or contribution.cycle.is_locked
    )


class ContributionUpdateView(FinancialAccessMixin, UpdateView):
    """Correct a contribution. A reason is mandatory and audited."""

    permission = "can_edit_contributions"
    model = Contribution
    form_class = ContributionEditForm
    template_name = "contributions/contribution_edit.html"
    context_object_name = "contribution"

    def get_object(self, queryset=None):

        contribution = super().get_object(queryset)
        # Taken before the form touches the instance, for the audit log.
        self.old_snapshot = _snapshot(contribution)

        return contribution

    def get_success_url(self):
        return reverse("contributions:detail", args=[self.object.pk])

    def form_valid(self, form):

        try:
            update_contribution(
                form.instance,
                self.old_snapshot,
                actor=self.request.user,
                reason=form.cleaned_data["reason"],
            )
        except ContributionError as exc:
            form.add_error(None, exc)
            return self.form_invalid(form)

        self.object = form.instance
        messages.success(self.request, "Contribution updated.")

        return redirect(self.get_success_url())


class ReversalRequestView(FinancialAccessMixin, FormView):
    """Ask for a contribution to be reversed (needs approval)."""

    permission = "can_request_reversal"
    form_class = ReversalRequestForm
    template_name = "contributions/reversal_request.html"

    def dispatch(self, request, *args, **kwargs):

        self.contribution = get_object_or_404(Contribution, pk=kwargs["pk"])

        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):

        context = super().get_context_data(**kwargs)
        context["contribution"] = self.contribution

        return context

    def form_valid(self, form):

        try:
            request_reversal(
                self.contribution,
                actor=self.request.user,
                reason=form.cleaned_data["reason"],
            )
        except ContributionError as exc:
            form.add_error(None, exc)
            return self.form_invalid(form)

        messages.success(
            self.request,
            "Reversal requested. It takes effect once the Administrator or "
            "Chairperson approves it.",
        )

        return redirect("contributions:detail", pk=self.contribution.pk)


class ReversalListView(FinancialAccessMixin, ListView):
    """Reversal requests: pending first, then the decided ones."""

    model = ContributionReversal
    template_name = "contributions/reversal_list.html"
    context_object_name = "reversals"
    paginate_by = 30

    def get_queryset(self):

        return (
            ContributionReversal.objects
            .select_related(
                "contribution__member",
                "contribution__cycle",
                "requested_by",
                "decided_by",
            )
            .order_by(
                # PENDING sorts before APPROVED/REJECTED
                "status",
                "-requested_at",
            )
        )


class ReversalDecisionView(FinancialAccessMixin, View):
    """Approve or reject a reversal request (POST only)."""

    permission = "can_approve_reversal"
    http_method_names = ["post"]

    def post(self, request, pk, decision):

        reversal = get_object_or_404(ContributionReversal, pk=pk)
        form = ReversalDecisionForm(request.POST)
        note = form.cleaned_data["note"] if form.is_valid() else ""

        try:
            if decision == "approve":
                approve_reversal(reversal, actor=request.user, note=note)
                messages.success(request, "Reversal approved. The payment no longer counts.")
            elif decision == "reject":
                reject_reversal(reversal, actor=request.user, note=note)
                messages.success(request, "Reversal rejected. The payment is unchanged.")
            else:
                raise PermissionDenied
        except ContributionError as exc:
            messages.error(request, "; ".join(exc.messages))

        return redirect("contributions:reversals")
