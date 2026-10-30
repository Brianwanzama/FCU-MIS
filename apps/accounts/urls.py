from django.contrib.auth import views as auth_views
from django.urls import path, reverse_lazy

from . import views
from .forms import FCULoginForm

app_name = "accounts"

urlpatterns = [
    path(
        "login/",
        auth_views.LoginView.as_view(template_name="accounts/login.html", authentication_form=FCULoginForm),
        name="login",
    ),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("activate/", views.ActivateAccountView.as_view(), name="activate"),
    # --- password reset (FR-2.4) ---
    path(
        "password-reset/",
        views.FCUPasswordResetView.as_view(
            template_name="accounts/password_reset_form.html",
            email_template_name="accounts/password_reset_email.txt",
            subject_template_name="accounts/password_reset_subject.txt",
            success_url=reverse_lazy("accounts:password_reset_done"),
        ),
        name="password_reset",
    ),
    path(
        "password-reset/done/",
        auth_views.PasswordResetDoneView.as_view(template_name="accounts/password_reset_done.html"),
        name="password_reset_done",
    ),
    path(
        "reset/<uidb64>/<token>/",
        auth_views.PasswordResetConfirmView.as_view(
            template_name="accounts/password_reset_confirm.html",
            success_url=reverse_lazy("accounts:password_reset_complete"),
        ),
        name="password_reset_confirm",
    ),
    path(
        "reset/done/",
        auth_views.PasswordResetCompleteView.as_view(template_name="accounts/password_reset_complete.html"),
        name="password_reset_complete",
    ),
    # --- admin-only user/role management (FR-14, FR-3.2) ---
    path("users/", views.UserManagementView.as_view(), name="user_management"),
    path("users/<int:pk>/role/", views.ChangeUserRoleView.as_view(), name="change_role"),
    path("users/<int:pk>/temporary-password/", views.SetTemporaryPasswordView.as_view(), name="temporary_password"),
    path("users/<int:pk>/require-password-change/", views.RequirePasswordChangeView.as_view(), name="require_password_change"),
    path("users/require-password-change/", views.RequirePasswordChangeView.as_view(), name="require_password_change_all"),
    path("users/<int:pk>/sign-in-as/", views.impersonate_start, name="impersonate_start"),
    path("sign-in-as/stop/", views.impersonate_stop, name="impersonate_stop"),
    path("password/change/", views.ChangePasswordView.as_view(), name="password_change"),
]
