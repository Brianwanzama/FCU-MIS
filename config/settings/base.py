"""
Base settings shared by every environment (dev / staging / production).
Environment-specific values are read from environment variables via django-environ,
never hardcoded here. See dev.py and prod.py for the two concrete environments,
and .env.example at the project root for the variables this expects.
"""

from pathlib import Path
import environ

# BASE_DIR = the fcu-mis/ project root (three levels up from this file:
# config/settings/base.py -> config/settings -> config -> project root)
BASE_DIR = Path(__file__).resolve().parent.parent.parent

env = environ.Env()
environ.Env.read_env(BASE_DIR / ".env")

SECRET_KEY = env("DJANGO_SECRET_KEY", default="dev-only-insecure-key-override-in-.env")

# ---------------------------------------------------------------------------
# Applications
# ---------------------------------------------------------------------------
DJANGO_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.humanize",
]

THIRD_PARTY_APPS = [
    # widget_tweaks etc. can be added here as later phases need form styling helpers
]

# One local app per SRS domain module (FR sections trace 1:1 to these apps).
# Several are still skeletons (models/views arrive in their own roadmap phase)
# so the project structure is stable and future phases only add to it.
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

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "apps.auditlog.middleware.CurrentRequestMiddleware",  # exposes request (actor/IP) to model-layer audit signals
]

ROOT_URLCONF = "config.urls"

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
    },
]

WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

# ---------------------------------------------------------------------------
# Database — PostgreSQL only, connection string comes from the environment.
# ---------------------------------------------------------------------------
DATABASES = {
    "default": env.db(
        "DATABASE_URL",
        default="postgres://postgres:devpassword@localhost:5432/fcu_mis",
    )
}

# ---------------------------------------------------------------------------
# Password validation
# ---------------------------------------------------------------------------
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 10}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LOGIN_URL = "accounts:login"
LOGIN_REDIRECT_URL = "core:dashboard"
LOGOUT_REDIRECT_URL = "accounts:login"

# ---------------------------------------------------------------------------
# Internationalization
# ---------------------------------------------------------------------------
LANGUAGE_CODE = "en-us"
TIME_ZONE = "Africa/Kampala"
USE_I18N = True
USE_TZ = True

# ---------------------------------------------------------------------------
# Static & media files
# ---------------------------------------------------------------------------
STATIC_URL = "static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"

MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "media"  # document_template PDFs, generated PDF statements, etc.

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# ---------------------------------------------------------------------------
# FCU business constants (defaults only — the authoritative, editable values
# live in the sysconfig.SystemSetting table per FR-14 / A9; these are used
# purely as fallback defaults the first time the app boots before an admin
# has configured anything).
# ---------------------------------------------------------------------------
FCU_DEFAULT_MONTHLY_SAVINGS = env.int("FCU_DEFAULT_MONTHLY_SAVINGS", default=100_000)
FCU_DEFAULT_MONTHLY_EMERGENCY = env.int("FCU_DEFAULT_MONTHLY_EMERGENCY", default=5_000)
FCU_DEFAULT_LOAN_INTEREST_PERCENT = env.float("FCU_DEFAULT_LOAN_INTEREST_PERCENT", default=5.0)
FCU_DEFAULT_MAX_LOAN_AMOUNT = env.int("FCU_DEFAULT_MAX_LOAN_AMOUNT", default=2_000_000)
FCU_DEFAULT_MAX_LOAN_PERCENT_OF_CONTRIBUTIONS = env.float(
    "FCU_DEFAULT_MAX_LOAN_PERCENT_OF_CONTRIBUTIONS", default=75.0
)
FCU_MAX_LOAN_REPAYMENT_MONTHS = 3  # Manual §4.5(b) — hard cap, not admin-configurable

# ---------------------------------------------------------------------------
# Email (password reset, account activation notices)
# ---------------------------------------------------------------------------
EMAIL_BACKEND = env("EMAIL_BACKEND", default="django.core.mail.backends.console.EmailBackend")
EMAIL_HOST = env("EMAIL_HOST", default="")
EMAIL_PORT = env.int("EMAIL_PORT", default=587)
EMAIL_HOST_USER = env("EMAIL_HOST_USER", default="")
EMAIL_HOST_PASSWORD = env("EMAIL_HOST_PASSWORD", default="")
EMAIL_USE_TLS = env.bool("EMAIL_USE_TLS", default=True)
DEFAULT_FROM_EMAIL = env("DEFAULT_FROM_EMAIL", default="no-reply@fcu-mis.local")
