"""
Base settings shared by every environment (development / production).

Environment-specific values are read from environment variables using
django-environ.

Local development:
    Reads values from .env

Production (Railway):
    Reads values from Railway environment variables.
"""

import os
from pathlib import Path

import environ

# ---------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------

BASE_DIR = Path(__file__).resolve().parent.parent.parent

# ---------------------------------------------------------------------
# Environment
# ---------------------------------------------------------------------

env = environ.Env()

# Read .env only if it exists (local development)
env_file = BASE_DIR / ".env"
if env_file.exists():
    environ.Env.read_env(env_file)

# ---------------------------------------------------------------------
# Core
# ---------------------------------------------------------------------

SECRET_KEY = env(
    "DJANGO_SECRET_KEY",
    default="unsafe-development-key-change-this",
)

# ---------------------------------------------------------------------
# Applications
# ---------------------------------------------------------------------

DJANGO_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.humanize",
]

THIRD_PARTY_APPS = []

LOCAL_APPS = [
    "apps.core",
    "apps.accounts",
    "apps.members",
    "apps.documents",
    "apps.auditlog",
    "apps.cycles",
    "apps.contributions",

    # Future modules (not yet implemented)
    "apps.loans",
    # "apps.repayments",
    "apps.unittrust",
    # "apps.emergencyfund",
    # "apps.expenses",
    # "apps.orgsettings",
    "apps.reports",
]

INSTALLED_APPS = DJANGO_APPS + THIRD_PARTY_APPS + LOCAL_APPS

AUTH_USER_MODEL = "accounts.User"

# ---------------------------------------------------------------------
# Middleware
# ---------------------------------------------------------------------

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",

    # WhiteNoise
    "whitenoise.middleware.WhiteNoiseMiddleware",

    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",

    "apps.auditlog.middleware.CurrentRequestMiddleware",
]

ROOT_URLCONF = "config.urls"

# ---------------------------------------------------------------------
# Templates
# ---------------------------------------------------------------------

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            # `ugx` is available in every template without {% load %}.
            "builtins": ["apps.core.templatetags.currency"],
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "apps.core.context_processors.fcu_branding",
            ],
        },
    }
]

WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

# ---------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------

DATABASES = {
    "default": env.db(
        "DATABASE_URL",
        default="postgres://postgres:devpassword@localhost:5432/fcu_mis",
    )
}

# ---------------------------------------------------------------------
# Password Validation
# ---------------------------------------------------------------------

AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"
    },
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
        "OPTIONS": {"min_length": 10},
    },
    {
        "NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"
    },
    {
        "NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"
    },
]

LOGIN_URL = "accounts:login"
LOGIN_REDIRECT_URL = "core:dashboard"
LOGOUT_REDIRECT_URL = "accounts:login"

# ---------------------------------------------------------------------
# Internationalisation
# ---------------------------------------------------------------------

LANGUAGE_CODE = "en-us"

TIME_ZONE = "Africa/Kampala"

USE_I18N = True
USE_TZ = True

# ---------------------------------------------------------------------
# Static Files
# ---------------------------------------------------------------------

STATIC_URL = "/static/"

STATICFILES_DIRS = [
    BASE_DIR / "static",
]

STATIC_ROOT = BASE_DIR / "staticfiles"

# ---------------------------------------------------------------------
# Media
# ---------------------------------------------------------------------

MEDIA_URL = "/media/"

# On Railway, point this at a persistent volume, or uploaded document templates are
# lost on every deploy.
def _volume_or(subfolder, fallback):
    """Use a writable /data volume (Railway) when one is mounted, else the fallback folder."""

    if os.path.isdir("/data") and os.access("/data", os.W_OK):
        return f"/data/{subfolder}"

    return str(fallback)


MEDIA_ROOT = env.str("FCU_MEDIA_ROOT", default="") or _volume_or("media", BASE_DIR / "media")

# ---------------------------------------------------------------------
# Default PK
# ---------------------------------------------------------------------

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# ---------------------------------------------------------------------
# FCU Defaults
# ---------------------------------------------------------------------

FCU_DEFAULT_MONTHLY_SAVINGS = env.int(
    "FCU_DEFAULT_MONTHLY_SAVINGS",
    default=100000,
)

FCU_DEFAULT_MONTHLY_EMERGENCY = env.int(
    "FCU_DEFAULT_MONTHLY_EMERGENCY",
    default=5000,
)

FCU_DEFAULT_LOAN_INTEREST_PERCENT = env.float(
    "FCU_DEFAULT_LOAN_INTEREST_PERCENT",
    default=5.0,
)

# A member may borrow up to this percentage of accumulated deposits. FCU has decided
# on 100% (a loan can never exceed previous contributions). Manual v3.0 4.4 and the
# official form still say 120%: amend them to match, or set this to 120.
FCU_LOAN_LIMIT_PERCENT = env.float("FCU_LOAN_LIMIT_PERCENT", default=100.0)

# Manual 4.5(b): maximum repayment period.
FCU_MAX_LOAN_REPAYMENT_MONTHS = 3

# Uploaded forms (signed declarations, loan applications) are private: they live
# outside MEDIA_ROOT, are never served as static files, and are only handed out by
# views that check who is asking. On Railway point this at a persistent volume.
PRIVATE_MEDIA_ROOT = env.str("FCU_PRIVATE_MEDIA_ROOT", default="") or _volume_or(
    "private", BASE_DIR / "private_media"
)

FCU_UPLOAD_MAX_MB = env.int("FCU_UPLOAD_MAX_MB", default=5)

# Automatic recovery of defaulted loans from a member's savings (Manual 4.6) runs
# only when this is switched on, i.e. once FCU confirms the recovery policy. Until
# then the nightly job only reports what it WOULD recover. Record the authority
# (e.g. the General Meeting resolution) in the reference, which is written to the
# audit log with every recovery.
FCU_LOAN_AUTO_RECOVERY_ENABLED = env.bool("FCU_LOAN_AUTO_RECOVERY_ENABLED", default=False)
FCU_LOAN_RECOVERY_POLICY_REF = env.str(
    "FCU_LOAN_RECOVERY_POLICY_REF", default="FCU Manual v3.0, section 4.6"
)

# ---------------------------------------------------------------------
# Email
# ---------------------------------------------------------------------

EMAIL_BACKEND = env(
    "EMAIL_BACKEND",
    default="django.core.mail.backends.console.EmailBackend",
)

EMAIL_HOST = env("EMAIL_HOST", default="")

EMAIL_PORT = env.int("EMAIL_PORT", default=587)

EMAIL_HOST_USER = env("EMAIL_HOST_USER", default="")

EMAIL_HOST_PASSWORD = env("EMAIL_HOST_PASSWORD", default="")

EMAIL_USE_TLS = env.bool("EMAIL_USE_TLS", default=True)

DEFAULT_FROM_EMAIL = env(
    "DEFAULT_FROM_EMAIL",
    default="no-reply@fcu-mis.local",
)