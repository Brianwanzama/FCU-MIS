from django import forms

from .models import STATUTORY_CYCLE_MONTHS, Cycle


class CycleForm(forms.ModelForm):
    """
    Note what is NOT here: end_date, per_member_target, financial_year, status.

    All four are derived or transition-controlled. Putting them on the form would
    let an Administrator type an end date that contradicts the duration, or flip a
    status without the audit entry that services.py guarantees. The form only
    accepts the true inputs.
    """

    class Meta:
        model = Cycle
        fields = [
            "cycle_number",
            "name",
            "start_date",
            "duration_months",
            "duration_override_reason",
            "monthly_savings_amount",
            "monthly_emergency_amount",
        ]
        widgets = {
            "cycle_number": forms.NumberInput(attrs={"class": "form-control", "min": 1}),
            "name": forms.TextInput(
                attrs={"class": "form-control", "placeholder": "Leave blank to generate from the dates"}
            ),
            "start_date": forms.DateInput(attrs={"class": "form-control", "type": "date"}),
            "duration_months": forms.NumberInput(attrs={"class": "form-control", "min": 1, "max": 24}),
            "duration_override_reason": forms.Textarea(
                attrs={
                    "class": "form-control",
                    "rows": 2,
                    "placeholder": f"Only required if the duration is not {STATUTORY_CYCLE_MONTHS} months",
                }
            ),
            "monthly_savings_amount": forms.NumberInput(attrs={"class": "form-control", "step": "0.01"}),
            "monthly_emergency_amount": forms.NumberInput(attrs={"class": "form-control", "step": "0.01"}),
        }
        labels = {
            "cycle_number": "Cycle number",
            "name": "Cycle name",
            "start_date": "Start date",
            "duration_months": "Duration (months)",
            "duration_override_reason": "Reason for non-standard duration",
            "monthly_savings_amount": "Monthly savings (UGX)",
            "monthly_emergency_amount": "Monthly emergency contribution (UGX)",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["name"].required = False
        self.fields["duration_override_reason"].required = False


class CycleCloseForm(forms.Form):
    """
    Closing locks the cycle. If there are warnings, the Administrator has to tick
    the box that says they read them - which is the whole point of the screen.
    """

    confirm = forms.BooleanField(
        required=True,
        label="I understand that closing this cycle locks its financial records.",
        widget=forms.CheckboxInput(attrs={"class": "form-check-input"}),
    )


class CycleReopenForm(forms.Form):
    reason = forms.CharField(
        required=True,
        label="Reason for reopening (recorded in the audit log)",
        widget=forms.Textarea(
            attrs={
                "class": "form-control",
                "rows": 3,
                "placeholder": "e.g. Contribution for FCU007 for June 2026 was received on 4 July "
                               "and was omitted in error before the cycle was closed.",
            }
        ),
    )

    def clean_reason(self):
        reason = self.cleaned_data["reason"].strip()
        if len(reason) < 15:
            raise forms.ValidationError(
                "Please give a real reason. This is the first thing an auditor will read."
            )
        return reason
