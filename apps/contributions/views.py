from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.shortcuts import redirect
from django.urls import reverse_lazy
from django.views.generic import CreateView

from apps.accounts.models import User
from apps.cycles.models import Cycle

from .forms import ContributionForm
from .models import Contribution
from .services import ContributionError, record_contribution


class ContributionCreateView(LoginRequiredMixin, CreateView):
    """
    Allows the Treasurer or Administrator to record a contribution.

    The active cycle and the logged-in user are assigned automatically.

    All business rules are delegated to the Contribution Service.
    """

    model = Contribution
    form_class = ContributionForm
    template_name = "contributions/contribution_form.html"

    success_url = reverse_lazy("contributions:create")

    def dispatch(self, request, *args, **kwargs):
        """
        Restrict access to Administrators and Treasurers.
        """

        if request.user.role not in (
            User.Role.ADMINISTRATOR,
            User.Role.TREASURER,
        ):
            messages.error(
                request,
                "You do not have permission to record contributions.",
            )
            return redirect("dashboard")

        return super().dispatch(request, *args, **kwargs)

    def get_active_cycle(self):
        """
        Return the current active cycle.
        """

        return Cycle.objects.filter(
            status=Cycle.Status.ACTIVE
        ).first()

    def get_context_data(self, **kwargs):
        """
        Add the active cycle to the template.
        """

        context = super().get_context_data(**kwargs)
        context["active_cycle"] = self.get_active_cycle()
        return context

    def form_valid(self, form):
        """
        Record the contribution through the service layer.
        """

        active_cycle = self.get_active_cycle()

        if active_cycle is None:
            messages.error(
                self.request,
                "There is no ACTIVE cycle."
            )
            return redirect("dashboard")

        contribution = form.save(commit=False)

        contribution.cycle = active_cycle
        contribution.recorded_by = self.request.user

        try:
            record_contribution(contribution)

        except ContributionError as exc:
            form.add_error(None, exc)
            return self.form_invalid(form)

        except Exception as exc:
            form.add_error(
                None,
                str(exc),
            )
            return self.form_invalid(form)

        messages.success(
            self.request,
            "Contribution recorded successfully."
        )

        return redirect(self.get_success_url())