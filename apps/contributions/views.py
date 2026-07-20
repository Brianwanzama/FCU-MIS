from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import Q
from django.shortcuts import redirect
from django.urls import reverse_lazy
from django.views.generic import CreateView, ListView

from apps.accounts.models import User
from apps.cycles.models import Cycle

from .forms import ContributionForm
from .models import Contribution
from .services import (
    ContributionError,
    cycle_contribution_summary,
    record_contribution,
)


class FinancialAccessMixin(LoginRequiredMixin):
    """
    Restricts access to Administrators and Treasurers.
    """

    def dispatch(self, request, *args, **kwargs):

        if request.user.role not in (
            User.Role.ADMINISTRATOR,
            User.Role.TREASURER,
        ):
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
            .order_by("-payment_date", "-id")
        )

        search = self.request.GET.get("search")

        if search:

            queryset = queryset.filter(
                Q(member__member_code__icontains=search)
                |
                Q(member__first_name__icontains=search)
                |
                Q(member__last_name__icontains=search)
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
            }

        return context


class ContributionCreateView(FinancialAccessMixin, CreateView):
    """
    Record a new contribution.
    """

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

            record_contribution(contribution)

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