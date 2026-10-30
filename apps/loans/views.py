import mimetypes
from datetime import date

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied
from django.http import FileResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views import View

from apps.documents.models import DocumentTemplate

from .forms import (
    DecisionForm,
    DisbursementForm,
    LoanApplicationForm,
    OffsystemLoanForm,
    RepaymentForm,
    ReversalReasonForm,
    ReviewForm,
    UndertakingUploadForm,
)
from .models import Loan, LoanApplication, LoanRepayment, SignedUndertaking
from .services import (
    LoanError,
    approve_application,
    check_eligibility,
    current_undertaking,
    delete_application,
    delete_loan,
    delete_repayment,
    delete_undertaking,
    disburse_loan,
    loan_schedule,
    overdue_loans,
    record_offsystem_loan,
    recovery_enabled,
    recovery_preview,
    record_repayment,
    reject_application,
    reverse_repayment,
    review_undertaking,
    run_recovery,
    submit_application,
    upload_undertaking,
    withdraw_application,
)


def _form_documents():
    """The current blank forms a member needs to download."""

    return {
        "application": DocumentTemplate.objects.filter(
            doc_type=DocumentTemplate.DocType.LOAN_APPLICATION_FORM, is_current=True
        ).first(),
        "declaration": DocumentTemplate.objects.filter(
            doc_type=DocumentTemplate.DocType.MEMBER_DECLARATION, is_current=True
        ).first(),
    }


def _error_messages(exc):
    return exc.messages if hasattr(exc, "messages") else [str(exc)]


class MemberRequiredMixin(LoginRequiredMixin):
    """The page belongs to a member: it needs an account linked to a member record."""

    def dispatch(self, request, *args, **kwargs):

        if not request.user.is_authenticated:
            return self.handle_no_permission()

        if request.user.member_id is None:
            messages.info(request, "This account is not linked to a member record.")
            return redirect("core:dashboard")

        return super().dispatch(request, *args, **kwargs)


class MyLoansView(MemberRequiredMixin, View):
    """A member's own loan page: forms to download, eligibility, upload, and applications."""

    def get(self, request):

        member = request.user.member

        return render(
            request,
            "loans/my_loans.html",
            {
                "member": member,
                "documents": _form_documents(),
                "result": check_eligibility(member),
                "undertaking": current_undertaking(member),
                "undertaking_form": UndertakingUploadForm(),
                "applications": member.loan_applications.select_related("cycle"),
                "loans": member.loans.select_related("application"),
            },
        )


class UndertakingUploadView(MemberRequiredMixin, View):
    http_method_names = ["post"]

    def post(self, request):

        form = UndertakingUploadForm(request.POST, request.FILES)

        if form.is_valid():
            try:
                upload_undertaking(request.user.member, form.cleaned_data["file"], request.user)
            except LoanError as exc:
                messages.error(request, " ".join(_error_messages(exc)))
            else:
                messages.success(
                    request,
                    "Your signed Declaration was uploaded. An officer will verify it.",
                )
        else:
            messages.error(request, "Choose the scanned, signed Declaration to upload.")

        return redirect("loans:mine")


