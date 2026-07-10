from .base import *  # noqa: F401,F403
from .base import env

# ---------------------------------------------------------------------
# Development Settings
# ---------------------------------------------------------------------

DEBUG = env.bool("DJANGO_DEBUG", default=True)

ALLOWED_HOSTS = env.list(
    "DJANGO_ALLOWED_HOSTS",
    default=["localhost", "127.0.0.1"],
)

# ---------------------------------------------------------------------
# Development Security
# ---------------------------------------------------------------------

SESSION_COOKIE_SECURE = False

CSRF_COOKIE_SECURE = False

SECURE_SSL_REDIRECT = False

SECURE_HSTS_SECONDS = 0

EMAIL_BACKEND = env(
    "EMAIL_BACKEND",
    default="django.core.mail.backends.console.EmailBackend",
)