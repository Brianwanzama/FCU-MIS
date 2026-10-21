from django import forms

from .models import Investment, InvestmentTransaction


def _style(fields):
    for field in fields.values():
        widget = field.widget
        css = "form-select" if isinstance(widget, forms.Select) else "form-control"
        widget.attrs["class"] = f"{widget.attrs.get('class', '')} {css}".strip()


class InvestmentForm(forms.ModelForm):
    class Meta:
        model = Investment
        fields = ["name", "kind", "provider", "account_reference", "registered_in_fcu_name", "opened_on", "notes"]
        widgets = {
            "opened_on": forms.DateInput(attrs={"type": "date"}),
            "notes": forms.Textarea(attrs={"rows": 2}),
        }
        labels = {"registered_in_fcu_name": "Registered in FCU's name"}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _style(self.fields)
        self.fields["registered_in_fcu_name"].widget.attrs["class"] = "form-check-input"


class TransactionForm(forms.Form):
    kind = forms.ChoiceField(choices=InvestmentTransaction.Kind.choices, label="Type")
    amount = forms.DecimalField(min_value=0, max_digits=14, decimal_places=0, label="Amount (UGX)")
    transaction_date = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}), label="Date")
    reference = forms.CharField(required=False, max_length=100)
    notes = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 2}))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _style(self.fields)


class ReasonForm(forms.Form):
    reason = forms.CharField(widget=forms.Textarea(attrs={"rows": 2}))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _style(self.fields)


class DistributionDraftForm(forms.Form):
    description = forms.CharField(max_length=200, label="Where the profit came from")
    total_amount = forms.DecimalField(min_value=1, max_digits=14, decimal_places=0, label="Total to share (UGX)")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _style(self.fields)
