from django import forms
from django.contrib.auth import password_validation
from django.contrib.auth.forms import AuthenticationForm

from apps.members.models import Member

from .models import User


class FCULoginForm(AuthenticationForm):
    """Cosmetic wrapper only — behaviour is Django's default, just styled for Bootstrap 5."""

    username = forms.CharField(
        label="Member ID",
        widget=forms.TextInput(attrs={"class": "form-control", "placeholder": "e.g. FCU005", "autofocus": True}),
    )
    password = forms.CharField(
        label="Password",
        widget=forms.PasswordInput(attrs={"class": "form-control"}),
    )


class AccountActivationForm(forms.Form):
    """
    Implements FR-2.2 end to end in one submission: verifies the Member ID + email
    match an existing, not-yet-activated member record, then captures the new
    password — validated all at once rather than across two requests/sessions,
    which removes an entire class of session-fixation/half-completed-signup bugs
    while still requiring exactly the two pieces of information the brief specifies.
    """

    member_code = forms.CharField(label="Member ID", max_length=10, widget=forms.TextInput(
        attrs={"class": "form-control", "placeholder": "e.g. FCU005"}))
    email = forms.EmailField(label="Email Address", widget=forms.EmailInput(attrs={"class": "form-control"}))
    password1 = forms.CharField(label="Create Password", widget=forms.PasswordInput(attrs={"class": "form-control"}))
    password2 = forms.CharField(label="Confirm Password", widget=forms.PasswordInput(attrs={"class": "form-control"}))

    def clean(self):
        cleaned = super().clean()
        member_code = cleaned.get("member_code", "").strip().upper()
        email = cleaned.get("email", "").strip()

        member = Member.objects.filter(member_code=member_code, email__iexact=email).first()
        if member is None:
            raise forms.ValidationError(
                "We couldn't find a member with that Member ID and email combination. "
                "Please check your details or contact an Administrator."
            )
        if hasattr(member, "user_account"):
            raise forms.ValidationError(
                "An account has already been activated for this Member ID. "
                "Use 'Forgot password' if you need to regain access."
            )

        password1 = cleaned.get("password1")
        password2 = cleaned.get("password2")
        if password1 and password2 and password1 != password2:
            raise forms.ValidationError("The two password fields did not match.")

        if password1:
            # Validate against Django's configured password validators using a
            # throwaway, unsaved User so username/email-similarity checks apply.
            temp_user = User(username=member_code, email=email)
            password_validation.validate_password(password1, user=temp_user)

        cleaned["member"] = member
        cleaned["member_code"] = member_code
        return cleaned
