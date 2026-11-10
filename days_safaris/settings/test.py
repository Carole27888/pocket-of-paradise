from .base import *  # noqa: F401,F403

DEBUG = False
JOBS_BACKEND = "sync"
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
INTEGRATIONS_MODE = "mock"
INTEGRATIONS_OFFLINE = True   # the "Test key" buttons make no network call in the test suite
AGENT_MAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
