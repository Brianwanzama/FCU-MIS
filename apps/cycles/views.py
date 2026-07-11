"""
Cycle Management views - Administrator only (FR-3, brief: SECURITY).

Access is enforced with the existing RBAC primitives from apps.accounts, server
side. Hiding the nav link is presentation; `role_required` is the actual control.
"""

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db.models import Count
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from apps.accounts.models import User
from apps.accounts.permissions import role_required

from . import services
from .forms import CycleCloseForm, CycleForm, CycleReopenForm
from .models import Cycle, FinancialYear

ADMIN_ONLY = role_required(User.Role.ADMINISTRATOR)


@ADMIN_ONLY
def cycle_list(request):
    cycles = Cycle.objects.select_related("financial_year").all()
    financial_years = (
        FinancialYear.objects.annotate(cycle_count=Count("cycles")).order_by("-year")
    )
    return render(
        request,
        "cycles/cycle_list.html",
        {
            "cycles": cycles,
            "financial_years": financial_years,
            "active_cycle": services.get_active_cycle(),
        },
    )


@ADMIN_ONLY
def cycle_detail(request, pk):
    cycle = get_object_or_404(Cycle.objects.select_related("financial_year"), pk=pk)
    deletable, delete_blocker = cycle.is_deletable()
    return render(
        request,
        "cycles/cycle_detail.html",
        {
            "cycle": cycle,
            "deletable": deletable,
            "delete_blocker": delete_blocker,
            "active_cycle": services.get_active_cycle(),
        },
    )


@ADMIN_ONLY
def cycle_create(request):
    if request.method == "POST":
        form = CycleForm(request.POST)
        if form.is_valid():
            try:
                cycle = services.create_cycle(form.instance, actor=request.user)
            except ValidationError as exc:
                form.add_error(None, exc)
            else:
                messages.success(
                    request,
                    f"{cycle} created as Upcoming. Activate it when the cycle begins.",
                )
                return redirect("cycles:detail", pk=cycle.pk)
    else:
        form = CycleForm(initial={"cycle_number": _next_cycle_number()})

    return render(request, "cycles/cycle_form.html", {"form": form, "cycle": None})


@ADMIN_ONLY
def cycle_edit(request, pk):
    cycle = get_object_or_404(Cycle, pk=pk)

    if cycle.is_locked:
        messages.error(
            request,
            f"{cycle} is closed and locked. Reopen it before editing.",
        )
        return redirect("cycles:detail", pk=cycle.pk)

    if request.method == "POST":
        # Snapshot BEFORE the form binds, or the audit trail records the new
        # values as both the before and the after.
        old_snapshot = services._snapshot(Cycle.objects.get(pk=cycle.pk))
        form = CycleForm(request.POST, instance=cycle)
        if form.is_valid():
            try:
                services.update_cycle(form.instance, old_snapshot, actor=request.user)
            except ValidationError as exc:
                form.add_error(None, exc)
            else:
                messages.success(request, f"{cycle} updated.")
                return redirect("cycles:detail", pk=cycle.pk)
    else:
        form = CycleForm(instance=cycle)

    return render(request, "cycles/cycle_form.html", {"form": form, "cycle": cycle})


@ADMIN_ONLY
def cycle_activate(request, pk):
    cycle = get_object_or_404(Cycle, pk=pk)

    if request.method != "POST":
        return redirect("cycles:detail", pk=cycle.pk)

    try:
        services.activate_cycle(cycle, actor=request.user)
    except ValidationError as exc:
        messages.error(request, "; ".join(exc.messages))
    else:
        messages.success(request, f"{cycle} is now the active cycle.")

    return redirect("cycles:detail", pk=cycle.pk)


@ADMIN_ONLY
def cycle_close(request, pk):
    cycle = get_object_or_404(Cycle, pk=pk)
    warnings = cycle.blocking_issues_for_close() if cycle.status == Cycle.Status.ACTIVE else []

    if request.method == "POST":
        form = CycleCloseForm(request.POST)
        if form.is_valid():
            try:
                services.close_cycle(cycle, actor=request.user, acknowledged_warnings=warnings)
            except ValidationError as exc:
                messages.error(request, "; ".join(exc.messages))
            else:
                messages.success(
                    request,
                    f"{cycle} is closed and locked. No financial records can be written "
                    f"against it until it is reopened.",
                )
                return redirect("cycles:detail", pk=cycle.pk)
    else:
        form = CycleCloseForm()

    return render(
        request,
        "cycles/cycle_close.html",
        {"cycle": cycle, "form": form, "warnings": warnings},
    )


@ADMIN_ONLY
def cycle_reopen(request, pk):
    cycle = get_object_or_404(Cycle, pk=pk)

    if request.method == "POST":
        form = CycleReopenForm(request.POST)
        if form.is_valid():
            try:
                services.reopen_cycle(cycle, actor=request.user, reason=form.cleaned_data["reason"])
            except ValidationError as exc:
                messages.error(request, "; ".join(exc.messages))
            else:
                messages.warning(
                    request,
                    f"{cycle} has been REOPENED and is active again. This is logged.",
                )
                return redirect("cycles:detail", pk=cycle.pk)
    else:
        form = CycleReopenForm()

    return render(request, "cycles/cycle_reopen.html", {"cycle": cycle, "form": form})


@ADMIN_ONLY
def cycle_delete(request, pk):
    cycle = get_object_or_404(Cycle, pk=pk)
    deletable, blocker = cycle.is_deletable()

    if request.method == "POST":
        try:
            repr_ = services.delete_cycle(cycle, actor=request.user)
        except ValidationError as exc:
            messages.error(request, "; ".join(exc.messages))
            return redirect("cycles:detail", pk=cycle.pk)
        messages.success(request, f"{repr_} deleted.")
        return redirect("cycles:list")

    return render(
        request,
        "cycles/cycle_confirm_delete.html",
        {"cycle": cycle, "deletable": deletable, "blocker": blocker},
    )


def _next_cycle_number():
    last = Cycle.objects.order_by("-cycle_number").first()
    return (last.cycle_number + 1) if last else 1