class ApplyView(MemberRequiredMixin, View):
    """Apply for a loan: terms, the filled form, and (once) the signed Declaration."""

    template_name = "loans/apply.html"

    def render_page(self, request, form, amount=None, months=None):

        member = request.user.member
        result = check_eligibility(member, amount=amount, months=months)
        schedule = None

        if amount and months and result["max_months"] >= months:
            schedule = loan_schedule(amount, months, timezone.localdate())

        return render(
            request,
            self.template_name,
            {
                "form": form,
                "member": member,
                "result": result,
                "schedule": schedule,
                "documents": _form_documents(),
                "has_undertaking": result["undertaking"] is not None,
            },
        )

    def get(self, request):

        member = request.user.member
        form = LoanApplicationForm(member_has_undertaking=current_undertaking(member) is not None)

        return self.render_page(request, form)

    def post(self, request):

        member = request.user.member
        previewing = "preview" in request.POST

        form = LoanApplicationForm(
            request.POST,
            request.FILES,
            member_has_undertaking=current_undertaking(member) is not None,
        )

        if previewing:
            # Browsers do not keep file choices between submits, so a preview
            # looks only at the loan terms.
            form.fields["application_form"].required = False

        valid = form.is_valid()
        amount = form.cleaned_data.get("amount")
        months = form.cleaned_data.get("period_months")

        if previewing or not valid:
            return self.render_page(request, form, amount, months)

        try:
            application = submit_application(
                member,
                amount,
                months,
                form.cleaned_data["application_form"],
                actor=request.user,
                undertaking_upload=form.cleaned_data.get("undertaking_file"),
            )
        except LoanError as exc:
            for message in _error_messages(exc):
                form.add_error(None, message)
            return self.render_page(request, form, amount, months)

        messages.success(
            request,
            f"Application {application.reference} submitted. You will see the decision on this page.",
        )

        return redirect("loans:detail", pk=application.pk)


class ApplicationAccessMixin(LoginRequiredMixin):
    def get_application(self, pk):

        application = get_object_or_404(
            LoanApplication.objects.select_related("member", "cycle", "undertaking", "decided_by"),
            pk=pk,
        )
        user = self.request.user

        if not (user.has_financial_view_access or user.member_id == application.member_id):
            raise PermissionDenied("You may only view your own loan applications.")

        return application


class ApplicationDetailView(ApplicationAccessMixin, View):

    def get(self, request, pk):

        application = self.get_application(pk)
        user = request.user
        open_ = application.status == LoanApplication.Status.SUBMITTED

        checks = None
        if open_ and user.has_financial_view_access:
            checks = check_eligibility(
                application.member,
                amount=application.amount_requested,
                months=application.period_months,
                ignoring=application,
                require_verified=True,
            )

        if application.status in (LoanApplication.Status.APPROVED, LoanApplication.Status.DISBURSED):
            schedule = [
                {**row, "due_date": date.fromisoformat(row["due_date"])}
                for row in application.approved_schedule
            ]
        else:
            schedule = loan_schedule(
                application.amount_approved or application.amount_requested,
                application.period_months,
                timezone.localdate(),
            )

        return render(
            request,
            "loans/application_detail.html",
            {
                "application": application,
                "checks": checks,
                "schedule": schedule,
                "schedule_is_final": application.status in (
                    LoanApplication.Status.APPROVED, LoanApplication.Status.DISBURSED
                ),
                "is_owner": user.member_id == application.member_id,
                "can_decide": open_ and user.can_approve_loans and user.member_id != application.member_id,
                "decision_form": DecisionForm(),
                "can_disburse": application.status == LoanApplication.Status.APPROVED
                and user.has_financial_write_access,
                "disbursement_form": DisbursementForm(),
            },
        )


class ApplicationListView(LoginRequiredMixin, View):
    """Every application, for the Administrator, Treasurer and Chairperson."""

    def get(self, request):

        if not request.user.has_financial_view_access:
            raise PermissionDenied("You do not have permission to view loan applications.")

        status = request.GET.get("status", "")
        applications = LoanApplication.objects.select_related("member", "cycle")

        if status in LoanApplication.Status.values:
            applications = applications.filter(status=status)

        return render(
            request,
            "loans/application_list.html",
            {
                "applications": applications.order_by("status", "-submitted_at")[:200],
                "status": status,
                "statuses": LoanApplication.Status.choices,
                "pending_undertakings": SignedUndertaking.objects.filter(
                    status=SignedUndertaking.Status.PENDING
                ).count(),
            },
        )


