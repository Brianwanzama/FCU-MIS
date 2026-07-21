from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render

from apps.members.models import Member
from apps.cycles.services import get_active_cycle
from apps.contributions.models import Contribution
from apps.contributions.services import cycle_contribution_summary


@login_required
def dashboard(request):
    """
    FCU Dashboard
    """

    active_cycle = get_active_cycle()

    summary = None

    if active_cycle:
        summary = cycle_contribution_summary(active_cycle)

    context = {
        "user": request.user,
        "member_count": Member.objects.count(),
        "active_member_count": Member.objects.filter(
            status=Member.Status.ACTIVE
        ).count(),
        "current_cycle": active_cycle,
        "summary": summary,
        "recent_contributions": (
            Contribution.objects
            .select_related(
                "member",
                "recorded_by",
            )
            .order_by("-payment_date", "-created_at")[:5]
        ),
    }

    return render(
        request,
        "core/dashboard.html",
        context,
    )


@login_required
def home_redirect(request):
    return redirect("core:dashboard")