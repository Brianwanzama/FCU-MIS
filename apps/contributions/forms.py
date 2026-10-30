from django import forms

from .models import Contribution


class ContributionForm(forms.ModelForm):
    """
    Form used by the Treasurer to record a contribution.

    Fields such as cycle and recorded_by are assigned automatically
    in the view and are therefore excluded from the form.
    """

    class Meta:
        model = Contribution
        fields = [
            "member",
            "contribution_type",
            "settles_cycle",
            "amount",
            "period_month",
            "payment_date",
            "payment_method",
            "reference",
            "remarks",
        ]

        widgets = {
            "payment_date": forms.DateInput(attrs={"type": "date"}),
            "remarks": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        from apps.cycles.models import Cycle

        self.fields["settles_cycle"].queryset = Cycle.objects.filter(
            status=Cycle.Status.CLOSED
        ).order_by("-end_date")
        self.fields["settles_cycle"].label = "Settles arrears of cycle"

        for field in self.fields.values():
            widget = field.widget
            css = "form-select" if isinstance(widget, forms.Select) else "form-control"
            widget.attrs["class"] = f"{widget.attrs.get('class', '')} {css}".strip()

        # Quick amounts: pick a ready-made figure instead of typing it. The amount
        # box stays editable, so part payments and odd amounts still work.
        self.fields["amount"].widget.attrs.update(
            {"inputmode": "numeric", "step": "1000", "min": "1"}
        )


# Ready-made amounts for the quick-amount lists (monthly deposit steps of
# UGX 100,000; emergency steps of UGX 5,000).
DEPOSIT_QUICK_AMOUNTS = [100_000 * n for n in range(1, 7)]
EMERGENCY_QUICK_AMOUNTS = [5_000 * n for n in range(1, 7)]


def _style(fields):
    for field in fields.values():
        widget = field.widget
        css = "form-select" if isinstance(widget, forms.Select) else "form-control"
        widget.attrs["class"] = f"{widget.attrs.get('class', '')} {css}".strip()


class ContributionEditForm(forms.ModelForm):
    """
    Correct an existing contribution. Who paid, what kind of payment it is and
    which cycle it belongs to cannot be changed: reverse the payment and record
    it again instead. A reason is always required.
    """

    reason = forms.CharField(
        widget=forms.Textarea(attrs={"rows": 2}),
        help_text="Why is this being changed? Kept permanently in the audit history.",
    )

    class Meta:
        model = Contribution
        fields = [
            "amount",
            "period_month",
            "payment_date",
            "payment_method",
            "reference",
            "remarks",
        ]
        widgets = {
            "payment_date": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
            "remarks": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _style(self.fields)


class DeleteForm(forms.Form):
    """Asks for the reason before a record is deleted."""

    reason = forms.CharField(
        widget=forms.Textarea(attrs={"rows": 2}),
        help_text="Why is this being deleted? Kept permanently in the audit history.",
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _style(self.fields)


class ReversalRequestForm(forms.Form):
    reason = forms.CharField(
        widget=forms.Textarea(attrs={"rows": 3}),
        help_text="Why should this payment be reversed?",
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _style(self.fields)


class ReversalDecisionForm(forms.Form):
    note = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"rows": 2}),
        help_text="Required when rejecting.",
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _style(self.fields)
