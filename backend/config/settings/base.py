"""Responsibility: Define shared Django, database, API, and logging settings.
Implementation: Reads environment variables and the project-root .env, with process variables taking precedence; configures the database through one DATABASE_URL.
Relationships: Imported by local.py; registers accounts, crm, sales, vectors, chat, agent_tools, knowledge_graph, request logging middleware, the exception handler, and the OpenAPI generator.

Directory:
- None

Variable index:
- WORKSPACE_OWNER_ONLY: Explicitly restores personal workspaces, taking priority over anonymous laboratory access, team sharing, and experiment-batch sharing.
- LAB_OPEN_ACCESS: Explicitly enables unauthenticated, cross-account laboratory access for all business APIs; disabled by default.
- LAB_DEFAULT_USER: Ownership and audit account name for anonymous laboratory writes.
- BASE_DIR: backend software root, the base path for frontend, contracts, static files, and media.
- PROJECT_DIR: Project root containing the shared .env.
- env: Environment-variable reader with type conversion.
- SECRET_KEY: Required non-empty Django secret read from the environment; its value must never be logged.
- DEBUG: Shared debug switch, disabled by default.
- ALLOWED_HOSTS: Allowed Host list read from DJANGO_ALLOWED_HOSTS.
- CSRF_TRUSTED_ORIGINS: Allowed CSRF origin list.
- INSTALLED_APPS: Registration order for framework, API, account, CRM mail, sales, vector, chat, agent-tool, and knowledge-graph apps.
- MIDDLEWARE: Request-processing chain with logging outermost; the account lock covers SessionMiddleware session persistence.
- ROOT_URLCONF: Root URL module path.
- WSGI_APPLICATION: WSGI application import path.
- ASGI_APPLICATION: ASGI application import path.
- TEMPLATES: Template backend and context processors shared by Admin and frontend/index.html under the software root.
- DATABASES: Database connection created from required DATABASE_URL; missing or invalid configuration fails directly and never switches databases automatically.
- AUTH_USER_MODEL: accounts.User project user model.
- AUTH_PASSWORD_VALIDATORS: Django password validator set retaining only the minimum eight-character requirement, with no character-combination, common-value, or username-similarity restrictions.
- LANGUAGES: Simplified Chinese and English UI languages; LocaleMiddleware prefers the language cookie and then request headers.
- LOCALE_PATHS: Project gettext directory.
- LANGUAGE_CODE: Default UI language, zh-hans.
- TIME_ZONE: Time zone from DJANGO_TIME_ZONE, defaulting to UTC.
- USE_I18N: Enables internationalization.
- USE_TZ: Enables time-zone-aware datetime handling.
- DEFAULT_AUTO_FIELD: Default BigAutoField auto-increment primary-key type.
- STATIC_URL: Static-file URL prefix.
- STATIC_ROOT: Static-file collection directory.
- MEDIA_ROOT: Media-file directory.
- ANALYSIS_PROVIDER: Explicit rules placeholder or agent standalone-task consumer selection.
- QQ_MAIL_ENABLED: QQ inbound and outbound mail switch, disabled by default while historical data remains readable.
- GOOGLE_OAUTH_CLIENT_ID: Google Web application OAuth client identifier.
- GOOGLE_OAUTH_CLIENT_SECRET: Google Web application OAuth client secret.
- GOOGLE_OAUTH_REDIRECT_URI: Exact authorization callback URI from Google back to Django.
- SALESMATE_VAULT_KEY: Fernet key for new external-action connections; an empty value makes authorization and decryption fail explicitly.
- REST_FRAMEWORK: Session authentication, default permission, JSON renderer, schema, and exception-handler configuration.
- SPECTACULAR_SETTINGS: API metadata, enum names, and documentation configuration that defaults to administrator-only access.
- TASK_EXECUTION_MODE: Explicit local/celery execution selection, local by default, with no fallback on connection failures.
- CELERY_BROKER_URL: Server Redis message address, required in celery mode.
- CELERY_RESULT_BACKEND: Server Redis result address, required in celery mode.
- LOGGING: Console log format, handlers, and Django/SalesMate log levels.
"""


from pathlib import Path

import environ
from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parents[2]
env = environ.Env()
# The project uses only the root .env; process environment variables still take precedence.
PROJECT_DIR = BASE_DIR.parent
if (PROJECT_DIR / ".env").is_file():
    environ.Env.read_env(PROJECT_DIR / ".env", overwrite=False)

SECRET_KEY = env.str("DJANGO_SECRET_KEY")
# A blank secret is also a configuration failure; exceptions never include its value.
if not SECRET_KEY.strip():
    raise ImproperlyConfigured("DJANGO_SECRET_KEY must be set to a non-empty secret.")

