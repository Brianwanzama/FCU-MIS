from django.contrib.auth.mixins import LoginRequiredMixin
from django.views.generic import DetailView, ListView

from apps.cycles.services import get_active_cycle

from .models import Member
from .services import (
    member_financial_summary,
    member_transaction_history,
)


class MemberListView(LoginRequiredMixin, ListView):
    """
    Displays the FCU Member Register.
    """

    model = Member
    template_name = "members/member_list.html"
    context_object_name = "members"
    ordering = ["member_code"]


class MemberDetailView(LoginRequiredMixin, DetailView):
    """
    Displays the Member Financial Dashboard.
    """

    model = Member
    template_name = "members/member_detail.html"
    context_object_name = "member"

    slug_field = "member_code"
    slug_url_kwarg = "member_code"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        cycle = get_active_cycle()

        context["current_cycle"] = cycle

        if cycle:
            context["summary"] = member_financial_summary(
                self.object,
                cycle,
            )
        else:
            context["summary"] = None

        context["transactions"] = (
            member_transaction_history(self.object)[:20]
        )

        return context