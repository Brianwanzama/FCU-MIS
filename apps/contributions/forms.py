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