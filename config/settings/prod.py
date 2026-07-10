from .base import *  # noqa: F401,F403
from .base import env

# ---------------------------------------------------------------------
# Production Settings
# ---------------------------------------------------------------------

DEBUG = False

# Railway injects this variable
ALLOWED_HOSTS = env.list("DJANGO_ALLOWED_HOSTS", default=["*"])

# If you use a custom Railway domain or your own domain later,
# set DJANGO_ALLOWED_HOSTS to:
# portal.financialcycleunit.org,.up.railway.app

CSRF_TRUSTED_ORIGINS = env.list(
    "CSRF_TRUSTED_ORIGINS",
    default=[],
)

# ---------------------------------------------------------------------
# Security
# ---------------------------------------------------------------------

SECURE_SSL_REDIRECT = True

SESSION_COOKIE_SECURE = True

CSRF_COOKIE_SECURE = True

SECURE_HSTS_SECONDS = 31536000

SECURE_HSTS_INCLUDE_SUBDOMAINS = True

SECURE_HSTS_PRELOAD = True

SECURE_CONTENT_TYPE_NOSNIFF = True

X_FRAME_OPTIONS = "DENY"

SECURE_REFERRER_POLICY = "same-origin"

SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

# ---------------------------------------------------------------------
# Static Files
# ---------------------------------------------------------------------

STATICFILES_STORAGE = (
    "whitenoise.storage.CompressedManifestStaticFilesStorage"
)

# ---------------------------------------------------------------------
# Railway
# ---------------------------------------------------------------------

USE_X_FORWARDED_HOST = True