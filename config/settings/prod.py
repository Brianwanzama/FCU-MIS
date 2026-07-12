from .base import *  # noqa: F401,F403

# ---------------------------------------------------------------------
# Production
# ---------------------------------------------------------------------

DEBUG = False

# Railway domain(s)
ALLOWED_HOSTS = [
    "fcu-mis-production.up.railway.app",
    ".up.railway.app",
    "localhost",
    "127.0.0.1",
]

# CSRF trusted origins
CSRF_TRUSTED_ORIGINS = [
    "https://fcu-mis-production.up.railway.app",
    "https://*.up.railway.app",
]

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

USE_X_FORWARDED_HOST = True

# ---------------------------------------------------------------------
# Static files
# ---------------------------------------------------------------------

STATICFILES_STORAGE = (
    "whitenoise.storage.CompressedManifestStaticFilesStorage"
)