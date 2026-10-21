from django import forms

from .models import LoanApplication


class _Styled:
    def style(self):
        for field in self.fields.values():
            widget = field.widget
            if isinstance(widget, forms.ClearableFileInput):
                css = "form-control"
            elif isinstance(widget, forms.Select):
                css = "form-select"
            else:
                css = "form-control"
            widget.attrs["class"] = f"{widget.attrs.get('class', '')} {css}".strip()


class LoanApplicationForm(forms.Form, _Styled):
    amount = forms.DecimalField(
        label="Amount requested (UGX)", min_value=1, max_digits=12, decimal_places=0,
        help_text="Whole shillings. Must be within your loan limit.",
    )
    period_months = forms.TypedChoiceField(
        label="Repayment period",
        choices=[(1, "1 month"), (2, "2 months"), (3, "3 months")],
        coerce=int,
        help_text="Maximum three months, and it must finish within the current cycle.",
    )
    application_form = forms.FileField(
        label="Filled and signed Loan Application Form",
        help_text="Scan or photograph the completed, signed form. PDF, JPG or PNG.",
    )
    undertaking_file = forms.FileField(
        label="Signed Member Declaration and Commitment Agreement",
        required=False,
        help_text="Needed once. Skip this if you have already uploaded yours.",
    )

    def __init__(self, *args, member_has_undertaking=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.member_has_undertaking = member_has_undertaking

        if member_has_undertaking:
            self.fields["undertaking_file"].label = "Replace your Declaration (optional)"

        self.style()

    def clean(self):
        cleaned = super().clean()

        # A preview needs only the terms, so files are checked on real submission.
        return cleaned


class UndertakingUploadForm(forms.Form, _Styled):
    file = forms.FileField(
        label="Signed Member Declaration and Commitment Agreement",
        help_text="Scan or photograph the signed form. PDF, JPG or PNG.",
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.style()


class DecisionForm(forms.Form, _Styled):
    amount_approved = forms.DecimalField(
        required=False, min_value=1, max_digits=12, decimal_places=0,
        label="Amount approved (UGX)", help_text="Leave blank to approve the amount requested.",
    )
    note = forms.CharField(
        required=False, widget=forms.Textarea(attrs={"rows": 2}),
        label="Comments / conditions", help_text="Required when rejecting. The member will see it.",
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.style()


class ReviewForm(forms.Form, _Styled):
    note = forms.CharField(
        required=False, widget=forms.Textarea(attrs={"rows": 2}), label="Note",
        help_text="Required when rejecting.",
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.style()


class DisbursementForm(forms.Form, _Styled):
    payment_method = forms.ChoiceField(
        label="Paid out by",
        choices=[("CASH", "Cash"), ("BANK", "Bank Transfer"), ("MOBILE_MONEY", "Mobile Money")],
    )
    reference = forms.CharField(
        required=False, max_length=100, label="Reference",
        help_text="Mobile-money ID, bank slip or receipt number.",
    )
    disbursed_on = forms.DateField(
        required=False, label="Date paid out", widget=forms.DateInput(attrs={"type": "date"}),
        help_text="Leave blank for today. Interest and the schedule run from this date.",
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.style()


class RepaymentForm(forms.Form, _Styled):
    amount = forms.DecimalField(min_value=1, max_digits=12, decimal_places=0, label="Amount (UGX)")
    payment_date = forms.DateField(label="Date paid", widget=forms.DateInput(attrs={"type": "date"}))
    payment_method = forms.ChoiceField(
        label="Method",
        choices=[("CASH", "Cash"), ("BANK", "Bank Transfer"), ("MOBILE_MONEY", "Mobile Money")],
    )
    reference = forms.CharField(required=False, max_length=100, label="Reference")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.style()


class ReversalReasonForm(forms.Form, _Styled):
    reason = forms.CharField(widget=forms.Textarea(attrs={"rows": 2}), label="Reason")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.style()
