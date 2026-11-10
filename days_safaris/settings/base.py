"""Base settings shared by dev, test and prod. Everything environment-specific comes from .env."""
import json
from pathlib import Path

import environ

BASE_DIR = Path(__file__).resolve().parent.parent.parent

env = environ.Env()
# Load .env from an absolute path; real process environment variables take precedence.
environ.Env.read_env(str(BASE_DIR / ".env"), overwrite=False)

SECRET_KEY = env("SECRET_KEY", default="dev-insecure-change-me-days-safaris")
DEBUG = env.bool("DEBUG", default=False)
ALLOWED_HOSTS = env.list("ALLOWED_HOSTS", default=["localhost", "127.0.0.1"])
CSRF_TRUSTED_ORIGINS = env.list("CSRF_TRUSTED_ORIGINS", default=[])
SITE_URL = env("SITE_URL", default="http://localhost:8000").rstrip("/")

# Optional pure-Python MySQL driver, must happen before database settings are built.
if env("DB_MYSQL_DRIVER", default="") == "pymysql":
    import pymysql  # type: ignore

    pymysql.install_as_MySQLdb()

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.humanize",
    # third party
    "django_htmx",
    "django_filters",
    "rest_framework",
    "rest_framework.authtoken",
    "simple_history",
    # local
    "apps.core",
    "apps.accounts",
    "apps.people",
    "apps.fleet",
    "apps.catalog",
    "apps.contacts",
    "apps.leads",
    "apps.frontdesk",
    "apps.sales",
    "apps.trips",
    "apps.suppliers",
    "apps.finance",
    "apps.marketing",
    "apps.system",
    "apps.compliance",
    "apps.schedule",
    "apps.hr",
    "apps.dealdesk",
    "apps.assets",
    "apps.ict",
    "apps.reports",
    "apps.coach",
    "apps.insights",
    "apps.tasks",
    "apps.mailbox",
    "apps.manuals",
    "apps.notifications",
    "apps.integrations",
    "apps.pwa",
    "apps.api",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "django_htmx.middleware.HtmxMiddleware",
    "simple_history.middleware.HistoryRequestMiddleware",
    "apps.core.middleware.CurrentUserMiddleware",
    "apps.accounts.middleware.TwoFactorSetupMiddleware",
]

ROOT_URLCONF = "days_safaris.urls"
WSGI_APPLICATION = "days_safaris.wsgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "apps.core.context_processors.app_shell",
            ],
            "builtins": ["apps.core.templatetags.ds"],
        },
    }
]

# ---------------------------------------------------------------- database (section 1.3)
DATABASES = {"default": env.db("DATABASE_URL", default=f"sqlite:///{BASE_DIR / 'db.sqlite3'}")}


def _finish_db(cfg: dict) -> dict:
    cfg["CONN_MAX_AGE"] = env.int("DB_CONN_MAX_AGE", default=60)
    cfg["CONN_HEALTH_CHECKS"] = env.bool("DB_CONN_HEALTH_CHECKS", default=True)
    opts = cfg.setdefault("OPTIONS", {})
    raw = env("DB_OPTIONS", default="")
    if raw:
        opts.update(json.loads(raw))
    if cfg["ENGINE"].endswith("sqlite3"):
        cfg["CONN_MAX_AGE"] = 0  # persistent connections do not help SQLite
    return cfg


_finish_db(DATABASES["default"])
if env("DATABASE_REPLICA_URL", default=""):
    DATABASES["replica"] = _finish_db(env.db("DATABASE_REPLICA_URL"))
    DATABASES["replica"]["TEST"] = {"MIRROR": "default"}
    DATABASE_ROUTERS = ["apps.core.db_router.ReplicaRouter"]

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
AUTH_USER_MODEL = "accounts.User"
AUTHENTICATION_BACKENDS = ["django.contrib.auth.backends.ModelBackend"]
LOGIN_URL = "accounts:login"
LOGIN_REDIRECT_URL = "core:home"
LOGOUT_REDIRECT_URL = "accounts:login"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 8}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# ---------------------------------------------------------------- locale
LANGUAGE_CODE = "en-gb"
TIME_ZONE = "Africa/Nairobi"
USE_I18N = True
USE_TZ = True
DATE_FORMAT = "j M Y"          # 28 Apr 2025
DATETIME_FORMAT = "j M Y, g:i A"
TIME_FORMAT = "g:i A"          # 2:45 PM
SHORT_DATE_FORMAT = "j M Y"
USE_THOUSAND_SEPARATOR = True
NUMBER_GROUPING = 3
BASE_CURRENCY = "KES"

# ---------------------------------------------------------------- static & media
STATIC_URL = "/static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = Path(env("STATIC_ROOT", default=str(BASE_DIR / "staticfiles")))
MEDIA_URL = "/media/"
MEDIA_ROOT = Path(env("MEDIA_ROOT", default=str(BASE_DIR / "media")))
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}
LOG_DIR = Path(env("LOG_DIR", default=str(BASE_DIR / "logs")))

# ---------------------------------------------------------------- sessions (installed app stays signed in)
SESSION_COOKIE_AGE = env.int("SESSION_COOKIE_AGE", default=60 * 60 * 24 * 30)  # "Remember me": 30 days
SESSION_EXPIRE_AT_BROWSER_CLOSE = False
SESSION_COOKIE_NAME = "dsa_sessionid"   # own names: browsers share cookies across ports, so another Django app on
CSRF_COOKIE_NAME = "dsa_csrftoken"      # the same host (or another port) must not overwrite or invalidate ours
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"