class ApplicationDecisionView(ApplicationAccessMixin, View):
    http_method_names = ["post"]

    def post(self, request, pk, decision):

        application = self.get_application(pk)
        form = DecisionForm(request.POST)
        data = form.cleaned_data if form.is_valid() else {}

        try:
            if decision == "approve":
                approve_application(
                    application, request.user,
                    amount_approved=data.get("amount_approved"), note=data.get("note", ""),
                )
                messages.success(request, f"{application.reference} approved.")
            elif decision == "reject":
                reject_application(application, request.user, data.get("note", ""))
                messages.success(request, f"{application.reference} rejected.")
            else:
                raise PermissionDenied
        except LoanError as exc:
            messages.error(request, " ".join(_error_messages(exc)))

        return redirect("loans:detail", pk=application.pk)


class ApplicationWithdrawView(ApplicationAccessMixin, View):
    http_method_names = ["post"]

    def post(self, request, pk):

        application = self.get_application(pk)

        try:
            withdraw_application(application, request.user)
        except LoanError as exc:
            messages.error(request, " ".join(_error_messages(exc)))
        else:
            messages.success(request, "Your application was withdrawn.")

        return redirect("loans:detail", pk=application.pk)


class UndertakingListView(LoginRequiredMixin, View):
    """Signed Declarations awaiting verification."""

    def get(self, request):

        if not request.user.can_verify_undertakings:
            raise PermissionDenied("You do not have permission to verify signed declarations.")

        return render(
            request,
            "loans/undertaking_list.html",
            {"undertakings": SignedUndertaking.objects.select_related("member", "reviewed_by")[:200]},
        )


class UndertakingReviewView(LoginRequiredMixin, View):
    http_method_names = ["post"]

    def post(self, request, pk, decision):

        undertaking = get_object_or_404(SignedUndertaking, pk=pk)
        form = ReviewForm(request.POST)
        note = form.cleaned_data["note"] if form.is_valid() else ""

        if decision not in ("verify", "reject"):
            raise PermissionDenied

        try:
            review_undertaking(undertaking, request.user, verified=decision == "verify", note=note)
        except LoanError as exc:
            messages.error(request, " ".join(_error_messages(exc)))
        else:
            messages.success(request, "Declaration " + ("verified." if decision == "verify" else "rejected."))

        return redirect("loans:undertakings")


class FileDownloadView(LoginRequiredMixin, View):
    """Hand out a private upload, but only to its owner or an authorised officer."""

    def get(self, request, kind, pk):

        user = request.user

        if kind == "application":
            obj = get_object_or_404(LoanApplication, pk=pk)
            allowed = user.has_financial_view_access or user.member_id == obj.member_id
            file, name = obj.application_form, obj.application_form_name
        elif kind == "undertaking":
            obj = get_object_or_404(SignedUndertaking, pk=pk)
            allowed = user.can_verify_undertakings or user.member_id == obj.member_id
            file, name = obj.file, obj.original_name
        else:
            raise PermissionDenied

        if not allowed:
            raise PermissionDenied("You may not open this file.")

        response = FileResponse(
            file.open("rb"),
            as_attachment=True,
            filename=name,
            content_type=mimetypes.guess_type(name)[0] or "application/octet-stream",
        )
        response["X-Content-Type-Options"] = "nosniff"

        return response


class DisburseView(ApplicationAccessMixin, View):
    http_method_names = ["post"]

    def post(self, request, pk):

        application = self.get_application(pk)
        form = DisbursementForm(request.POST)

        if not form.is_valid():
            messages.error(request, "Choose how the money was paid out.")
            return redirect("loans:detail", pk=application.pk)

        try:
            loan = disburse_loan(
                application,
                request.user,
                form.cleaned_data["payment_method"],
                reference=form.cleaned_data["reference"],
                disbursed_on=form.cleaned_data["disbursed_on"],
            )
        except LoanError as exc:
            messages.error(request, " ".join(_error_messages(exc)))
            return redirect("loans:detail", pk=application.pk)

        messages.success(request, f"{loan.reference} disbursed. The repayment schedule has started.")

        return redirect("loans:loan", pk=loan.pk)