DEBUG = False
WORKSPACE_OWNER_ONLY = env.bool("WORKSPACE_OWNER_ONLY", default=False)
LAB_OPEN_ACCESS = env.bool("LAB_OPEN_ACCESS", default=False) and not WORKSPACE_OWNER_ONLY
LAB_DEFAULT_USER = env.str("LAB_DEFAULT_USER", default="algorithm-lab")
ALLOWED_HOSTS = env.list("DJANGO_ALLOWED_HOSTS", default=[])
CSRF_TRUSTED_ORIGINS = env.list("DJANGO_CSRF_TRUSTED_ORIGINS", default=[])

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "drf_spectacular",
    "apps.accounts.apps.AccountsConfig",
    "apps.crm.apps.CRMConfig",
    "apps.sales.apps.SalesConfig",
    "apps.vectors",
    "apps.chat",
    "apps.agent_tools",
    "apps.knowledge_graph",
]
MIDDLEWARE = [
    # Generate request_id outermost so subsequent views, error responses, and completion logs can correlate it.
    "common.middleware.RequestLoggingMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "apps.accounts.reset_middleware.AccountDataMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.locale.LocaleMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]
ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"
TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "frontend"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ]
        },
    }
]
DATABASES = {
    "default": env.db(
        "DATABASE_URL",
    )
}
AUTH_USER_MODEL = "accounts.User"
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
]
LANGUAGE_CODE = "zh-hans"
LANGUAGES = [("zh-hans", "简体中文"), ("en", "English")]
LOCALE_PATHS = [BASE_DIR / "locale"]
TIME_ZONE = env.str("DJANGO_TIME_ZONE", default="UTC")
USE_I18N = True
USE_TZ = True
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
SALESMATE_VAULT_KEY = env.str("SALESMATE_VAULT_KEY", default="")
MEDIA_ROOT = BASE_DIR / "media"
ANALYSIS_PROVIDER = env.str("ANALYSIS_PROVIDER", default="rules")
QQ_MAIL_ENABLED = env.bool("QQ_MAIL_ENABLED", default=False)
if ANALYSIS_PROVIDER not in {"rules", "agent"}:
    raise ImproperlyConfigured("ANALYSIS_PROVIDER must be rules or agent.")

# Employee Gmail OAuth uses a Google "Web application" client. The callback
# must exactly match an authorized redirect URI in Google Cloud Console.
GOOGLE_OAUTH_CLIENT_ID = env.str("GOOGLE_OAUTH_CLIENT_ID", default="")
GOOGLE_OAUTH_CLIENT_SECRET = env.str("GOOGLE_OAUTH_CLIENT_SECRET", default="")
GOOGLE_OAUTH_REDIRECT_URI = env.str(
    "GOOGLE_OAUTH_REDIRECT_URI",
    default="http://127.0.0.1:8000/api/v1/mailboxes/gmail-callback/",
)

REST_FRAMEWORK = {
    # Laboratory authentication exposes public identity only behind its explicit switch; otherwise Session authentication resumes and health checks remain independently anonymous.
    "DEFAULT_AUTHENTICATION_CLASSES": ["common.laboratory.LaboratoryAuthentication", "rest_framework.authentication.SessionAuthentication"],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticated"],
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"],
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "EXCEPTION_HANDLER": "common.exceptions.api_exception_handler",
}
SPECTACULAR_SETTINGS = {
    "TITLE": "SalesMate Backend API",
    "DESCRIPTION": "SalesMate Gmail understanding contract, company workspace and explicit rule placeholder.",
    "VERSION": "0.2.0",
    "ENUM_NAME_OVERRIDES": {
        "IndustryEnum": ["半导体检测", "精密量测", "光学检测", "工业检测", "unknown"],
        "AnalysisStatusEnum": ["completed", "failed"],
        "LivenessStatusEnum": ["ok"],
        "ReadinessStatusEnum": ["ok", "unavailable"],
    },
    "SERVE_INCLUDE_SCHEMA": False,
    "SERVE_PERMISSIONS": ["rest_framework.permissions.AllowAny" if LAB_OPEN_ACCESS else "rest_framework.permissions.IsAdminUser"],
    "SERVE_AUTHENTICATION": ["rest_framework.authentication.SessionAuthentication"],
}
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "standard": {"format": "{asctime} {levelname} {name} {message}", "style": "{"},
    },
    "handlers": {"console": {"class": "logging.StreamHandler", "formatter": "standard"}},
    "root": {"handlers": ["console"], "level": env.str("DJANGO_LOG_LEVEL", default="INFO")},
    "loggers": {
        "django": {"handlers": ["console"], "level": "INFO", "propagate": False},
        "salesmate": {
            "handlers": ["console"],
            "level": env.str("DJANGO_LOG_LEVEL", default="INFO"),
            "propagate": False,
        },
    },
}

TASK_EXECUTION_MODE = env.str("TASK_EXECUTION_MODE", default="local")
if TASK_EXECUTION_MODE not in {"local", "celery"}:
    raise ImproperlyConfigured("TASK_EXECUTION_MODE must be local or celery")
CELERY_BROKER_URL = env.str("CELERY_BROKER_URL", default="")
CELERY_RESULT_BACKEND = env.str("CELERY_RESULT_BACKEND", default="")
if TASK_EXECUTION_MODE == "celery" and (not CELERY_BROKER_URL or not CELERY_RESULT_BACKEND):
    raise ImproperlyConfigured("Celery mode requires explicit broker and result URLs")