# ---------------------------------------------------------------- cache & email
CACHES = {"default": env.cache("CACHE_URL", default="locmemcache://days-safaris")}
EMAIL_CONFIG = env.email("EMAIL_URL", default="consolemail://")
vars().update(EMAIL_CONFIG)
DEFAULT_FROM_EMAIL = env("DEFAULT_FROM_EMAIL", default="Pocket of Paradise Travel Company <no-reply@pocketofparadise.co.ke>")
SERVER_EMAIL = DEFAULT_FROM_EMAIL
ADMINS = [tuple(a.split(":", 1)) for a in env.list("ADMINS", default=[]) if ":" in a]

# ---------------------------------------------------------------- app settings
COMPANY_NAME = "Pocket of Paradise Travel Company"
COMPANY_TAGLINE = "Travel Company"
JOBS_BACKEND = env("JOBS_BACKEND", default="sync")  # sync | cron | celery
JOBS_MAX_ATTEMPTS = 3
JOBS_RUN_TIME_LIMIT = env.int("JOBS_RUN_TIME_LIMIT", default=240)  # seconds per run_jobs invocation
# A departure inside this many days of leaving, still missing resources, is flagged for attention.
TRIPS_STUCK_DAYS = env.int("TRIPS_STUCK_DAYS", default=5)
PWA_VERSION = env("PWA_VERSION", default="4")
PWA_THEME_COLOR = "#1F6F6B"
PWA_BACKGROUND_COLOR = "#F9EDD7"   # the logo cream, matches the splash screens
FIELD_ENCRYPTION_KEY = env("FIELD_ENCRYPTION_KEY", default="")
LOGIN_RATE_LIMIT = env.int("LOGIN_RATE_LIMIT", default=5)  # attempts per 15 min per IP+email
VAPID_PUBLIC_KEY = env("VAPID_PUBLIC_KEY", default="")
VAPID_PRIVATE_KEY = env("VAPID_PRIVATE_KEY", default="")
VAPID_ADMIN_EMAIL = env("VAPID_ADMIN_EMAIL", default="admin@pocketofparadise.co.ke")
# Shared secret for the website / OTA enquiry webhook (X-Webhook-Secret header). Empty disables it.
# Two-factor authentication (TOTP). Off by default so demos and development stay frictionless.
# When on, the roles below must set it up before using the app; everyone else may opt in from My Space → Security.
TWO_FACTOR_ENABLED = env.bool("TWO_FACTOR_ENABLED", default=False)
TWO_FACTOR_REQUIRED_ROLES = env.list("TWO_FACTOR_REQUIRED_ROLES", default=["Admin", "Finance Officer", "Director", "COO"])
AI_MODEL = env("AI_MODEL", default="claude-sonnet-5-5")  # model used by the assistant and the WhatsApp bot
WEBHOOK_SECRET = env("WEBHOOK_SECRET", default="")
INTEGRATIONS_MODE = env("INTEGRATIONS_MODE", default="mock")  # mock | live
FX_PROVIDER_API_KEY = env("FX_PROVIDER_API_KEY", default="")

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework_simplejwt.authentication.JWTAuthentication",
        "rest_framework.authentication.TokenAuthentication",
        "rest_framework.authentication.SessionAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticated"],
    "DEFAULT_FILTER_BACKENDS": ["django_filters.rest_framework.DjangoFilterBackend"],
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "PAGE_SIZE": 25,
}

# ---------------------------------------------------------------- logging
LOG_DIR.mkdir(parents=True, exist_ok=True)
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {"std": {"format": "%(asctime)s %(levelname)s %(name)s: %(message)s"}},
    "handlers": {
        "console": {"class": "logging.StreamHandler", "formatter": "std"},
        "django_file": {
            "class": "logging.handlers.RotatingFileHandler",
            "filename": str(LOG_DIR / "django.log"),
            "maxBytes": 5 * 1024 * 1024,
            "backupCount": 5,
            "formatter": "std",
        },
        "jobs_file": {
            "class": "logging.handlers.RotatingFileHandler",
            "filename": str(LOG_DIR / "jobs.log"),
            "maxBytes": 5 * 1024 * 1024,
            "backupCount": 5,
            "formatter": "std",
        },
        "mail_admins": {"class": "django.utils.log.AdminEmailHandler", "level": "ERROR"},
    },
    "loggers": {
        "django": {"handlers": ["console", "django_file"], "level": "INFO"},
        "django.request": {"handlers": ["django_file", "mail_admins"], "level": "ERROR", "propagate": False},
        "apps": {"handlers": ["console", "django_file"], "level": "INFO"},
        "jobs": {"handlers": ["console", "jobs_file"], "level": "INFO", "propagate": False},
    },
}

# Backend used for agents' own mailboxes (SMTP with the agent's credentials). Tests switch it to locmem.
AGENT_MAIL_BACKEND = env("AGENT_MAIL_BACKEND", default="django.core.mail.backends.smtp.EmailBackend")

BLOG_SOURCE_URL = env("BLOG_SOURCE_URL", default="")  # company website blog read by Media -> Blogs