class LoanAccessMixin(LoginRequiredMixin):
    def get_loan(self, pk):

        loan = get_object_or_404(
            Loan.objects.select_related("member", "application", "cycle", "disbursed_by"), pk=pk
        )
        user = self.request.user

        if not (user.has_financial_view_access or user.member_id == loan.member_id):
            raise PermissionDenied("You may only view your own loans.")

        return loan


class LoanDetailView(LoanAccessMixin, View):
    def get(self, request, pk):

        loan = self.get_loan(pk)
        user = request.user
        repayments = list(loan.repayments.select_related("recorded_by", "reversed_by"))
        today = timezone.localdate()

        return render(
            request,
            "loans/loan_detail.html",
            {
                "loan": loan,
                "installments": loan.installments.all(),
                "repayments": repayments,
                "status_label": loan.display_status(),
                "outstanding": loan.outstanding,
                "is_owner": user.member_id == loan.member_id,
                "can_record": user.has_financial_write_access and loan.status != Loan.Status.SETTLED,
                "can_reverse": user.can_approve_loans,
                "can_delete": user.can_delete_records,
                "repayment_form": RepaymentForm(initial={"payment_date": today}),
                "reversal_form": ReversalReasonForm(),
                "preview": recovery_preview(loan) if loan.status != Loan.Status.SETTLED else None,
                "today": today,
            },
        )


class RepaymentCreateView(LoanAccessMixin, View):
    http_method_names = ["post"]

    def post(self, request, pk):

        loan = self.get_loan(pk)
        form = RepaymentForm(request.POST)

        if not form.is_valid():
            messages.error(request, "Check the repayment details: " + "; ".join(
                f"{f}: {' '.join(e)}" for f, e in form.errors.items()
            ))
            return redirect("loans:loan", pk=loan.pk)

        try:
            record_repayment(
                loan,
                form.cleaned_data["amount"],
                form.cleaned_data["payment_date"],
                form.cleaned_data["payment_method"],
                request.user,
                reference=form.cleaned_data["reference"],
            )
        except LoanError as exc:
            messages.error(request, " ".join(_error_messages(exc)))
        else:
            messages.success(request, "Repayment recorded.")

        return redirect("loans:loan", pk=loan.pk)


class RepaymentReverseView(LoginRequiredMixin, View):
    http_method_names = ["post"]

    def post(self, request, pk):

        repayment = get_object_or_404(LoanRepayment.objects.select_related("loan"), pk=pk)
        form = ReversalReasonForm(request.POST)
        reason = form.cleaned_data["reason"] if form.is_valid() else ""

        try:
            reverse_repayment(repayment, request.user, reason)
        except LoanError as exc:
            messages.error(request, " ".join(_error_messages(exc)))
        else:
            messages.success(request, "Reversed. The loan balance has been restored.")

        return redirect("loans:loan", pk=repayment.loan_id)


class LoanRegisterView(LoginRequiredMixin, View):
    """All loans, the ones past their agreed period, and the recovery controls."""

    def get(self, request):

        if not request.user.has_financial_view_access:
            raise PermissionDenied("You do not have permission to view the loan register.")

        today = timezone.localdate()
        overdue = overdue_loans(today)

        return render(
            request,
            "loans/loan_register.html",
            {
                "loans": Loan.objects.select_related("member", "application").prefetch_related(
                    "installments", "repayments"
                )[:300],
                "overdue": [{"loan": loan, "preview": recovery_preview(loan)} for loan in overdue],
                "recovery_enabled": recovery_enabled(),
                "can_run": request.user.can_approve_loans,
                "can_add_offsystem": request.user.can_record_offsystem_loans,
                "today": today,
            },
        )


