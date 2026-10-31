from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views import View

from apps.cycles.models import Cycle

from .forms import ExpenseEditForm, ExpenseForm, ReasonForm
from .models import Expense
from .services import (
    ExpenseError,
    _snapshot,
    delete_expense,
    expense_summary,
    record_expense,
    update_expense,
)


class ExpenseAccessMixin(LoginRequiredMixin):
    """Officers who may see the financial records can see expenses."""

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated and not request.user.has_financial_view_access:
            raise PermissionDenied("You do not have permission to view expenses.")
        return super().dispatch(request, *args, **kwargs)


class ExpenseListView(ExpenseAccessMixin, View):
    """Expenses of one cycle (the active cycle unless another is chosen)."""

    def get(self, request):
        cycles = Cycle.objects.exclude(status=Cycle.Status.UPCOMING).order_by("-start_date")
        chosen = request.GET.get("cycle")
        cycle = cycles.filter(pk=chosen).first() if chosen else None
        cycle = cycle or cycles.filter(status=Cycle.Status.ACTIVE).first() or cycles.first()

        summary = expense_summary(cycle) if cycle else None

        return render(request, "expenses/expense_list.html", {
            "cycles": cycles,
            "cycle": cycle,
            "summary": summary,
            "can_record": request.user.has_financial_write_access and cycle is not None and cycle.is_open,
            "can_delete": request.user.can_delete_records and cycle is not None and cycle.is_open,
        })


class ExpenseCreateView(ExpenseAccessMixin, View):
    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated and not request.user.has_financial_write_access:
            raise PermissionDenied("Only the Treasurer or Administrator can record expenses.")
        return super().dispatch(request, *args, **kwargs)

    def cycle(self):
        return Cycle.objects.filter(status=Cycle.Status.ACTIVE).first()

    def get(self, request):
        return render(request, "expenses/expense_form.html", {
            "form": ExpenseForm(initial={"expense_date": timezone.localdate()}), "cycle": self.cycle(), "expense": None})

    def post(self, request):
        cycle = self.cycle()
        form = ExpenseForm(request.POST)

        if cycle is None:
            form.add_error(None, "There is no ACTIVE cycle to record this expense against.")
        elif form.is_valid():
            expense = form.save(commit=False)
            expense.cycle = cycle
            try:
                record_expense(expense, request.user)
            except ExpenseError as exc:
                form.add_error(None, " ".join(exc.messages))
            else:
                messages.success(request, "Expense recorded.")
                return redirect(f"/expenses/?cycle={cycle.pk}")

        return render(request, "expenses/expense_form.html", {"form": form, "cycle": cycle, "expense": None})


class ExpenseEditView(ExpenseAccessMixin, View):
    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated and not request.user.has_financial_write_access:
            raise PermissionDenied("Only the Treasurer or Administrator can change expenses.")
        return super().dispatch(request, *args, **kwargs)

    def get(self, request, pk):
        expense = get_object_or_404(Expense, pk=pk)
        return render(request, "expenses/expense_form.html", {
            "form": ExpenseEditForm(instance=expense), "cycle": expense.cycle, "expense": expense})

    def post(self, request, pk):
        expense = get_object_or_404(Expense, pk=pk)
        old = _snapshot(expense)
        form = ExpenseEditForm(request.POST, instance=expense)

        if form.is_valid():
            try:
                update_expense(form.instance, old, request.user, form.cleaned_data["reason"])
            except ExpenseError as exc:
                form.add_error(None, " ".join(exc.messages))
            else:
                messages.success(request, "Expense updated.")
                return redirect(f"/expenses/?cycle={expense.cycle_id}")

        return render(request, "expenses/expense_form.html", {"form": form, "cycle": expense.cycle, "expense": expense})


class ExpenseDeleteView(ExpenseAccessMixin, View):
    http_method_names = ["post"]

    def post(self, request, pk):
        expense = get_object_or_404(Expense, pk=pk)
        cycle_id = expense.cycle_id
        form = ReasonForm(request.POST)

        try:
            delete_expense(expense, request.user, form.cleaned_data["reason"] if form.is_valid() else "")
        except ExpenseError as exc:
            messages.error(request, " ".join(exc.messages))
        else:
            messages.success(request, "Expense deleted. A copy is kept in the audit log.")

        return redirect(f"/expenses/?cycle={cycle_id}")
