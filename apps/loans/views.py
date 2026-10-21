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

from .forms import DecisionForm, LoanApplicationForm, ReviewForm, UndertakingUploadForm
from .models import LoanApplication, SignedUndertaking
from .services import (
    LoanError,
    approve_application,
    check_eligibility,
    current_undertaking,
    loan_schedule,
    reject_application,
    review_undertaking,
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

        if application.status == LoanApplication.Status.APPROVED:
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
                "schedule_is_final": application.status == LoanApplication.Status.APPROVED,
                "is_owner": user.member_id == application.member_id,
                "can_decide": open_ and user.can_approve_loans and user.member_id != application.member_id,
                "decision_form": DecisionForm(),
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
