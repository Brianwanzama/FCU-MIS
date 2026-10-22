from django.contrib.auth.decorators import login_required
from django.db import connection
from django.http import JsonResponse
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

    from apps.loans.models import Loan, LoanApplication
    from apps.loans.services import member_loan_summary

    user = request.user
    loan_card = {}

    if user.member_id:
        mine = member_loan_summary(user.member)
        loan_card["mine"] = {
            "outstanding": mine["balance"],
            "loans": [(loan, loan.display_status()) for loan in mine["loans"] if loan.status != "SETTLED"],
        }

    if user.has_financial_view_access:
        open_loans = [l for l in Loan.objects.exclude(status="SETTLED").prefetch_related("installments", "repayments")]
        loan_card["officer"] = {
            "pending_applications": LoanApplication.objects.filter(status=LoanApplication.Status.SUBMITTED).count(),
            "active_loans": len(open_loans),
            "outstanding": sum((l.outstanding for l in open_loans), 0),
            "overdue": sum(1 for l in open_loans if l.display_status() in ("Overdue", "Defaulted")),
        }

    context = {
        "loan_card": loan_card,
        "user": request.user,
        "member_count": Member.objects.count(),
        "active_member_count": Member.objects.filter(
            status=Member.Status.ACTIVE
        ).count(),
        "current_cycle": active_cycle,
        "summary": summary,
        "recent_contributions": (
            Contribution.objects
            .filter(reversed_at__isnull=True)
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


def healthz(request):
    """
    Liveness and database check for the hosting platform. No login, and it says
    nothing about the system beyond "ok" or "database unavailable".
    """

    try:
        connection.ensure_connection()
    except Exception:
        return JsonResponse({"status": "database unavailable"}, status=503)

    return JsonResponse({"status": "ok"})
