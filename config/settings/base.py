"""
Base settings shared by every environment (development / production).

Environment-specific values are read from environment variables using
django-environ.

Local development:
    Reads values from .env

Production (Railway):
    Reads values from Railway environment variables.
"""

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
    "apps.loans",
    "apps.repayments",
    "apps.unittrust",
    "apps.emergencyfund",
    "apps.expenses",
    "apps.orgsettings",
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

STATICFILES_STORAGE = (
    "whitenoise.storage.CompressedManifestStaticFilesStorage"
)

# ---------------------------------------------------------------------
# Media
# ---------------------------------------------------------------------

MEDIA_URL = "/media/"

MEDIA_ROOT = BASE_DIR / "media"

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

FCU_DEFAULT_MAX_LOAN_AMOUNT = env.int(
    "FCU_DEFAULT_MAX_LOAN_AMOUNT",
    default=2000000,
)

FCU_DEFAULT_MAX_LOAN_PERCENT_OF_CONTRIBUTIONS = env.float(
    "FCU_DEFAULT_MAX_LOAN_PERCENT_OF_CONTRIBUTIONS",
    default=75.0,
)

FCU_MAX_LOAN_REPAYMENT_MONTHS = 3

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