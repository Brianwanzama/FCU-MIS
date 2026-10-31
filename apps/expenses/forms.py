from django import forms

from .models import Expense


def _style(fields):
    for field in fields.values():
        widget = field.widget
        css = "form-select" if isinstance(widget, forms.Select) else "form-control"
        widget.attrs["class"] = f"{widget.attrs.get('class', '')} {css}".strip()


class ExpenseForm(forms.ModelForm):
    class Meta:
        model = Expense
        fields = ["category", "description", "amount", "expense_date", "paid_to", "payment_method", "reference", "notes"]
        widgets = {
            "expense_date": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
            "notes": forms.Textarea(attrs={"rows": 2}),
            "amount": forms.NumberInput(attrs={"step": "500", "min": "1", "inputmode": "numeric"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _style(self.fields)


class ExpenseEditForm(ExpenseForm):
    reason = forms.CharField(
        widget=forms.Textarea(attrs={"rows": 2}),
        help_text="Why is this being changed? Kept permanently in the audit history.",
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _style(self.fields)


class ReasonForm(forms.Form):
    reason = forms.CharField(widget=forms.Textarea(attrs={"rows": 2}))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _style(self.fields)
