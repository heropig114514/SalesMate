"""Responsibility: Define configuration overrides used only for local development.
Implementation: Enables DEBUG and automatic local development-session login and allows anonymous schema access; business endpoints still validate the Session.
Relationships: Loaded by default through manage.py, ASGI, and WSGI entry points; never used for production deployment.

Directory:
- None

Variable index:
- DEBUG: Enables debugging for local development.
- LOCAL_DEBUG_AUTO_LOGIN: Whether browsers from loopback addresses automatically receive a development session.
- LOCAL_DEBUG_USER: Existing ordinary development username used for the automatic session.
- SPECTACULAR_SETTINGS: Inherits base schema settings and enables local anonymous documentation access.
"""


from .base import *  # noqa: F403

DEBUG = True
LOCAL_DEBUG_AUTO_LOGIN = env.bool("LOCAL_DEBUG_AUTO_LOGIN", default=True)  # noqa: F405
LOCAL_DEBUG_USER = env.str("LOCAL_DEBUG_USER", default="demo")  # noqa: F405
SPECTACULAR_SETTINGS = {
    **SPECTACULAR_SETTINGS,  # noqa: F405
    "SERVE_PERMISSIONS": ["rest_framework.permissions.AllowAny"],
    "SERVE_AUTHENTICATION": [],
}
