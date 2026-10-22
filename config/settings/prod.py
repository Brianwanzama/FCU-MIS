import hashlib
import hmac

from django.core.exceptions import ImproperlyConfigured

from .base import *  # noqa: F401,F403
from .base import BASE_DIR, SECRET_KEY, env  # noqa: F401

# ---------------------------------------------------------------------
# Production
# ---------------------------------------------------------------------

DEBUG = False

# The sample values in .env.example are public, so they must never be used as the key.
PLACEHOLDER_KEY_PREFIXES = ("unsafe", "change-me", "django-insecure", "changeme", "secret")

SECRET_KEY_IS_DERIVED = False

if SECRET_KEY.lower().startswith(PLACEHOLDER_KEY_PREFIXES) or len(SECRET_KEY) < 50:
    # No usable DJANGO_SECRET_KEY was set. Rather than refuse to start (or, worse, run with a
    # public sample key), derive a key from the database address, which contains the database
    # password and is known only to this deployment. It is stable between deploys, so sessions
    # survive restarts. It is a safety net: set a real DJANGO_SECRET_KEY when you can.
    _database_url = env("DATABASE_URL", default="")

    if not _database_url or "devpassword" in _database_url:
        raise ImproperlyConfigured(
            "DJANGO_SECRET_KEY is missing, too short or still a sample value, and there is no "
            "DATABASE_URL to derive a key from. Generate a key with: "
            "python -c \"import secrets; print(secrets.token_urlsafe(60))\""
        )

    SECRET_KEY = hmac.new(
        b"fcu-mis-derived-secret-key-v1", _database_url.encode(), hashlib.sha256
    ).hexdigest()
    SECRET_KEY_IS_DERIVED = True

# Railway domains, plus any custom domain listed in DJANGO_ALLOWED_HOSTS.
ALLOWED_HOSTS = [
    ".up.railway.app",
    "healthcheck.railway.app",
    "localhost",
    "127.0.0.1",
] + env.list("DJANGO_ALLOWED_HOSTS", default=[])

# Origins allowed to POST forms: Railway domains plus DJANGO_CSRF_TRUSTED_ORIGINS
# (full URLs, e.g. https://mis.example.org).
CSRF_TRUSTED_ORIGINS = [
    "https://*.up.railway.app",
] + env.list("DJANGO_CSRF_TRUSTED_ORIGINS", default=[])

# ---------------------------------------------------------------------
# Security
# ---------------------------------------------------------------------

SECURE_SSL_REDIRECT = True

# The platform's health check calls over plain HTTP inside the network.
SECURE_REDIRECT_EXEMPT = [r"^healthz/$"]

SESSION_COOKIE_SECURE = True

CSRF_COOKIE_SECURE = True

SECURE_HSTS_SECONDS = 31536000

SECURE_HSTS_INCLUDE_SUBDOMAINS = True

SECURE_HSTS_PRELOAD = True

SECURE_CONTENT_TYPE_NOSNIFF = True

X_FRAME_OPTIONS = "DENY"

SECURE_REFERRER_POLICY = "same-origin"

SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

USE_X_FORWARDED_HOST = True

# Keep connections open between requests instead of reconnecting every time.
DATABASES["default"]["CONN_MAX_AGE"] = 60  # noqa: F405

# ---------------------------------------------------------------------
# Static files (Django 6 reads STORAGES; the old STATICFILES_STORAGE is ignored)
# ---------------------------------------------------------------------

STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}

# ---------------------------------------------------------------------
# Logging: everything to the console, where Railway collects it.
# ---------------------------------------------------------------------

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {"plain": {"format": "%(asctime)s %(levelname)s %(name)s %(message)s"}},
    "handlers": {"console": {"class": "logging.StreamHandler", "formatter": "plain"}},
    "root": {"handlers": ["console"], "level": env("DJANGO_LOG_LEVEL", default="INFO")},
}
