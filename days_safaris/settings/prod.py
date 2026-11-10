from .base import *  # noqa: F401,F403
from .base import BASE_DIR, SECRET_KEY, env

DEBUG = env.bool("DEBUG", default=False)
if SECRET_KEY.startswith("dev-insecure"):
    raise RuntimeError("Set a real SECRET_KEY in .env for production.")

STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_SSL_REDIRECT = env.bool("SECURE_SSL_REDIRECT", default=True)
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SECURE_HSTS_SECONDS = env.int("SECURE_HSTS_SECONDS", default=60 * 60 * 24 * 365)
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"
X_FRAME_OPTIONS = "DENY"
WHITENOISE_MAX_AGE = 60 * 60 * 24 * 30
JOBS_BACKEND = env("JOBS_BACKEND", default="cron")