class RecoveryRunView(LoginRequiredMixin, View):
    """Run the overdue-loan recovery now (the nightly job runs the same code)."""

    http_method_names = ["post"]

    def post(self, request):

        if not request.user.can_approve_loans:
            raise PermissionDenied("Only the Administrator or Chairperson can run loan recovery.")

        try:
            results = run_recovery(actor=request.user)
        except LoanError as exc:
            messages.error(request, " ".join(_error_messages(exc)))
        else:
            total = sum((r["recovered"] for r in results), 0)
            messages.success(
                request,
                f"Checked {len(results)} overdue loan(s); recovered UGX {total:,.0f} from savings.",
            )

        return redirect("loans:register")



class OffsystemLoanCreateView(LoginRequiredMixin, View):
    """Add a loan made outside the system (Treasurer, Chairperson or Administrator)."""

    template_name = "loans/offsystem_form.html"

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated and not request.user.can_record_offsystem_loans:
            raise PermissionDenied("Only the Treasurer, Chairperson or Administrator can add loans made outside the system.")
        return super().dispatch(request, *args, **kwargs)

    def get(self, request):
        form = OffsystemLoanForm(initial={"disbursed_on": timezone.localdate()})
        return render(request, self.template_name, {"form": form})

    def post(self, request):
        form = OffsystemLoanForm(request.POST)

        if form.is_valid():
            data = form.cleaned_data
            try:
                loan = record_offsystem_loan(
                    request.user,
                    data["member"],
                    data["principal"],
                    data["period_months"],
                    data["disbursed_on"],
                    rate_percent=data["interest_rate_percent"],
                    method=data["payment_method"],
                    reference=data["reference"],
                    already_repaid=data["already_repaid"] or 0,
                    repaid_on=data["repaid_on"],
                    note=data["note"],
                )
            except LoanError as exc:
                form.add_error(None, " ".join(_error_messages(exc)))
            else:
                messages.success(
                    request,
                    f"{loan.reference} added for {loan.member.member_code}. Balance: UGX {loan.outstanding:,.0f}.",
                )
                return redirect("loans:loan", pk=loan.pk)

        return render(request, self.template_name, {"form": form})


class _DeleteView(LoginRequiredMixin, View):
    http_method_names = ["post"]

    def reason(self, request):
        form = ReversalReasonForm(request.POST)
        return form.cleaned_data["reason"] if form.is_valid() else ""


class RepaymentDeleteView(_DeleteView):
    def post(self, request, pk):
        repayment = get_object_or_404(LoanRepayment, pk=pk)
        loan_id = repayment.loan_id

        try:
            delete_repayment(repayment, request.user, self.reason(request))
        except LoanError as exc:
            messages.error(request, " ".join(_error_messages(exc)))
        else:
            messages.success(request, "Deleted. The loan balance has been recalculated.")

        return redirect("loans:loan", pk=loan_id)


class LoanDeleteView(_DeleteView):
    def post(self, request, pk):
        loan = get_object_or_404(Loan, pk=pk)

        try:
            delete_loan(loan, request.user, self.reason(request))
        except LoanError as exc:
            messages.error(request, " ".join(_error_messages(exc)))
            return redirect("loans:loan", pk=pk)

        messages.success(request, "Loan deleted. A copy is kept in the audit log.")
        return redirect("loans:register")


class ApplicationDeleteView(_DeleteView):
    def post(self, request, pk):
        application = get_object_or_404(LoanApplication, pk=pk)

        try:
            delete_application(application, request.user, self.reason(request))
        except LoanError as exc:
            messages.error(request, " ".join(_error_messages(exc)))
            return redirect("loans:detail", pk=pk)

        messages.success(request, "Application deleted. A copy is kept in the audit log.")
        return redirect("loans:applications")


class UndertakingDeleteView(_DeleteView):
    def post(self, request, pk):
        undertaking = get_object_or_404(SignedUndertaking, pk=pk)

        try:
            delete_undertaking(undertaking, request.user, self.reason(request))
        except LoanError as exc:
            messages.error(request, " ".join(_error_messages(exc)))
        else:
            messages.success(request, "Declaration deleted.")

        return redirect("loans:undertakings")
