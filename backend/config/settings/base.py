"""职责：定义共用 Django、数据库、API 和日志配置。
实现：读取环境变量及项目根目录 .env，进程变量优先；数据库通过单一 DATABASE_URL 配置。
关联：供 local.py 导入；注册 accounts、crm、sales、vectors、chat、agent_tools、请求日志中间件、错误处理器及 OpenAPI 生成器。

目录：
- 无

变量索引：
- WORKSPACE_OWNER_ONLY：显式恢复个人空间，优先禁止匿名实验访问、团队与实验批次共享。
- LAB_OPEN_ACCESS：显式启用所有业务 API 免登录和跨账号实验访问，默认关闭。
- LAB_DEFAULT_USER：匿名实验写入的归属及审计账号名。
- BASE_DIR：软件根目录 backend，作为前端、契约、静态文件和媒体路径基准。
- PROJECT_DIR：项目根目录，共享 .env 所在位置。
- env：具有类型转换能力的环境变量读取器。
- SECRET_KEY：必需且非空的 Django 密钥，从环境读取，禁止记录其值。
- DEBUG：共用配置中的调试开关，默认关闭。
- ALLOWED_HOSTS：允许的 Host 列表，从 DJANGO_ALLOWED_HOSTS 读取。
- CSRF_TRUSTED_ORIGINS：允许的 CSRF 来源列表。
- INSTALLED_APPS：框架、API、账号、crm 邮件、sales 业务、vectors 向量、chat 聊天与 agent_tools 业务工具应用的注册顺序。
- MIDDLEWARE：请求处理链，日志位于最外层；账号锁覆盖 SessionMiddleware 的会话保存阶段。
- ROOT_URLCONF：根路由模块路径。
- WSGI_APPLICATION：WSGI 应用导入路径。
- ASGI_APPLICATION：ASGI 应用导入路径。
- TEMPLATES：Admin 与软件根目录内 frontend/index.html 共用的模板后端及上下文处理器。
- DATABASES：由必填 DATABASE_URL 生成的数据库连接；缺失或非法配置直接失败，不自动切换数据库。
- AUTH_USER_MODEL：项目用户模型 accounts.User。
- AUTH_PASSWORD_VALIDATORS：仅保留最少 8 字符长度要求，不限制字符组合、常见值或用户名相似性；Django 密码校验器集合。
- LANGUAGES：界面支持简体中文和英文；LocaleMiddleware 优先使用语言 cookie，再匹配请求头。
- LOCALE_PATHS：项目 gettext 目录位置。
- LANGUAGE_CODE：默认界面语言 zh-hans。
- TIME_ZONE：DJANGO_TIME_ZONE 指定的时区，缺省 UTC。
- USE_I18N：启用国际化。
- USE_TZ：启用时区感知的日期时间处理。
- DEFAULT_AUTO_FIELD：默认自增主键类型 BigAutoField。
- STATIC_URL：静态文件 URL 前缀。
- STATIC_ROOT：静态文件收集目录。
- MEDIA_ROOT：媒体文件目录。
- ANALYSIS_PROVIDER：显式选择 rules 占位或 agent 独立任务消费者。
- QQ_MAIL_ENABLED：QQ 收信及发信开关，默认关闭，历史数据仍可读。
- GOOGLE_OAUTH_CLIENT_ID：Google Web application OAuth 客户端标识。
- GOOGLE_OAUTH_CLIENT_SECRET：Google Web application OAuth 客户端密钥。
- GOOGLE_OAUTH_REDIRECT_URI：Google 回到 Django 的精确授权回调地址。
- SALESMATE_VAULT_KEY：新外部动作连接的 Fernet 密钥，空值时授权与解密明确失败。
- REST_FRAMEWORK：会话认证、默认权限、JSON 渲染、Schema 与异常处理器配置。
- SPECTACULAR_SETTINGS：API 元数据、枚举名称及默认仅管理员访问的文档配置。
- TASK_EXECUTION_MODE：显式 local/celery 执行方式，本地默认 local，无连接失败回退。
- CELERY_BROKER_URL：服务器 Redis 消息地址，celery 模式必填。
- CELERY_RESULT_BACKEND：服务器 Redis 结果地址，celery 模式必填。
- LOGGING：控制台日志格式、处理器和 Django/SalesMate 日志级别。
"""


from pathlib import Path

import environ
from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parents[2]
env = environ.Env()
# 项目只使用根目录一个 .env；进程环境仍拥有更高优先级。
PROJECT_DIR = BASE_DIR.parent
if (PROJECT_DIR / ".env").is_file():
    environ.Env.read_env(PROJECT_DIR / ".env", overwrite=False)

SECRET_KEY = env.str("DJANGO_SECRET_KEY")
# 空白密钥也视为配置失败；异常中不包含读取到的密钥值。
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
]
MIDDLEWARE = [
    # 最外层先生成 request_id，使后续视图、错误响应和完成日志能够关联。
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
    # 实验认证仅在显式开关下提供公开身份；关闭时恢复 Session，健康检查独立匿名。
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
