from django.contrib.auth.decorators import login_required
from django.shortcuts import render


@login_required
def dashboard(request):
    """
    Role-aware landing page (FR-12/FR-13/FR-14). Financial totals (contribution
    progress, loan balance, cash position, etc.) are wired in once the Cycles,
    Contributions and Loans modules land in their own roadmap phases — this
    view is deliberately honest about what's live today rather than showing
    placeholder numbers that look real.
    """
    return render(request, "core/dashboard.html", {"user": request.user})


@login_required
def home_redirect(request):
    from django.shortcuts import redirect

    return redirect("core:dashboard")
