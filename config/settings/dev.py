from .base import *  # noqa: F401,F403
from .base import env

DEBUG = env.bool("DJANGO_DEBUG", default=True)
ALLOWED_HOSTS = env.list("DJANGO_ALLOWED_HOSTS", default=["localhost", "127.0.0.1"])

# Verbose errors, no HTTPS-only cookie flags, so local development isn't blocked.
SESSION_COOKIE_SECURE = False
CSRF_COOKIE_SECURE = False
